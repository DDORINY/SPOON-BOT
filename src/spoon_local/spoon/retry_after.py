"""Parse provider Retry-After without logging header contents."""
import math
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime

def retry_delay(header):
    try:
        seconds=float(header)
    except (TypeError,ValueError):
        try:seconds=(parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
        except (ValueError,TypeError,OverflowError):return 30
    return max(1,math.ceil(seconds)) if math.isfinite(seconds) else 30
