"""OAuth interface and offline simulator. No network transport is implemented."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID
import secrets
import time
from collections.abc import Callable

REQUIRED_SCOPES = frozenset({"live.read", "events.donation"})
REAL_EVENT_SCOPES = REQUIRED_SCOPES | frozenset({"events.chat", "events.like", "listeners.read", "fans.read", "chat.send"})
MOCK_CLIENT_ID = "00000000-0000-4000-8000-000000000001"
MOCK_CLIENT_SECRET = "mock-only-not-a-real-secret"
LOCAL_ORIGIN = "http://localhost:8000"
CALLBACK_URI = LOCAL_ORIGIN + "/auth/spoon/callback"


class OAuthFailure(Exception):
    """A deliberately generic error; provider responses are never forwarded."""

    def __init__(self):
        super().__init__("OAuth operation failed")


@dataclass(frozen=True)
class OAuthSettings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    region: str = "kr"
    redirect_uri: str = CALLBACK_URI
    scopes: frozenset[str] = REQUIRED_SCOPES

    def __post_init__(self):
        try:
            UUID(self.client_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid OAuth configuration") from None
        if (
            not isinstance(self.client_secret, str) or not self.client_secret.strip()
            or not isinstance(self.region, str) or self.region not in {"kr", "jp"}
            or self.redirect_uri != CALLBACK_URI or not isinstance(self.scopes,frozenset)
            or not REQUIRED_SCOPES.issubset(self.scopes) or not self.scopes.issubset(REAL_EVENT_SCOPES)
        ):
            raise ValueError("Invalid OAuth configuration")

    @classmethod
    def mock(cls):
        return cls(MOCK_CLIENT_ID, MOCK_CLIENT_SECRET)


class OAuthClient(Protocol):
    async def exchange(self, code: str, settings: OAuthSettings) -> dict: ...
    async def refresh(self, refresh_token: str, settings: OAuthSettings) -> dict: ...
    async def revoke(self, token: str, settings: OAuthSettings) -> None: ...


class DisabledHttpOAuthClient:
    """Explicit real-client boundary. Always refuses; never opens a socket."""

    async def exchange(self, code: str, settings: OAuthSettings) -> dict:
        raise OAuthFailure()

    async def refresh(self, refresh_token: str, settings: OAuthSettings) -> dict:
        raise OAuthFailure()

    async def revoke(self, token: str, settings: OAuthSettings) -> None:
        raise OAuthFailure()


class MockOAuthClient:
    """Issues only local dummy codes/tokens, with counters for verification."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.codes: dict[str, float] = {}
        self.refresh_tokens: set[str] = set()
        self.exchange_calls = 0
        self.refresh_calls = 0
        self.revoke_calls = 0
        self.fail_exchange = False
        self.fail_refresh = False
        self.fail_revoke = False
        self.token_overrides: dict = {}

    def issue_code(self) -> str:
        self.codes = {key: deadline for key, deadline in self.codes.items() if self.clock() < deadline}
        if len(self.codes) >= 32:
            raise OAuthFailure()
        code = "mock-code-" + secrets.token_urlsafe(32)
        self.codes[code] = self.clock() + 60
        return code

    def _pair(self) -> dict:
        refresh = "mock-refresh-" + secrets.token_urlsafe(32)
        self.refresh_tokens.add(refresh)
        return {
            "access_token": "mock-access-" + secrets.token_urlsafe(32),
            "refresh_token": refresh,
            "token_type": "Bearer", "expires_in": 3600,
            "scope": "live.read events.donation", **self.token_overrides,
        }

    async def exchange(self, code: str, settings: OAuthSettings) -> dict:
        self.exchange_calls += 1
        deadline = self.codes.pop(code, None)
        if deadline is None or self.clock() >= deadline:
            raise OAuthFailure()
        if self.fail_exchange:
            raise OAuthFailure()
        return self._pair()

    async def refresh(self, refresh_token: str, settings: OAuthSettings) -> dict:
        self.refresh_calls += 1
        if refresh_token not in self.refresh_tokens:
            raise OAuthFailure()
        self.refresh_tokens.remove(refresh_token)
        if self.fail_refresh:
            raise OAuthFailure()
        return self._pair()

    async def revoke(self, token: str, settings: OAuthSettings) -> None:
        self.revoke_calls += 1
        if self.fail_revoke:
            raise OAuthFailure()
        self.refresh_tokens.discard(token)
