"""Read-only local ledger search; no account or broadcast attribution."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import sqlite3
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo('Asia/Seoul')


def boundaries(period, start=None, end=None, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(SEOUL).date()
    if period not in {'all', 'today', '7d', '30d', 'custom'}:
        raise ValueError('Invalid period')
    if period == 'custom':
        first, last = date.fromisoformat(start or ''), date.fromisoformat(end or '')
        if first > last: raise ValueError('Invalid dates')
    elif start is not None or end is not None:
        raise ValueError('Unexpected dates')
    elif period == 'all': return None, None
    else:
        last = today
        first = today - timedelta(days={'today': 0, '7d': 6, '30d': 29}[period])
    def utc(day):
        return datetime.combine(day, datetime.min.time(), SEOUL).astimezone(timezone.utc).isoformat(timespec='microseconds')
    return utc(first), utc(last + timedelta(days=1))


class DecimalSum:
    def __init__(self): self.total = Decimal(0)
    def step(self, value):
        with localcontext() as context:
            context.prec = 80
            self.total += Decimal(value)
    def finalize(self): return format(self.total, 'f')


def search(repository, *, period='all', start=None, end=None, page=1, page_size=12, snapshot=None, now=None):
    if isinstance(page,bool) or not 1 <= page <= 10000 or page_size not in {12,24}:
        raise ValueError('Invalid page')
    lower, upper = boundaries(period,start,end,now)
    today_lower,today_upper = boundaries('today',now=now)
    empty = {'items':[], 'total':0, 'page':page, 'page_size':page_size, 'pages':0, 'has_previous':False, 'has_next':False, 'snapshot':0, 'statistics':{'event_count':0,'amount_spoons':'0','today_event_count':0,'latest_at_utc':None}, 'timezone':'Asia/Seoul','time_basis':'occurred_at_else_received','coverage':'observed_stored_only','unit':'spoon'}
    if not repository.path.exists(): return empty
    with closing(sqlite3.connect(repository.path.as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
        if db.execute('PRAGMA application_id').fetchone()[0]!=repository.application_id or db.execute('PRAGMA user_version').fetchone()[0] not in repository.supported_versions:
            raise ValueError('Invalid ledger')
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')  # rows, counts and totals share the same read snapshot
        db.create_aggregate('decimal_sum',1,DecimalSum)
        maximum=db.execute('SELECT COALESCE(MAX(received_sequence),0) FROM donations').fetchone()[0]
        anchor=maximum if snapshot is None else snapshot
        if isinstance(anchor,bool) or not 0<=anchor<=maximum:raise ValueError('Invalid snapshot')
        basis="COALESCE(NULLIF(occurred_at_utc,''),received_at_utc)"
        where=f'received_sequence<=? AND (? IS NULL OR {basis}>=?) AND (? IS NULL OR {basis}<?)'
        params=(anchor,lower,lower,upper,upper)
        total,amount,latest=db.execute(f'SELECT COUNT(*),decimal_sum(amount_spoons_text),MAX({basis}) FROM donations WHERE {where}',params).fetchone()
        today_count=db.execute(f'SELECT COUNT(*) FROM donations WHERE received_sequence<=? AND {basis}>=? AND {basis}<?',(anchor,today_lower,today_upper)).fetchone()[0]
        db.row_factory=sqlite3.Row
        rows=db.execute(f'SELECT record_id,amount_spoons_text AS amount_spoons,occurred_at_utc,received_at_utc,{basis} AS display_at_utc FROM donations WHERE {where} ORDER BY {basis} DESC,received_sequence DESC LIMIT ? OFFSET ?',(*params,page_size,(page-1)*page_size)).fetchall()
        items=[{**dict(row),'time_basis':'occurred' if row['occurred_at_utc'] else 'received'} for row in rows]
    pages=(total+page_size-1)//page_size
    return {**empty,'items':items,'total':total,'pages':pages,'has_previous':page>1 and total>0,'has_next':page<pages,'snapshot':anchor,'statistics':{'event_count':total,'amount_spoons':amount or '0','today_event_count':today_count,'latest_at_utc':latest}}
