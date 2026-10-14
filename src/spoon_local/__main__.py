"""Loopback-only launcher; mock auth is an explicit offline option."""

import argparse
import uvicorn
from pathlib import Path
from spoon_local.spoon.real_oauth import RealConfigurationError




def create_real_app(**kwargs):
    from spoon_local.main import create_real_app as factory
    return factory(**kwargs)

def create_mock_app(path):
    from spoon_local.main import create_mock_app as factory
    return factory(path)

def create_app():
    from spoon_local.main import create_app as factory
    return factory()


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--simulator", action="store_true", help="Offline isolated broadcast simulator; no environment; separate local settings DB")
    modes.add_argument("--real-oauth", action="store_true", help="Enable manual real SPOON OAuth; never starts SSE")
    modes.add_argument("--mock-auth", action="store_true", help="Enable local mock auth, never real SPOON login")
    parser.add_argument("--mock-db", type=Path, help="Absolute dedicated path ending in mock-sse.sqlite3")
    parser.add_argument("--real-sse-db", type=Path, help="Absolute dedicated real-sse.sqlite3 path; collection starts only by confirmed POST")
    parser.add_argument("--simulator-settings-db", type=Path, help="Absolute dedicated bot-settings.sqlite3; simulator mode only")
    args = parser.parse_args()
    if args.simulator_settings_db is not None and not args.simulator:parser.error("--simulator-settings-db requires --simulator")
    if args.real_sse_db is not None and not args.real_oauth:
        parser.error("--real-sse-db requires --real-oauth")
    if args.mock_auth and args.mock_db is None:
        parser.error("--mock-auth requires --mock-db; real .env and DB are never used")
    if args.mock_db is not None and not args.mock_auth:
        parser.error("--mock-db requires --mock-auth")
    if args.simulator:
        from spoon_local.simulator_network import install_simulator_network_guard
        from spoon_local.simulator import create_simulator_app
        install_simulator_network_guard()
        uvicorn.run(create_simulator_app(settings_path=args.simulator_settings_db or Path(__file__).resolve().parents[2]/"local_settings"/"simulator"/"bot-settings.sqlite3"), host="127.0.0.1", port=8000, workers=1)
        return
    try:
        app = (create_real_app(real_sse_db=args.real_sse_db) if args.real_sse_db else create_real_app()) if args.real_oauth else (create_mock_app(args.mock_db) if args.mock_auth else create_app())
    except RealConfigurationError:
        parser.error("Real OAuth configuration missing/invalid; check the four local setting names without sharing values")
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
