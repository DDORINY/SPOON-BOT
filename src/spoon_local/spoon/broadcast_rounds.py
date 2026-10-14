"""Explicit migration and account-scoped round queries; no automatic backfill."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from spoon_local.spoon.donation_repository import RealDonationRepository
from spoon_local.spoon.event_models import utc_now

MIGRATION = [
    '''CREATE TABLE broadcast_rounds (
        round_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id TEXT UNIQUE NOT NULL, account_key TEXT NOT NULL,
        provider_live_id TEXT, started_at_utc TEXT,
        first_observed_at_utc TEXT NOT NULL,last_observed_at_utc TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('observed_live','ended','unknown')),
        ended_at_utc TEXT,end_observed_at_utc TEXT,
        created_at_utc TEXT NOT NULL,updated_at_utc TEXT NOT NULL,
        UNIQUE(account_key,provider_live_id))''',
    'ALTER TABLE donations ADD COLUMN account_key TEXT',
    'ALTER TABLE donations ADD COLUMN broadcast_round_id TEXT REFERENCES broadcast_rounds(round_id)',
    "ALTER TABLE donations ADD COLUMN attribution_basis TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(attribution_basis IN ('unconfirmed','observed_context'))",
    'ALTER TABLE event_conflicts ADD COLUMN account_key TEXT',
    'ALTER TABLE collection_gaps ADD COLUMN account_key TEXT',
    'CREATE INDEX donations_rounds ON donations(account_key,broadcast_round_id,received_sequence)',
]


def version(repository):
    if not isinstance(repository,RealDonationRepository):raise ValueError('Real ledger required')
    if not repository.path.exists():return 0
    with repository.path.open('rb') as handle: header=handle.read(100)
    if header[:16]!=b'SQLite format 3\x00' or int.from_bytes(header[68:72],'big')!=repository.application_id:
        raise ValueError('Dedicated real ledger required')
    with sqlite3.connect(repository.path.as_uri()+'?mode=ro',uri=True) as db:
        return db.execute('PRAGMA user_version').fetchone()[0]


def migrate_rounds(repository):
    """Only explicit callers; operational invocation requires separate approval."""
    if version(repository) not in {1,2}:raise ValueError('Unsupported schema')
    with repository.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('PRAGMA user_version').fetchone()[0]==1:
            for statement in MIGRATION:db.execute(statement)
            db.execute('PRAGMA user_version=2')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key violation')


def timestamp(value):
    if value is None:return None
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def account(value):
    if not isinstance(value,str) or not value or len(value)>128:raise ValueError('Account namespace required')
    return value


class RoundRepository:
    def __init__(self,repository):self.repository=repository

    def ready(self):return version(self.repository)==2

    def observe(self,account_key,live_id=None,started_at=None,observed_at=None):
        account(account_key)
        if live_id is not None and (isinstance(live_id,bool) or not isinstance(live_id,int) or live_id<0):raise ValueError('Invalid official live ID')
        started=timestamp(started_at);now=timestamp(observed_at) if observed_at else utc_now()
        with self.repository.connection() as db:
            if live_id is not None:
                db.execute("UPDATE broadcast_rounds SET state='unknown',updated_at_utc=? WHERE account_key=? AND provider_live_id<>? AND state='observed_live'",(now,account_key,str(live_id)))
            existing=db.execute('SELECT round_id FROM broadcast_rounds WHERE account_key=? AND provider_live_id=?',(account_key,str(live_id))).fetchone() if live_id is not None else None
            if existing:
                db.execute("UPDATE broadcast_rounds SET last_observed_at_utc=?,updated_at_utc=?,state='observed_live' WHERE round_id=?",(now,now,existing[0]))
                return existing[0]
            key=str(uuid.uuid4())
            db.execute('INSERT INTO broadcast_rounds(round_id,account_key,provider_live_id,started_at_utc,first_observed_at_utc,last_observed_at_utc,state,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)',(key,account_key,str(live_id) if live_id is not None else None,started,now,now,'observed_live' if live_id is not None else 'unknown',now,now))
            return key

    def uncertain(self,account_key):
        with self.repository.connection() as db:
            db.execute("UPDATE broadcast_rounds SET state='unknown',updated_at_utc=? WHERE account_key=? AND state='observed_live'",(utc_now(),account(account_key)))

    def end_observed(self,account_key,round_id):
        with self.repository.connection() as db:
            db.execute("UPDATE broadcast_rounds SET state='ended',end_observed_at_utc=?,updated_at_utc=? WHERE account_key=? AND round_id=?",(utc_now(),utc_now(),account(account_key),round_id))

    def rounds(self,account_key,limit=20,before=None):
        if not 1<=limit<=100 or (before is not None and before<1):raise ValueError('Invalid page')
        with self.repository.connection() as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT * FROM broadcast_rounds WHERE account_key=? AND (? IS NULL OR round_sequence<?) ORDER BY round_sequence DESC LIMIT ?',(account(account_key),before,before,limit)).fetchall()
        items=[{key:row[key] for key in row.keys() if key!='account_key'} for row in rows]
        return {'items':items,'next_before':items[-1]['round_sequence'] if items else None}

    def detail(self,account_key,round_id):
        with self.repository.connection() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM broadcast_rounds WHERE account_key=? AND round_id=?',(account(account_key),round_id)).fetchone()
        return {key:row[key] for key in row.keys() if key!='account_key'} if row else None

    def donations(self,account_key,round_id=None,unconfirmed=False,limit=20,before=None,date_from=None,date_to=None):
        account(account_key)
        if not 1<=limit<=100 or (before is not None and before<1):raise ValueError('Invalid page')
        start,end=timestamp(date_from),timestamp(date_to)
        if start and end and (start>end or (datetime.fromisoformat(end.replace('Z','+00:00'))-datetime.fromisoformat(start.replace('Z','+00:00'))).days>366):raise ValueError('Invalid date range')
        if unconfirmed and round_id is not None:raise ValueError('Conflicting filter')
        with self.repository.connection() as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('''SELECT received_sequence,record_id,amount_spoons_text AS amount_spoons,occurred_at_utc,received_at_utc,broadcast_round_id,attribution_basis FROM donations
                WHERE account_key=? AND (? IS NULL OR broadcast_round_id=?) AND (?=0 OR broadcast_round_id IS NULL)
                AND (? IS NULL OR received_sequence<?) AND (? IS NULL OR occurred_at_utc>=?) AND (? IS NULL OR occurred_at_utc<?)
                ORDER BY received_sequence DESC LIMIT ?''',(account_key,round_id,round_id,int(unconfirmed),before,before,start,start,end,end,limit)).fetchall()
        items=[dict(row) for row in rows]
        return {'items':items,'next_before':items[-1]['received_sequence'] if items else None,'coverage':'observed_only'}
