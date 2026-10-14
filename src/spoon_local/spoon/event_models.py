"""Validate official fields and immediately discard donor identifiers/text."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json

from spoon_local.spoon.sse_parser import SSEFrame


class InvalidDonation(Exception):
    def __init__(self):
        super().__init__("Invalid donation event")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_amount(value: Decimal) -> str:
    if (
        not isinstance(value, Decimal) or not value.is_finite() or value < 0
        or len(value.as_tuple().digits) > 32 or abs(value.as_tuple().exponent) > 18
        or value.adjusted() > 18
    ):
        raise InvalidDonation()
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"


@dataclass(frozen=True)
class Donation:
    amount_spoons: str
    occurred_at_utc: str
    received_at_utc: str
    event_id: str | None = field(default=None, repr=False)


def reject_constant(value):
    raise InvalidDonation()


def normalize_donation(frame: SSEFrame, received_at: str | None = None) -> Donation:
    try:
        if len(frame.data.encode("utf-8")) > 65536:
            raise InvalidDonation()
        payload = json.loads(frame.data, parse_float=Decimal, parse_int=Decimal, parse_constant=reject_constant)
        if not isinstance(payload, dict):
            raise InvalidDonation()
        stack = [(payload, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 8:
                raise InvalidDonation()
            if isinstance(node, dict):
                stack.extend((value, depth + 1) for value in node.values())
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node)
        user = payload["user"]
        if (
            frame.event != "donation" or not isinstance(user, dict)
            or not isinstance(user["id"], str) or not user["id"]
            or not (user["nickname"] is None or isinstance(user["nickname"], str))
            or not (payload["message"] is None or isinstance(payload["message"], str))
            or not isinstance(payload["time"], str)
        ):
            raise InvalidDonation()
        amount = canonical_amount(payload["amount"])
        moment = datetime.fromisoformat(payload["time"].replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise InvalidDonation()
        event_id = frame.explicit_id
        if event_id is not None and (len(event_id) > 256 or "\x00" in event_id):
            raise InvalidDonation()
        return Donation(amount, moment.astimezone(timezone.utc).isoformat(timespec="microseconds"), received_at or utc_now(), event_id)
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
        raise InvalidDonation() from None
