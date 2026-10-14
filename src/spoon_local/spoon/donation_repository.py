"""Dedicated, marked mode-isolated SQLite ledgers; never migrate an unmarked database."""

from contextlib import contextmanager
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import uuid

from spoon_local.spoon.event_models import Donation, utc_now

APPLICATION_ID = 0x534D5345
SCHEMA = '''
CREATE TABLE mock_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE broadcast_sessions (
 session_key TEXT PRIMARY KEY, provider_live_id TEXT,
 source_mode TEXT NOT NULL DEFAULT 'mock' CHECK(source_mode='mock'),
 first_seen_at_utc TEXT NOT NULL
);
CREATE TABLE donations (
 received_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 record_id TEXT NOT NULL UNIQUE,
 session_key TEXT NOT NULL REFERENCES broadcast_sessions(session_key),
 source_mode TEXT NOT NULL DEFAULT 'mock' CHECK(source_mode='mock'),
 integration_key TEXT NOT NULL DEFAULT 'mock-single-dj' CHECK(integration_key='mock-single-dj'),
 stream_event_id TEXT, amount_spoons_text TEXT NOT NULL,
 occurred_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL,
 dedup_quality TEXT NOT NULL CHECK(dedup_quality IN ('event_id','unavailable')),
 fingerprint TEXT NOT NULL
);
CREATE UNIQUE INDEX donation_id ON donations(integration_key,stream_event_id) WHERE stream_event_id IS NOT NULL;
CREATE TABLE event_conflicts (
 conflict_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 existing_record_id TEXT NOT NULL REFERENCES donations(record_id),
 incoming_fingerprint TEXT NOT NULL, detected_at_utc TEXT NOT NULL
);
CREATE TABLE collection_gaps (
 gap_id TEXT PRIMARY KEY, reason_code TEXT NOT NULL,
 detected_at_utc TEXT NOT NULL, recovered_at_utc TEXT,
 completeness TEXT NOT NULL DEFAULT 'unknown' CHECK(completeness='unknown')
);
'''


class MockPathError(ValueError):
    def __init__(self):
        super().__init__("Use an absolute, dedicated SSE database path")


