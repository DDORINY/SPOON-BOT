"""Remove OAuth queries from ASGI scope and restrict auth hosts before routing."""

from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from spoon_local.web.auth_routes import SAFE_HEADERS


class AuthSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/auth/spoon"):
            query = scope.get("query_string", b"")
            # Mutate the original scope, which Uvicorn uses for access logging.
            scope["query_string"] = b""
            try:
                if len(query) > 4096:
                    raise ValueError()
                scope["oauth_query"] = parse_qs(query.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
            except (ValueError, UnicodeError):
                scope["oauth_query"] = {}
            hosts = [value for key, value in scope["headers"] if key.lower() == b"host"]
            cookies = [value for key, value in scope["headers"] if key.lower() == b"cookie"]
            duplicate_cookie = len(cookies) > 1 or any(
                sum(item.strip().startswith(name+b'=') for value in cookies for item in value.split(b';')) > 1
                for name in (b'spoon_mock_session', b'spoon_real_session')
            )
            if hosts != [b"localhost:8000"] or duplicate_cookie:
                response = JSONResponse({"error": "invalid_auth_host"}, status_code=400, headers=SAFE_HEADERS)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
