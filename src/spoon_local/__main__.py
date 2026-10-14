"""Loopback-only launcher; mock auth is an explicit offline option."""

import argparse
import uvicorn
from pathlib import Path

from spoon_local.main import create_app, create_mock_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-auth", action="store_true", help="Enable local mock auth, never real SPOON login")
    parser.add_argument("--mock-db", type=Path, help="Absolute dedicated path ending in mock-sse.sqlite3")
    args = parser.parse_args()
    if args.mock_auth and args.mock_db is None:
        parser.error("--mock-auth requires --mock-db; real .env and DB are never used")
    if args.mock_db is not None and not args.mock_auth:
        parser.error("--mock-db requires --mock-auth")
    app = create_mock_app(args.mock_db) if args.mock_auth else create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
