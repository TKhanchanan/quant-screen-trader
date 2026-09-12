"""Local developer entry point for a replay (Phase 11).

``python -m quant_engine.replay --from ... --to ...``

Deliberately available without the desktop. A backtest is something a developer needs to be able
to run, diff and re-run from a terminal while changing nothing about the application, and
requiring a UI to validate a research layer would make the research layer harder to check than
the thing it is checking.

It is offline compute over a recorded file. It starts no browser, opens no platform session,
reads no wallet and cannot reach an execution surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from quant_engine.analytics.models import AnalyticsSettings
from quant_engine.configuration import Platform
from quant_engine.paper.policy import PaperSettings
from quant_engine.paths import app_paths
from quant_engine.replay.models import ReplayManifest, WalkForwardSettings
from quant_engine.replay.service import default_source, execute

PLATFORM_CHOICES = ("capitalbear", "iqoption", "both")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quant_engine.replay",
        description="Replay recorded market history through the frozen quant pipeline",
    )
    parser.add_argument("--from", dest="from_time", type=int, help="evaluation start, epoch ms")
    parser.add_argument("--to", dest="to_time", type=int, help="evaluation end, epoch ms")
    parser.add_argument("--platform", choices=PLATFORM_CHOICES, default="both")
    parser.add_argument(
        "--warmup",
        type=int,
        help="warm-up duration in ms; omitted derives it from qfe-v2 and the platform policies",
    )
    parser.add_argument("--data-dir", type=Path, help="application data root to read and write")
    parser.add_argument("--output", type=Path, help="write the summary JSON here as well")
    parser.add_argument(
        "--latency",
        type=int,
        action="append",
        default=[],
        help="extra research decision delay in ms; repeatable",
    )
    parser.add_argument("--currency", help="simulated paper currency, with --stake and --payout")
    parser.add_argument("--stake", type=float, help="simulated paper stake")
    parser.add_argument("--payout", type=float, help="simulated net payout fraction on a win")
    parser.add_argument("--starting-capital", type=float, help="simulated starting balance")
    parser.add_argument("--folds", type=int, default=3, help="walk-forward fold count")
    parser.add_argument("--no-walk-forward", action="store_true")
    parser.add_argument("--timezone", default=AnalyticsSettings().timezone)
    return parser


def manifest_from(args: argparse.Namespace) -> ReplayManifest:
    platforms: tuple[Platform, ...] = (
        ("capitalbear", "iqoption")
        if args.platform == "both"
        else (("capitalbear",) if args.platform == "capitalbear" else ("iqoption",))
    )
    return ReplayManifest(
        includeCapitalBear="capitalbear" in platforms,
        includeIqOption="iqoption" in platforms,
        fromTime=args.from_time,
        toTime=args.to_time,
        warmupDurationMs=args.warmup,
        paperStartingCapital=args.starting_capital,
        paperSettings=PaperSettings(
            paperCurrency=args.currency, paperStake=args.stake, paperPayoutRate=args.payout
        ),
        analyticsSettings=AnalyticsSettings(timezone=args.timezone),
        latencyScenarios=sorted({value for value in args.latency if value > 0}),
        walkForward=WalkForwardSettings(
            enabled=not args.no_walk_forward, mode="COUNT", foldCount=max(1, args.folds)
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = app_paths(args.data_dir)
    manifest = manifest_from(args)
    report = execute(
        manifest,
        market_data=paths.market_data,
        factory=default_source(paths.market_data),
    )
    payload = {
        "replayRunId": str(report.run.replayRunId),
        "status": report.run.status,
        "run": report.run.model_dump(mode="json"),
        "summary": report.summary.model_dump(mode="json"),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)
    return 0 if report.run.status == "COMPLETED" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through __main__
    sys.exit(main())
