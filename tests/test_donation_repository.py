from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from spoon_local.spoon.donation_repository import DonationRepository, MockPathError
from spoon_local.spoon.event_models import Donation


@pytest.fixture
def repository(tmp_path):
    repo = DonationRepository(tmp_path / 'mock' / 'mock-sse.sqlite3')
    repo.initialize()
    return repo


def item(amount='1.25', event_id='1000-0'):
    return Donation(amount, '2026-10-14T00:00:00.000000+00:00', '2026-10-14T00:00:01.000000+00:00', event_id)


def test_duplicates_conflicts_idless_and_decimal_total(repository):
    session = repository.session('mock-live-1')
    assert repository.append(item(), session) == 'inserted'
    assert repository.append(item(), session) == 'duplicate'
    assert repository.append(item('2'), session) == 'conflict'
    assert repository.append(item('0.1', None), session) == 'inserted'
    assert repository.append(item('0.1', None), session) == 'inserted'
    data = repository.recent()
    assert len(data['items']) == 3
    assert data['observed_total_spoons'] == '1.45'
    assert data['issues'] == {'conflicts': 1, 'gaps': 0, 'idless': 2}
    assert [row['received_sequence'] for row in data['items']] == [3, 2, 1]


def test_reopen_recovers_same_session_and_dedup(repository):
    session = repository.session('mock-live-1')
    repository.append(item(), session)
    reopened = DonationRepository(repository.path)
    reopened.initialize()
    assert reopened.session('mock-live-1') == session
    assert reopened.session('mock-live-2') != session
    assert reopened.append(item(), reopened.session('mock-live-2')) == 'duplicate'
    assert len(reopened.recent()['items']) == 1


def test_query_pagination_and_sql_binding(repository):
    session = repository.session(None)
    for index in range(5): repository.append(item(str(index), f'id-{index}'), session)
    first = repository.recent(2)
    second = repository.recent(2, first['next_before_sequence'])
    assert [row['received_sequence'] for row in first['items']] == [5,4]
    assert [row['received_sequence'] for row in second['items']] == [3,2]
    assert repository.append(item('1', "id'; DROP TABLE donations; --"), session) == 'inserted'
    assert len(repository.recent()['items']) == 6


def test_gap_recovery_keeps_unknown_completeness(repository):
    repository.gap('unexpected_eof')
    repository.recover_gaps()
    with repository.connection() as db:
        row = db.execute('SELECT reason_code,recovered_at_utc,completeness FROM collection_gaps').fetchone()
    assert row[0] == 'unexpected_eof' and row[1] and row[2] == 'unknown'


def test_no_donor_fields_or_raw_payload_in_schema(repository):
    with repository.connection() as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(donations)')}
    assert not columns & {'nickname','message','user_id','donor_key','raw_payload','access_token','refresh_token'}
    assert repository.path.stat().st_mode & 0o777 == 0o600


def test_wrong_filename_never_opens_operational_db(tmp_path, monkeypatch):
    operational = tmp_path / 'spoon_local.sqlite3'
    operational.write_bytes(b'OPERATIONAL-SENTINEL')
    def denied(*args, **kwargs): raise AssertionError('SQLite opened')
    monkeypatch.setattr(sqlite3, 'connect', denied)
    with pytest.raises(MockPathError): DonationRepository(operational)
    assert operational.read_bytes() == b'OPERATIONAL-SENTINEL'
    with pytest.raises(MockPathError): DonationRepository(Path('mock-sse.sqlite3'))


def test_unmarked_database_refused_without_modification(tmp_path):
    path = tmp_path / 'mock-sse.sqlite3'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE existing(value TEXT)')
    before = path.read_bytes()
    with pytest.raises(MockPathError): DonationRepository(path).initialize()
    assert path.read_bytes() == before


def test_symlinks_and_hardlinks_refused(tmp_path):
    target = tmp_path / 'operational.sqlite3'
    target.write_bytes(b'SENTINEL')
    link = tmp_path / 'mock-sse.sqlite3'
    link.symlink_to(target)
    with pytest.raises(MockPathError): DonationRepository(link)
    assert target.read_bytes() == b'SENTINEL'


def test_database_lock_rolls_back_then_recovers(repository):
    session = repository.session('mock-live')
    blocker = sqlite3.connect(repository.path)
    try:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(sqlite3.OperationalError): repository.append(item(), session)
    finally:
        blocker.rollback()
        blocker.close()
    assert repository.append(item(), session) == 'inserted'
    assert len(repository.recent()['items']) == 1


def test_mock_db_cannot_be_served_as_static_asset():
    from spoon_local.main import STATIC_DIR
    with pytest.raises(MockPathError): DonationRepository(STATIC_DIR/'mock-sse.sqlite3')
