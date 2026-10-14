"""Single-process mock auth state machine with injected monotonic clock."""

import asyncio
from dataclasses import dataclass, field
import hashlib
import secrets
import time
from collections.abc import Callable

from spoon_local.spoon.oauth import (
    MOCK_CLIENT_ID, MOCK_CLIENT_SECRET, MockOAuthClient, OAuthClient, OAuthFailure, OAuthSettings,
)
from spoon_local.spoon.token_store import MemoryTokenStore, TokenPair

SESSION_TTL = 300


def same(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass
class BrowserSession:
    identifier: str = field(repr=False)
    csrf: str = field(repr=False)
    deadline: float


@dataclass
class Pending:
    state: str = field(repr=False)
    session: str = field(repr=False)
    deadline: float


class AuthService:
    def __init__(
        self, settings: OAuthSettings, client: OAuthClient,
        clock: Callable[[], float] = time.monotonic,
    ):
        # Real credentials/clients cannot be enabled through settings or .env.
        if (
            not isinstance(client, MockOAuthClient)
            or settings.client_id != MOCK_CLIENT_ID
            or settings.client_secret != MOCK_CLIENT_SECRET
        ):
            raise ValueError("Only offline mock authentication is available")
        self.settings = settings
        self.client = client
        self.clock = clock
        self.store = MemoryTokenStore()
        self.session: BrowserSession | None = None
        self.pending: Pending | None = None
        self.used_codes: set[bytes] = set()
        self.lock = asyncio.Lock()
        self.state = "mock_ready"

    def prepare(self, identifier: str | None) -> BrowserSession:
        if not self.valid_session(identifier):
            self.session = BrowserSession(secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock() + SESSION_TTL)
            self.pending = None
        return self.session

    def valid_session(self, identifier: str | None) -> bool:
        return bool(
            self.session and identifier and self.clock() < self.session.deadline
            and same(identifier, self.session.identifier)
        )

    def valid_csrf(self, identifier: str | None, csrf: str | None) -> bool:
        return bool(self.valid_session(identifier) and csrf and same(csrf, self.session.csrf))

    async def begin(self, identifier: str) -> str:
        async with self.lock:
            if not self.valid_session(identifier) or self.store.pair is not None:
                raise OAuthFailure()
            state = secrets.token_urlsafe(32)
            self.pending = Pending(state, identifier, self.clock() + SESSION_TTL)
            return state

    def valid_pending(self, identifier: str | None, state: str | None) -> bool:
        return bool(
            self.valid_session(identifier) and self.pending and state
            and self.clock() < self.pending.deadline
            and same(identifier, self.pending.session)
            and same(state, self.pending.state)
        )

    async def complete(self, identifier: str | None, state: str | None, code: str | None, error: str | None) -> bool:
        async with self.lock:
            if not self.valid_pending(identifier, state):
                return False
            self.pending = None  # Consume before any await/provider call.
            if error:
                return False
            digest = hashlib.sha256((code or "").encode()).digest()
            if not code or digest in self.used_codes or len(self.used_codes) >= 4096:
                return False
            self.used_codes.add(digest)
            try:
                response = await self.client.exchange(code, self.settings)
                pair = TokenPair.from_response(response, self.clock())
                self.store.replace(pair)
                self.state = "mock_authenticated"
                return True
            except Exception:
                # Provider messages may contain secrets; never log/rethrow them.
                self.store.clear()
                self.state = "reauth_required"
                return False

    async def refresh_if_needed(self) -> None:
        async with self.lock:
            pair = self.store.pair
            if not pair or self.clock() < pair.refresh_at or self.state != "mock_authenticated":
                return
            try:
                response = await self.client.refresh(pair.refresh_token, self.settings)
                updated = TokenPair.from_response(response, self.clock())
                if updated.refresh_token == pair.refresh_token:
                    raise OAuthFailure()
                self.store.replace(updated)
            except Exception:
                self.store.clear()
                self.state = "reauth_required"

    async def status(self) -> str:
        await self.refresh_if_needed()
        return self.state

    async def disconnect(self) -> bool:
        async with self.lock:
            self.pending = None
            try:
                if self.store.pair:
                    await self.client.revoke(self.store.pair.refresh_token, self.settings)
            except Exception:
                self.state = "revoke_unconfirmed"
                return False
            self.store.clear()
            self.session = None
            self.state = "mock_ready"
            return True

    def close(self) -> None:
        self.store.clear()
        self.session = None
        self.pending = None
        self.used_codes.clear()
        self.client.codes.clear()
        self.client.refresh_tokens.clear()
        self.state = "mock_ready"
