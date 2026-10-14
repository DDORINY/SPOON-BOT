"""Loopback-only launcher; mock auth is an explicit offline option."""

import argparse
import uvicorn

from spoon_local.main import create_app
from spoon_local.spoon.auth_service import AuthService
from spoon_local.spoon.oauth import MockOAuthClient, OAuthSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-auth", action="store_true", help="Enable local mock auth, never real SPOON login")
    args = parser.parse_args()
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient()) if args.mock_auth else None
    uvicorn.run(create_app(auth_service=auth), host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
