import json
from decimal import Decimal

import pytest

from spoon_local.spoon.sse_parser import SSEParser, SSEParseError, SSEFrame
from spoon_local.spoon.event_models import normalize_donation, InvalidDonation, canonical_amount


def wire(amount=1, event_id='1000-0', **overrides):
    payload = {'user': {'id': 'PRIVATE-USER', 'nickname': '한글 이름'}, 'amount': amount, 'message': 'PRIVATE-MESSAGE', 'time': '2026-10-14T00:00:00Z', **overrides}
    return SSEFrame('donation', json.dumps(payload, ensure_ascii=False), event_id)


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_incremental_utf8_bom_comments_and_line_endings(ending):
    parser = SSEParser()
    text = '\ufeff:heartbeat' + ending + ending + 'id:1000-0' + ending + 'event:donation' + ending + 'data:한글' + ending + 'data:두 줄' + ending + ending
    output = []
    for byte in text.encode('utf-8'):
        output.extend(parser.feed(bytes([byte])))
    assert len(output) == 1
    assert output[0].data == '한글\n두 줄'
    assert output[0].event == 'donation'
    assert output[0].explicit_id == '1000-0'
    assert not parser.finish()


def test_explicit_id_never_inherits_and_empty_id_resets():
    parser = SSEParser()
    frames = parser.feed(b'id:1-0\ndata:first\n\ndata:second\n\nid:\ndata:third\n\nid:bad\x00id\ndata:fourth\n\n')
    assert [frame.explicit_id for frame in frames] == ['1-0', None, None, None]
    assert parser.last_event_id == ''
    assert all(frame.event == 'message' for frame in frames)


def test_retry_hint_unknown_fields_and_empty_data():
    frames = SSEParser().feed(b'unknown:value\nretry:2500\ndata:\n\n')
    assert frames[0].data == '' and frames[0].retry_hint == 2500
    assert not SSEParser().feed(b':heartbeat\n\n')


def test_incomplete_eof_never_dispatches():
    parser = SSEParser()
    assert parser.feed(b'event:donation\ndata:{"amount":1}') == []
    assert parser.finish()


@pytest.mark.parametrize('data', [b'\xff', b'\xe3'])
def test_invalid_utf8(data):
    with pytest.raises(SSEParseError):
        parser = SSEParser()
        parser.feed(data)
        parser.finish()


def test_frame_bound():
    with pytest.raises(SSEParseError):
        SSEParser(max_frame_bytes=10).feed(b'data:' + b'a' * 20)


@pytest.mark.parametrize('value,expected', [('0','0'), ('1.2500','1.25'), ('0.000001','0.000001'), ('123456789012345678.125','123456789012345678.125')])
def test_decimal_is_exact(value, expected):
    frame = wire()
    frame = SSEFrame('donation', frame.data.replace('"amount": 1', '"amount": '+value), frame.explicit_id)
    item = normalize_donation(frame)
    assert item.amount_spoons == expected
    assert not hasattr(item, 'nickname') and not hasattr(item, 'user_id') and not hasattr(item, 'message')
    assert 'PRIVATE' not in repr(item)


@pytest.mark.parametrize('payload', ['{', '[]', '{}', '{"amount":NaN}', '{"amount":Infinity}'])
def test_invalid_json_and_required_fields(payload):
    with pytest.raises(InvalidDonation): normalize_donation(SSEFrame('donation', payload))


@pytest.mark.parametrize('field,value', [('amount', True), ('amount', -1), ('amount', '1'), ('amount', None), ('time', 'not-a-date'), ('time', '2026-10-14T00:00:00'), ('message', 1), ('user', None)])
def test_payload_field_validation(field, value):
    with pytest.raises(InvalidDonation): normalize_donation(wire(**{field:value}))


@pytest.mark.parametrize('field', ['user','amount','message','time'])
def test_missing_fields(field):
    payload = json.loads(wire().data)
    payload.pop(field)
    with pytest.raises(InvalidDonation): normalize_donation(SSEFrame('donation', json.dumps(payload)))


def test_timestamp_precision_and_unknown_fields():
    result = normalize_donation(wire(time='2026-10-14T09:00:00.1+09:00', unknown='ignored'))
    assert result.occurred_at_utc == '2026-10-14T00:00:00.100000+00:00'


def test_deep_json_and_numeric_bound():
    payload = json.loads(wire().data)
    nested = []
    for _ in range(12): nested = [nested]
    payload['unknown'] = nested
    with pytest.raises(InvalidDonation): normalize_donation(SSEFrame('donation', json.dumps(payload)))
    with pytest.raises(InvalidDonation): canonical_amount(Decimal('1e100000'))
