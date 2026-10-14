"""One in-memory token slot. No filesystem or database writes."""

from dataclasses import dataclass, field
import math

from spoon_local.spoon.oauth import OAuthFailure, REQUIRED_SCOPES


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    refresh_at: float
    scopes: frozenset[str]

    @classmethod
    def from_response(cls, response: dict, now: float):
        try:
            access, refresh = response["access_token"], response["refresh_token"]
            lifetime = response["expires_in"]
            scope = response["scope"]
            if (
                not isinstance(access, str) or not access
                or not isinstance(refresh, str) or not refresh
                or response["token_type"] != "Bearer"
                or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float))
                or not math.isfinite(lifetime) or lifetime <= 0
                or not isinstance(scope, str)
                or not REQUIRED_SCOPES.issubset(scope.split())
            ):
                raise OAuthFailure()
            margin = min(lifetime * 0.1, 300)
            return cls(access, refresh, now + lifetime, now + lifetime - margin, frozenset(scope.split()))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise OAuthFailure() from None


class MemoryTokenStore:
    def __init__(self):
        self.pair: TokenPair | None = None

    def replace(self, pair: TokenPair) -> None:
        self.pair = pair

    def clear(self) -> None:
        self.pair = None
