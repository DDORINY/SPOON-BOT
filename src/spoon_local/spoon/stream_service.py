"""Single offline collector with a bounded queue and one SQLite writer task."""

import asyncio
from collections.abc import Callable, Awaitable
from contextlib import suppress
import json
import math
import random
import sqlite3

from spoon_local.spoon.auth_service import AuthService
from spoon_local.spoon.donation_repository import DonationRepository
from spoon_local.spoon.event_models import normalize_donation, InvalidDonation, utc_now
from spoon_local.spoon.sse_parser import SSEParser, SSEParseError
from spoon_local.spoon.sse_source import MockSSESource


class CollectionFailure(Exception):
    pass


class MockStreamService:
    def __init__(self, auth: AuthService, repository: DonationRepository, source_factory: Callable[[], MockSSESource], *, queue_size: int = 256, sleep: Callable[[float], Awaitable] = asyncio.sleep, jitter: Callable[[], float] = random.random):
        if queue_size < 1:
            raise ValueError('Invalid queue capacity')
        self.auth = auth
        self.repository = repository
        self.source_factory = source_factory
        self.queue_size = queue_size
        self.sleep = sleep
        self.jitter = jitter
        self.state = 'STOPPED'
        self.writer_task: asyncio.Task | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.session_key = None
        self.gap_persistence_failed = False
        self.unpersisted_gaps = {}
        self.counts = {'inserted': 0, 'duplicate': 0, 'conflict': 0, 'invalid': 0}

    async def _write(self, operation, *args):
        if self.writer_task is None or self.writer_task.done():
            # Startup/cleanup has no active writer. Never race another DB writer.
            return getattr(self.repository, operation)(*args)
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
        await self.queue.put((operation, args, future))
        return await future

    async def _gap(self, reason):
        detected = utc_now()
        try:
            await self._write("gap", reason, detected)
        except (sqlite3.Error, ValueError, OSError):
            self.gap_persistence_failed = True
            self.unpersisted_gaps.setdefault(reason, detected)

    async def start(self) -> bool:
        async with self.lock:
            if self.task and not self.task.done():
                return False
            if not await self.auth.ensure_access():
                self.state = 'AUTH_REQUIRED'
                return False
            if not await self.auth.authorized_for({'events.donation'}):
                self.state = 'PERMISSION_REQUIRED'
                return False
            try:
                source = self.source_factory()
            except Exception:
                self.state = 'STOPPED'
                await self._gap('source_initialization_failed')
                return False
            if not isinstance(source, MockSSESource):
                self.state = 'STOPPED'
                await self._gap('invalid_source')
                return False
            self.queue = asyncio.Queue(maxsize=self.queue_size)
            self.state = 'CONNECTING'
            self.task = asyncio.create_task(self._run(source))
            return True

    async def stop(self, reason='manual_stop'):
        async with self.lock:
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
                await self._gap(reason)
            self.state = 'AUTH_REQUIRED' if reason == 'auth_disconnected' else 'STOPPED'

    async def close(self):
        await self.stop('process_shutdown')

    async def _writer(self):
        while True:
            operation, args, future = await self.queue.get()
            try:
                result = getattr(self.repository, operation)(*args)
                if operation == 'append':
                    self.counts[result] += 1
                if future is not None and not future.done():
                    future.set_result(result)
            except Exception as error:
                if future is not None and not future.done():
                    future.set_exception(error)
                raise
            finally:
                self.queue.task_done()

    async def _run(self, source):
        writer = asyncio.create_task(self._writer())
        self.writer_task = writer
        producer = asyncio.create_task(self._collect(source))
        try:
            finished, _ = await asyncio.wait([writer, producer], return_when=asyncio.FIRST_COMPLETED)
            if writer in finished:
                writer.result()
            producer.result()
            drainer = asyncio.create_task(self.queue.join())
            try:
                done, _ = await asyncio.wait([writer, drainer], return_when=asyncio.FIRST_COMPLETED)
                if writer in done:
                    writer.result()
            finally:
                drainer.cancel()
                await asyncio.gather(drainer, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except (sqlite3.Error, ValueError, OSError):
            self.state = 'STORAGE_ERROR'
            await self._gap('storage_error')
        except Exception:
            # Never include source/provider exception text in logs or APIs.
            self.state = 'STOPPED'
            await self._gap('collector_error')
        finally:
            producer.cancel()
            writer.cancel()
            await asyncio.gather(producer, writer, return_exceptions=True)
            if not self.queue.empty():
                await self._gap('uncommitted_queue')
            while not self.queue.empty():
                operation, args, future = self.queue.get_nowait()
                if future is not None and not future.done():
                    future.cancel()
                self.queue.task_done()

    async def _pause(self, attempt: int, waiting=False):
        delay = min(30, (5 if waiting else 1) * 2 ** min(attempt, 5))
        await self.sleep(delay + min(1, max(0, self.jitter())))

    async def _authorized(self) -> bool:
        if not await self.auth.ensure_access():
            self.state = 'AUTH_REQUIRED'
            await self._gap('auth_unavailable')
            return False
        if not await self.auth.authorized_for({'events.donation'}):
            self.state = 'PERMISSION_REQUIRED'
            await self._gap('permission_required')
            return False
        return True

    async def _collect(self, source):
        attempts, immediate_count, auth_retry = 0, 0, False
        while True:
            if not await self._authorized():
                return
            self.state = 'CONNECTING'
            connection = None
            try:
                connection = await asyncio.wait_for(source.open(), timeout=10)
                status = connection.status
                if status != 200:
                    await connection.close()
                if status == 401:
                    if not auth_retry and await self.auth.ensure_access(force_refresh=True):
                        auth_retry = True
                        continue
                    self.state = 'AUTH_REQUIRED'
                    await self._gap('authentication_failed')
                    return
                if status == 403:
                    self.state = 'BLOCKED' if connection.detail_code == 'OAPI_MNGR_0209' else 'PERMISSION_REQUIRED'
                    await self._gap('permission_blocked')
                    return
                if status == 404:
                    self.state = 'WAITING'
                    await self._pause(attempts, waiting=True)
                    attempts += 1
                    continue
                if status == 429:
                    self.state = 'RATE_LIMITED'
                    try:
                        delay = float(connection.retry_after)
                        if not math.isfinite(delay) or delay < 0 or delay > 86400:
                            raise ValueError()
                    except (TypeError, ValueError):
                        delay = min(30, 2 ** min(attempts, 5))
                    await self._gap('rate_limited')
                    await self.sleep(delay)
                    attempts += 1
                    continue
                if status in {500, 502, 503}:
                    raise ConnectionError()
                if status != 200:
                    self.state = 'STOPPED'
                    await self._gap('invalid_response')
                    return
                auth_retry = False
                self.session_key = await self._write("session", connection.live_id)
                for pending_reason, detected in list(self.unpersisted_gaps.items()):
                    await self._write("gap", pending_reason, detected)
                    del self.unpersisted_gaps[pending_reason]
                self.gap_persistence_failed = bool(self.unpersisted_gaps)
                await self._write("recover_gaps")
                self.state = 'LIVE'
                parser = SSEParser()
                iterator = connection.read().__aiter__()
                reason = None
                while True:
                    if not await self._authorized():
                        return
                    try:
                        chunk = await asyncio.wait_for(anext(iterator), timeout=45)
                    except StopAsyncIteration:
                        if parser.finish():
                            await self._gap('incomplete_frame')
                        break
                    if not await self._authorized():
                        return
                    for frame in parser.feed(chunk):
                        if frame.event == 'donation':
                            try:
                                donation = normalize_donation(frame)
                            except InvalidDonation:
                                self.counts['invalid'] += 1
                                await self._gap('invalid_donation')
                                continue
                            try:
                                self.queue.put_nowait(("append", (donation, self.session_key), None))
                            except asyncio.QueueFull:
                                self.state = 'STOPPED'
                                await self._gap('queue_overflow')
                                return
                        elif frame.event == 'end':
                            try:
                                reason = json.loads(frame.data)['reason']
                            except (ValueError, TypeError, KeyError):
                                reason = 'unknown'
                            break
                    if reason is not None:
                        break
                await connection.close()
                if reason == 'LIVE_ENDED':
                    self.state = 'WAITING'
                    await self._pause(attempts, waiting=True)
                    attempts += 1
                elif reason == 'TOKEN_EXPIRED':
                    self.state = 'REFRESHING'
                    await self._gap('token_expired')
                    if not await self.auth.ensure_access(force_refresh=True):
                        self.state = 'AUTH_REQUIRED'
                        return
                elif reason == 'RECONNECT':
                    await self._gap('server_reconnect')
                    immediate_count += 1
                    if immediate_count > 3:
                        self.state = 'STOPPED'
                        await self._gap('reconnect_limit')
                        return
                else:
                    self.state = 'BACKOFF'
                    await self._gap('unexpected_eof')
                    await self._pause(attempts)
                    attempts += 1
            except (ConnectionError, TimeoutError):
                self.state = 'BACKOFF'
                await self._gap('network_interruption')
                await self._pause(attempts)
                attempts += 1
            except SSEParseError:
                self.state = 'BACKOFF'
                await self._gap('invalid_frame')
                await self._pause(attempts)
                attempts += 1
            finally:
                if connection is not None:
                    await connection.close()
