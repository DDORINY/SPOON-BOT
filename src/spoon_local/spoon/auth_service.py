"""Shared auth state machine and strictly separated mock/real services."""

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


class _AuthCore:
    ready_state = "mock_ready"
    authenticated_state = "mock_authenticated"
    mode = "mock"
    def __init__(
        self, settings: OAuthSettings, client: OAuthClient,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.client = client
        self.clock = clock
        self.store = MemoryTokenStore()
        self.session: BrowserSession | None = None
        self.pending: Pending | None = None
        self.used_codes: set[bytes] = set()
        self.lock = asyncio.Lock()
        self.state = self.ready_state

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

    async def begin(self, identifier: str, *, reuse_pending: bool = False) -> str:
        async with self.lock:
            if not self.valid_session(identifier) or self.store.pair is not None:
                raise OAuthFailure()
            if reuse_pending and self.pending and self.valid_pending(identifier, self.pending.state):
                return self.pending.state
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

    def _on_auth_failure(self):
        pass

    def _on_provider_failure(self, failure):
        pass

    async def complete(self, identifier: str | None, state: str | None, code: str | None, error: str | None) -> bool:
        async with self.lock:
            if not self.valid_pending(identifier, state):
                return False
            self.pending = None  # Consume before any await/provider call.
            if error:
                self._on_auth_failure()
                return False
            digest = hashlib.sha256((code or "").encode()).digest()
            if not code or digest in self.used_codes or len(self.used_codes) >= 4096:
                self._on_auth_failure()
                return False
            self.used_codes.add(digest)
            try:
                response = await self.client.exchange(code, self.settings)
                pair = TokenPair.from_response(response, self.clock())
                self.store.replace(pair)
                self.state = self.authenticated_state
                return True
            except asyncio.CancelledError:
                self.store.clear()
                self.state = "reauth_required"
                self._on_auth_failure()
                raise
            except Exception as failure:
                # Provider messages may contain secrets; never log/rethrow them.
                self.store.clear()
                self.state = "reauth_required"
                self._on_auth_failure()
                self._on_provider_failure(failure)
                return False

    async def refresh_if_needed(self, force: bool = False, expected_pair=None) -> None:
        async with self.lock:
            pair = self.store.pair
            if (not pair or self.state != self.authenticated_state
                or (force and expected_pair is not None and pair is not expected_pair)
                or (not force and self.clock() < pair.refresh_at)):
                return
            try:
                response = await self.client.refresh(pair.refresh_token, self.settings)
                updated = TokenPair.from_response(response, self.clock())
                if updated.refresh_token == pair.refresh_token:
                    raise OAuthFailure()
                self.store.replace(updated)
            except asyncio.CancelledError:
                self.store.clear()
                self.state = "reauth_required"
                self._on_auth_failure()
                raise
            except Exception as failure:
                self.store.clear()
                self.state = "reauth_required"
                self._on_provider_failure(failure)

    async def ensure_access(self, force_refresh: bool = False) -> bool:
        expected = self.store.pair
        await self.refresh_if_needed(force_refresh, expected)
        return bool(self.state == self.authenticated_state and self.store.pair)

    async def authorized_for(self, scopes: set[str]) -> bool:
        return bool(await self.ensure_access() and scopes.issubset(self.store.pair.scopes))

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
            self.state = self.ready_state
            return True

    def close(self) -> None:
        self.store.clear()
        self.session = None
        self.pending = None
        self.used_codes.clear()
        if isinstance(self.client, MockOAuthClient):
            self.client.codes.clear()
            self.client.refresh_tokens.clear()
        self.state = self.ready_state


class AuthService(_AuthCore):
    """Existing offline service; rejects HTTP clients and real credentials."""

    def __init__(self, settings: OAuthSettings, client: OAuthClient, clock=time.monotonic):
        if (not isinstance(client, MockOAuthClient) or settings.client_id != MOCK_CLIENT_ID or settings.client_secret != MOCK_CLIENT_SECRET):
            raise ValueError("Only offline mock authentication is available")
        super().__init__(settings,client,clock)


class RealAuthService(_AuthCore):
    account_context = None

    async def complete(self, identifier, state, code, error):
        success = await super().complete(identifier, state, code, error)
        # No documented stable DJ identity is supplied by the token response.
        # A login, token value, client ID or arbitrary user_id is not an identity.
        self.account_context = None
        return success

    def close(self):
        super().close()
        self.account_context = None

    async def begin(self, identifier: str) -> str:
        return await super().begin(identifier, reuse_pending=True)

    async def invalidate_access(self):
        async with self.lock:
            self._on_auth_failure()

    async def access_lease(self, scopes):
        """Server-internal snapshot; identity guards concurrent token rotation."""
        if not await self.authorized_for(scopes):
            return None
        return self.store.pair

    ready_state = "real_ready"
    authenticated_state = "authenticated"
    mode = "real"

    def __init__(self, settings, client, clock=time.monotonic):
        from spoon_local.spoon.real_oauth import RealOAuthClient, validate_real_settings
        validate_real_settings(settings)
        if not isinstance(client,RealOAuthClient) or client.settings != settings:
            raise ValueError("Real OAuth requires its own HTTP client")
        super().__init__(settings,client,clock)

    def _on_auth_failure(self):
        self.store.clear()
        self.session=None
        self.pending=None
        self.state="reauth_required"

    def _on_provider_failure(self, failure):
        if getattr(failure,"category",None) in {"invalid_client","invalid_request","unsupported_grant_type"}:
            self.state="configuration_error"

    async def refresh_if_needed(self, force=False, expected_pair=None):
        await super().refresh_if_needed(force,expected_pair)
        if self.state in {"reauth_required","configuration_error"}:
            self.session=None
            self.pending=None

    async def disconnect(self):
        async with self.lock:
            if self.state=="revoke_unconfirmed" and self.store.pair is None:
                return False
            confirmed=True
            try:
                if self.store.pair:
                    await self.client.revoke(self.store.pair.refresh_token,self.settings)
            except Exception:
                confirmed=False
            finally:
                self.store.clear()
                self.session=None
                self.pending=None
            self.state=self.ready_state if confirmed else "revoke_unconfirmed"
            return confirmed
