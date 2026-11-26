"""Start the web UI:  uv run python -m app.web  [--host 127.0.0.1] [--port 8000]"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from app.config import get_settings
from app.main import build_service
from app.web.server import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secure SQL Agent web UI")
    parser.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1 (this computer only)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
        service = build_service(settings)
    except Exception as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "WARNING: the web UI has no login. Anyone who can reach "
            f"{args.host}:{args.port} can query and change the database.",
            file=sys.stderr,
        )
    print(f"Open http://{args.host}:{args.port} in your browser")
    uvicorn.run(create_app(service, settings), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