class DonationRepository:
    filename = "mock-sse.sqlite3"
    application_id = APPLICATION_ID
    schema = SCHEMA
    mode = "mock"
    metadata_table = "mock_metadata"
    supported_versions = {1}

    def __init__(self, path: Path):
        if not path.is_absolute() or path.name != self.filename:
            raise MockPathError()
        static_dir = Path(__file__).resolve().parents[1] / 'web' / 'static'
        if path.is_relative_to(static_dir):
            raise MockPathError()
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise MockPathError()
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise MockPathError()
        self.path = path

    def initialize(self):
        fresh = False
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            fresh = True
        else:
            # Verify marker before opening an existing file with SQLite.
            with self.path.open('rb') as handle:
                header = handle.read(100)
            if header[:16] != b'SQLite format 3\x00' or int.from_bytes(header[68:72], 'big') != self.application_id:
                raise MockPathError()
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=0.1)
        try:
            if fresh:
                connection.executescript('BEGIN;\n' + self.schema)
                connection.execute(f'INSERT INTO {self.metadata_table} VALUES (?,?)', ('fingerprint_salt', os.urandom(32).hex()))
                connection.execute(f'PRAGMA application_id={self.application_id}')
                connection.execute('PRAGMA user_version=1')
                connection.commit()
            elif connection.execute('PRAGMA user_version').fetchone()[0] not in self.supported_versions:
                raise MockPathError()
        finally:
            connection.close()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=0.1)
        try:
            if connection.execute('PRAGMA application_id').fetchone()[0] != self.application_id:
                raise MockPathError()
            connection.execute('PRAGMA foreign_keys=ON')
            with connection:
                yield connection
        finally:
            connection.close()

    def session(self, live_id: str | None) -> str:
        with self.connection() as db:
            existing = db.execute('SELECT session_key FROM broadcast_sessions WHERE provider_live_id=? ORDER BY rowid DESC LIMIT 1', (live_id,)).fetchone() if live_id is not None else None
            if existing:
                return existing[0]
            key = str(uuid.uuid4())
            db.execute('INSERT INTO broadcast_sessions(session_key,provider_live_id,first_seen_at_utc) VALUES (?,?,?)', (key, live_id, utc_now()))
            return key

    def append(self, donation: Donation, session: str, account_key=None, round_id=None) -> str:
        with self.connection() as db:
            if account_key is not None:
                from spoon_local.spoon.broadcast_rounds import account
                account(account_key)
                if db.execute('PRAGMA user_version').fetchone()[0] != 2:
                    raise ValueError('Explicit round migration required')
                if round_id is not None and not db.execute("SELECT 1 FROM broadcast_rounds WHERE round_id=? AND account_key=? AND provider_live_id IS NOT NULL AND state='observed_live'", (round_id, account_key)).fetchone():
                    raise ValueError('Confirmed same-account round required')
            elif round_id is not None:
                raise ValueError('Account namespace required')
            salt_row = db.execute(f"SELECT value FROM {self.metadata_table} WHERE key='fingerprint_salt'").fetchone()
            if salt_row is None:
                raise MockPathError()
            salt = bytes.fromhex(salt_row[0])
            if len(salt) != 32:
                raise MockPathError()
            core_values = [donation.event_id, donation.amount_spoons, donation.occurred_at_utc]
            if account_key is not None:
                core_values.append(account_key)
            core = json.dumps(core_values, separators=(',', ':')).encode()
            fingerprint = hmac.new(salt, core, hashlib.sha256).hexdigest()
            existing = db.execute('SELECT record_id,fingerprint FROM donations WHERE stream_event_id=?', (donation.event_id,)).fetchone() if donation.event_id is not None else None
            if existing:
                if hmac.compare_digest(existing[1], fingerprint):
                    return 'duplicate'
                db.execute('INSERT INTO event_conflicts(existing_record_id,incoming_fingerprint,detected_at_utc) VALUES (?,?,?)', (existing[0], fingerprint, utc_now()))
                if account_key is not None:
                    db.execute('UPDATE event_conflicts SET account_key=? WHERE conflict_sequence=(SELECT MAX(conflict_sequence) FROM event_conflicts)',(account_key,))
                return 'conflict'
            db.execute('INSERT INTO donations(record_id,session_key,stream_event_id,amount_spoons_text,occurred_at_utc,received_at_utc,dedup_quality,fingerprint) VALUES (?,?,?,?,?,?,?,?)', (str(uuid.uuid4()), session, donation.event_id, donation.amount_spoons, donation.occurred_at_utc, donation.received_at_utc, 'event_id' if donation.event_id is not None else 'unavailable', fingerprint))
            if account_key is not None:
                db.execute('UPDATE donations SET account_key=?,broadcast_round_id=?,attribution_basis=? WHERE record_id=(SELECT record_id FROM donations ORDER BY received_sequence DESC LIMIT 1)',(account_key,round_id,'observed_context' if round_id else 'unconfirmed'))
            return 'inserted'

    def observe_round(self, account_key, live_id, started_at, observed_at=None):
        from spoon_local.spoon.broadcast_rounds import RoundRepository
        return RoundRepository(self).observe(account_key, live_id, started_at, observed_at)

    def uncertain_rounds(self, account_key):
        from spoon_local.spoon.broadcast_rounds import RoundRepository
        return RoundRepository(self).uncertain(account_key)

    def gap(self, reason: str, detected_at: str | None = None, account_key=None) -> str:
        key = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('INSERT INTO collection_gaps(gap_id,reason_code,detected_at_utc) VALUES (?,?,?)', (key, reason, detected_at or utc_now()))
            if account_key is not None:
                db.execute('UPDATE collection_gaps SET account_key=? WHERE gap_id=?',(account_key,key))
        return key

    def recover_gaps(self):
        with self.connection() as db:
            db.execute('UPDATE collection_gaps SET recovered_at_utc=? WHERE recovered_at_utc IS NULL', (utc_now(),))

    def recent(self, limit: int = 20, before_sequence: int | None = None) -> dict:
        with self.connection() as db:
            rows = db.execute('SELECT received_sequence,record_id,amount_spoons_text,occurred_at_utc,received_at_utc,dedup_quality FROM donations WHERE (? IS NULL OR received_sequence<?) ORDER BY received_sequence DESC LIMIT ?', (before_sequence, before_sequence, limit)).fetchall()
            amounts = db.execute('SELECT amount_spoons_text FROM donations').fetchall()
            conflicts = db.execute('SELECT COUNT(*) FROM event_conflicts').fetchone()[0]
            gaps = db.execute('SELECT COUNT(*) FROM collection_gaps').fetchone()[0]
            idless = db.execute("SELECT COUNT(*) FROM donations WHERE dedup_quality='unavailable'").fetchone()[0]
        items = [dict(zip(('received_sequence', 'record_id', 'amount_spoons', 'occurred_at_utc', 'received_at_utc', 'dedup_quality'), row)) for row in rows]
        # Decimal addition must not silently round a long sum with the default context.
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 80
            total = sum((Decimal(row[0]) for row in amounts), Decimal(0))
        return {'source_mode': self.mode, 'items': items, 'observed_total_spoons': format(total, 'f'), 'coverage': 'observed_only', 'issues': {'conflicts': conflicts, 'gaps': gaps, 'idless': idless}, 'next_before_sequence': rows[-1][0] if rows else None}


class RealDonationRepository(DonationRepository):
    """Dedicated real ledger; a different marker prevents mock/operational DB reuse."""
    filename = "real-sse.sqlite3"
    application_id = 0x53525345
    mode = "real"
    metadata_table = "real_metadata"
    supported_versions = {1, 2}
    schema = SCHEMA.replace("mock_metadata", "real_metadata").replace("'mock'", "'real'").replace("mock-single-dj", "real-single-dj")
