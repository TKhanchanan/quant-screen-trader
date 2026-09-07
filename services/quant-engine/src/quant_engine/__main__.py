"""Command-line entry point for the local quant engine."""

from __future__ import annotations

import argparse
import ipaddress
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from quant_engine.paths import DATA_DIR_ENV

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _loopback_host(value: str) -> str:
    """Reject network-visible binds: this service is intentionally local only."""
    if value.lower() == "localhost":
        return value
    try:
        if ipaddress.ip_address(value).is_loopback:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("host must be a loopback address or localhost")


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the QuantScreen Trader quant engine")
    parser.add_argument(
        "--host",
        type=_loopback_host,
        default=os.environ.get("QST_ENGINE_HOST", DEFAULT_HOST),
        help="loopback bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=os.environ.get("QST_ENGINE_PORT", str(DEFAULT_PORT)),
        help="listen port (default: %(default)s)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=os.environ.get(DATA_DIR_ENV) or None,
        help=f"application data root (overrides {DATA_DIR_ENV})",
    )
    parser.add_argument(
        "--log-level",
        type=str.lower,
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default=os.environ.get("QST_LOG_LEVEL", "info").lower(),
    )
    parser.add_argument("--reload", action="store_true", help="reload when Python files change")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.data_dir is not None:
        os.environ[DATA_DIR_ENV] = str(args.data_dir)

    uvicorn.run(
        "quant_engine.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
