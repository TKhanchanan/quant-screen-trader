"""T-EN..T-ET: what Phase 11 could not possibly do.

Phase 11 is the layer with the most obvious temptation attached to it: it is the first one that
can say what *would have happened*. So the boundary is asserted in both directions and by
scanning the names the code binds, not by describing them. Prose saying the layer cannot press a
button cannot pass this file, and prose cannot hide a real press either.

Four separate walls:

* it cannot reach an execution surface;
* it cannot write into the live durable record;
* it cannot move the live daily session or any Phase 6-9 contract;
* and its output has no consumer anywhere in the application.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import replay_fixtures as fixtures
from quant_engine.market_storage import ParquetStorage
from quant_engine.paper.policy import PaperSettings
from quant_engine.replay import (
    InMemoryObservationSource,
    ReplayEngine,
    ReplayEvidence,
    ReplayManifest,
    replay_root,
    run_replay,
)
from quant_engine.session_guard.engine import SessionGuard
from quant_engine.session_guard.settings import SessionGuardSettings

ROOT = Path(__file__).resolve().parents[1] / "src" / "quant_engine"
REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "replay"
API = ROOT / "replay_api.py"
ACCOUNTING = PaperSettings(paperCurrency="THB", paperStake=50, paperPayoutRate=0.82)

EXECUTION_NAMES = frozenset(
    {
        "sendinputevent",
        "webcontents",
        "webcontentsview",
        "executionmanager",
        "orderexecutor",
        "orderpanel",
        "orderticket",
        "controlmap",
        "presspoint",
        "press",
        "pressed",
        "click",
        "tap",
        "arm",
        "arming",
        "armed",
        "disarm",
        "higher",
        "lower",
        "buy",
        "sell",
        "placeorder",
        "submitorder",
        "brokerstake",
        "brokerpayout",
        "wallet",
        "balance",
        "bankroll",
        "martingale",
        "positionsize",
        "stakemultiplier",
        "increasestake",
        "execute",
        "auto",
        "automode",
        "brokercontrol",
        "sendinput",
    }
)
"""Every name that could reach a broker, plus the two that could only ever be mistaken for one.

``execute`` and ``auto`` are on the list deliberately. Neither is dangerous on its own — running
a replay is a perfectly good thing to call executing it — but in an application that really does
have an AUTO mode and a real order executor, a replay function called ``execute`` is one careless
import away from reading as the wrong thing. The replay layer calls its own entry point
``run_replay`` so the word can stay banned outright."""

MUTATION_NAMES = frozenset(
    {
        "stopsession",
        "updatesettings",
        "savesettings",
        "save_settings",
        "apply_threshold",
        "applythreshold",
        "minselectionscore",
        "minleadmargin",
        "baseweights",
        "executionsettings",
        "advance_live",
        "restore_paper",
        "restore_session_guard",
    }
)
"""Phase 11 may *read* a session limit inside its own sandbox — that is what the research
scenario is — but it may not name the settings writer, the live maintenance pass or the live
restore path. A layer that can spell the setting it just formed an opinion about is one refactor
away from setting it."""

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "argparse",
        "bisect",
        "collections",
        "collections.abc",
        "dataclasses",
        "datetime",
        "hashlib",
        "heapq",
        "json",
        "math",
        "pathlib",
        "statistics",
        "sys",
        "threading",
        "time",
        "types",
        "typing",
        "uuid",
        "zoneinfo",
        "duckdb",
        "pydantic",
        "quant_engine",
    }
)

ENGINE_MODULES = frozenset(
    {
        "quant_engine.market_api",
        "quant_engine.market_models",
        "quant_engine.market_storage",
        "quant_engine.features.engine",
        "quant_engine.analytics",
        "quant_engine.analytics.dataset",
        "quant_engine.analytics.thresholds",
        "quant_engine.paper.engine",
        "quant_engine.paper.policy",
        "quant_engine.session_guard.engine",
        "quant_engine.strategy.policy",
    }
)
"""The production engines the replay is *required* to import. Phase 11's whole claim is that it
drives the real pipeline, so a missing import here is as much a failure as a forbidden one."""

CLOCK_FREE = ("clock.py", "source.py", "walk_forward.py", "robustness.py", "report.py", "models.py")


def sources() -> list[Path]:
    found = sorted(PACKAGE.rglob("*.py"))
    assert len(found) >= 10, "the scan must actually be reading the package"
    return found


def identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.casefold())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.casefold())
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name.casefold())
        elif isinstance(node, ast.arg):
            names.add(node.arg.casefold())
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg.casefold())
    return names


def modules_imported(paths: list[Path]) -> set[str]:
    imported: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def manifest(**overrides: Any) -> ReplayManifest:
    values: dict[str, Any] = {
        "warmupDurationMs": 0,
        "sourceMode": "SYNTHETIC",
        "includeIqOption": False,
        "paperSettings": ACCOUNTING,
    }
    values.update(overrides)
    return ReplayManifest(**values)


def replay(root: Path, **overrides: Any) -> Any:
    spec = manifest(**overrides)
    return ReplayEngine(
        spec,
        InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        root=root,
        persist=False,
    ).run()


# --- T-EN no execution surface ---------------------------------------------------------


def test_no_name_in_the_replay_layer_refers_to_a_broker_control() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in [*sources(), API]
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


def test_the_replay_http_surface_exposes_no_route_that_could_be_read_as_an_order() -> None:
    routes = [
        node.value
        for node in ast.walk(ast.parse(API.read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/api/")
    ]
    assert routes, "the scan must actually be reading the router"
    for route in routes:
        assert route.startswith("/api/replay/")
        for forbidden in ("apply", "execute", "arm", "press", "auto", "order", "stake"):
            assert forbidden not in route.casefold(), route


def test_the_live_execution_layer_is_still_present_and_untouched_by_this_package() -> None:
    # Phase 11 is not allowed to reach the execution layer, and it is equally not allowed to
    # remove it. An application made "safer" by deleting the operator's functionality is a
    # different application, not a safer one.
    desktop = REPOSITORY / "apps" / "desktop" / "electron" / "main"
    for name in ("execution-manager.ts", "order-executor.ts", "order-panel.ts"):
        assert (desktop / name).is_file(), name
    assert (REPOSITORY / "packages" / "shared-types" / "src" / "execution.ts").is_file()
    # And nothing in the replay layer names any of them.
    text = " ".join(path.read_text() for path in [*sources(), API])
    for name in ("execution-manager", "order-executor", "order-panel", "ExecutionManager"):
        assert name not in text, name


def test_the_replay_layer_imports_nothing_that_could_reach_a_broker() -> None:
    roots = {module.split(".")[0] for module in modules_imported(sources())}
    assert roots <= {name.split(".")[0] for name in ALLOWED_IMPORTS}
    assert roots.isdisjoint({"socket", "http", "subprocess", "requests", "httpx", "asyncio"})


def test_the_replay_layer_cannot_name_a_settings_writer_or_the_live_maintenance_pass() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in [*sources(), API]
        for name in identifiers(ast.parse(path.read_text())) & MUTATION_NAMES
    }
    assert offences == set()


def test_the_replay_api_starts_and_cancels_offline_work_and_applies_nothing() -> None:
    source = API.read_text()
    assert source.count("@router.post") == 2  # start, cancel
    assert "@router.put" not in source and "@router.delete" not in source
    names = identifiers(ast.parse(source))
    assert not {name for name in names if name.startswith("apply")} - {"appliedtoliveexecution"}


# --- T-EO one brain --------------------------------------------------------------------


def test_the_replay_drives_the_production_engines_rather_than_restating_them() -> None:
    imported = modules_imported(sources())
    missing = ENGINE_MODULES - imported
    assert missing == set(), f"replay must drive the real pipeline, not a copy: {missing}"


def test_no_indicator_regime_strategy_or_outcome_rule_is_defined_in_the_replay_layer() -> None:
    # A second definition of an indicator, a regime, a rank score or an outcome rule here would
    # let the backtest disagree with the thing it is supposed to be testing.
    banned = {
        "ema",
        "rsi",
        "atr",
        "macd",
        "bollinger",
        "stochastic",
        "choppiness",
        "efficiency_ratio",
        "rank_score",
        "classify",
        "outcome_for",
        "price_delta_bps",
        "true_range",
    }
    defined = set()
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                defined.add(node.name.casefold())
    assert defined & banned == set()


def test_the_window_gate_inherits_every_timing_rule_from_the_paper_contract() -> None:
    from quant_engine.paper.engine import PaperEngine
    from quant_engine.replay import WindowedPaperEngine

    assert issubclass(WindowedPaperEngine, PaperEngine)
    # It overrides exactly one method, and that method decides only whether an intent is created.
    overridden = {name for name in WindowedPaperEngine.__dict__ if not name.startswith("__")}
    assert overridden == {"on_board"}


# --- T-EP no wall clock in anything that reaches a number ------------------------------


def test_the_analytical_half_of_the_layer_cannot_see_what_time_it_is() -> None:
    for name in CLOCK_FREE:
        tree = ast.parse((PACKAGE / name).read_text())
        names = identifiers(tree)
        assert names.isdisjoint({"utcnow", "today", "perf_counter", "process_time"}), name
        assert "time" not in modules_imported([PACKAGE / name]), name
        # ``now`` is allowed as the replay clock's own reader — it returns the market time the
        # record has reached — but it may never be *called*, which is what reading a host clock
        # looks like in every form of it.
        called = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("now", "utcnow", "today", "time", "monotonic")
        ]
        assert called == [], name


def test_the_only_wall_clock_is_the_stopwatch_on_the_run_metadata() -> None:
    users = {path.name for path in sources() if "time" in modules_imported([path])}
    assert users == {"engine.py", "service.py"}
    run = replay(Path("/tmp") / "unused-replay-root")
    # Every field a result is built from is market time; the runtime pair is diagnostics.
    assert run.run.startedMarketTime is not None
    assert run.run.runtimeMs is not None


# --- T-EQ storage isolation ------------------------------------------------------------


def seed_live_record(root: Path) -> dict[str, bytes]:
    """A live durable record the replay must leave exactly as it found it."""
    storage = ParquetStorage(root)
    from analytics_fixtures import trade

    for index in range(4):
        storage.append("paper_trades", trade(f"live-{index}"))
    storage.flush()
    snapshots = root / "analytics_snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    (snapshots / "0000000000001-live.json").write_text(json.dumps({"live": True}))
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_replay_writes_only_under_its_own_namespace(tmp_path: Path) -> None:
    before = seed_live_record(tmp_path)
    report = run_replay(
        manifest(),
        market_data=tmp_path,
        factory=lambda spec: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        persist=True,
    )
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }
    # Every file that existed is byte-identical, and everything new is inside replay/.
    assert all(after[name] == payload for name, payload in before.items())
    added = set(after) - set(before)
    assert added
    assert all(name.startswith("replay/") for name in added)
    assert replay_root(tmp_path).is_dir()
    assert (replay_root(tmp_path) / str(report.run.replayRunId)).is_dir()


def test_a_replay_leaves_the_live_paper_history_and_analytics_untouched(tmp_path: Path) -> None:
    before = seed_live_record(tmp_path)
    live_trades = [name for name in before if name.startswith("paper_trades/")]
    assert live_trades
    run_replay(
        manifest(),
        market_data=tmp_path,
        factory=lambda spec: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        persist=True,
    )
    storage = ParquetStorage(tmp_path)
    assert len(storage.reload("paper_trades")) == 4
    assert len(storage.reload("daily_sessions")) == 0
    assert (tmp_path / "analytics_snapshots" / "0000000000001-live.json").read_text() == json.dumps(
        {"live": True}
    )


# --- T-ER the live session guard is never touched --------------------------------------


def test_a_replay_cannot_move_the_live_daily_session(tmp_path: Path) -> None:
    live = SessionGuard(
        SessionGuardSettings(
            enabled=True, dailyProfitTarget=500, dailyLossLimit=300, currency="THB"
        )
    )
    at = fixtures.BASE_MS
    before = live.state(at).model_dump(mode="json")
    replay(
        tmp_path,
        sessionGuardScenario=SessionGuardSettings(
            enabled=True, dailyLossLimit=100.0, currency="THB", timezone="UTC"
        ),
    )
    after = live.state(at).model_dump(mode="json")
    assert after == before
    assert after["canOpenNewEntry"] is True


def test_neither_starting_nor_cancelling_a_replay_touches_the_live_trading_day(
    tmp_path: Path,
) -> None:
    """A replay's whole lifecycle, against a live day that is mid-session and counting.

    Starting one, letting it settle simulated outcomes, and cancelling it must all leave the
    real day exactly where it was — the same realized total, the same permission, the same lock
    state. A research run that could reset the operator's day would be the single worst failure
    this layer could have, because it would look like nothing happened.
    """
    from quant_engine.paper.models import PaperSettlement

    live = SessionGuard(
        SessionGuardSettings(
            enabled=True, dailyProfitTarget=500, dailyLossLimit=300, currency="THB"
        )
    )
    at = fixtures.BASE_MS
    live.apply_settlement(
        PaperSettlement(
            tradeId=fixtures.identity("live-settlement"),
            platform="capitalbear",
            assetName="EUR/USD OTC",
            settledAt=at,
            outcome="WIN",
            currency="THB",
            stake=50,
            payoutRate=0.82,
            realizedPnl=41.0,
            paperVersion="qst-paper-v1",
        )
    )
    before = live.state(at).model_dump(mode="json")
    assert before["session"]["realizedPnl"] == 41.0

    scenario = SessionGuardSettings(
        enabled=True, dailyProfitTarget=41.0, currency="THB", timezone="UTC"
    )
    started = replay(tmp_path / "start", sessionGuardScenario=scenario)
    assert started.run.paperResolved > 0, "the replay must really have settled something"
    assert started.sessions, "the sandbox must really have kept a trading day"
    assert live.state(at).model_dump(mode="json") == before

    cancelled = ReplayEngine(
        manifest(sessionGuardScenario=scenario),
        InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=("capitalbear",)
        ),
        root=tmp_path / "cancel",
        persist=False,
        cancelled=lambda: True,
    ).run()
    assert cancelled.run.status == "CANCELLED"
    assert live.state(at).model_dump(mode="json") == before


def test_a_sandbox_settlement_never_reaches_the_live_session_record(tmp_path: Path) -> None:
    # The sandbox keeps a real trading day with real transitions — and writes every one of them
    # under the run's own namespace. Nothing lands where the live guard would read it back.
    seed_live_record(tmp_path)
    report = run_replay(
        manifest(
            sessionGuardScenario=SessionGuardSettings(
                enabled=True, dailyLossLimit=100.0, currency="THB", timezone="UTC"
            )
        ),
        market_data=tmp_path,
        factory=lambda spec: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        persist=True,
    )
    storage = ParquetStorage(tmp_path)
    assert storage.reload("daily_sessions") == []
    assert storage.reload("session_guard_events") == []
    sandbox = ParquetStorage(replay_root(tmp_path) / str(report.run.replayRunId) / "market")
    assert sandbox.reload("daily_sessions"), "the sandbox day must exist under the run"


def test_the_sandbox_guard_is_a_different_object_from_any_live_one(tmp_path: Path) -> None:
    first = replay(tmp_path / "a", sessionGuardScenario=SessionGuardSettings(enabled=True))
    second = replay(tmp_path / "b", sessionGuardScenario=SessionGuardSettings(enabled=True))
    assert first.guard is not second.guard


# --- T-ES nothing upstream moves -------------------------------------------------------


def test_a_full_replay_changes_no_phase_six_to_ten_contract(tmp_path: Path) -> None:
    from quant_engine.analytics.models import ANALYTICS_VERSION, MIN_RECOMMENDATION_SAMPLE
    from quant_engine.features.models import FEATURE_VERSION
    from quant_engine.opportunity.models import RANKING_VERSION
    from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN, MIN_SELECTION_SCORE
    from quant_engine.paper.policy import MAX_OPEN_PER_PLATFORM, PAPER_DURATION_MS
    from quant_engine.strategy.policy import BASE_WEIGHTS

    before = (
        FEATURE_VERSION,
        RANKING_VERSION,
        ANALYTICS_VERSION,
        MIN_SELECTION_SCORE,
        MIN_LEAD_MARGIN,
        MIN_RECOMMENDATION_SAMPLE,
        MAX_OPEN_PER_PLATFORM,
        dict(BASE_WEIGHTS),
        dict(PAPER_DURATION_MS),
    )
    result = replay(tmp_path)
    assert result.run.paperResolved > 0, "the replay must actually have done something"
    from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN as lead_after
    from quant_engine.opportunity.scoring import MIN_SELECTION_SCORE as score_after

    assert (score_after, lead_after) == (before[3], before[4])
    assert dict(BASE_WEIGHTS) == before[7]
    assert dict(PAPER_DURATION_MS) == before[8]


def test_a_replay_states_on_its_own_record_that_it_changes_nothing(tmp_path: Path) -> None:
    report = run_replay(
        manifest(),
        market_data=tmp_path,
        factory=lambda spec: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        persist=False,
    )
    assert report.run.researchOnly is True
    assert report.run.appliedToLiveExecution is False
    assert report.summary.researchOnly is True
    assert report.summary.appliedToLiveExecution is False
    assert report.evidence.researchOnly is True
    assert report.evidence.appliedToLiveExecution is False


# --- T-ET the evidence has no consumer -------------------------------------------------


def test_nothing_in_this_application_reads_replay_evidence() -> None:
    # Persisting it without a consumer is the point: Phase 12 is the first phase allowed to
    # consider it, and it inherits evidence instead of starting from an empty table.
    searched = [
        path
        for folder in ("services/quant-engine/src", "apps/desktop", "packages")
        for path in (REPOSITORY / folder).rglob("*")
        if path.is_file()
        and path.suffix in (".py", ".ts", ".tsx")
        and "node_modules" not in path.parts
        and "out" not in path.parts
    ]
    assert len(searched) > 50, "the scan must actually be reading the repository"
    mentions = set()
    for path in searched:
        text = path.read_text()
        if path.suffix == ".py":
            # Through the AST, so the prose explaining that nothing consumes the evidence cannot
            # itself count as a consumer — and cannot hide a real one either.
            names = identifiers(ast.parse(text))
            names |= {
                alias.name
                for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.ImportFrom | ast.Import)
                for alias in node.names
            }
            if "replayevidence" in {name.casefold() for name in names}:
                mentions.add(str(path.relative_to(REPOSITORY)))
        elif "ReplayEvidence" in text or "evidence.json" in text:
            mentions.add(str(path.relative_to(REPOSITORY)))
    # Only the module that declares it, the ones that build and persist it, and the export list.
    assert mentions <= {
        "services/quant-engine/src/quant_engine/replay/models.py",
        "services/quant-engine/src/quant_engine/replay/report.py",
        "services/quant-engine/src/quant_engine/replay/repository.py",
        "services/quant-engine/src/quant_engine/replay/service.py",
        "services/quant-engine/src/quant_engine/replay/__init__.py",
    }, mentions


def test_the_evidence_model_says_on_its_face_that_it_is_not_applied() -> None:
    fields = ReplayEvidence.model_fields
    assert fields["researchOnly"].default is True
    assert fields["appliedToLiveExecution"].default is False


# --- T-FC the serialized contract did not change ---------------------------------------


def test_a_result_written_before_this_hardening_still_loads_unchanged(tmp_path: Path) -> None:
    """Why ``qst-replay-v1`` is still ``qst-replay-v1``.

    The hardening added two reported fields — whether a job was cancelled part-way, and how many
    stored files would not open — and one in-flight distinction that is never serialized at all.
    Every one of them is additive with a default, so a result written by the previous build reads
    back under the current model and means exactly what it meant when it was written. That is the
    test for whether a version bump is warranted, and it says no: bumping would orphan every
    stored comparison to buy nothing.

    Built by taking a real summary and deleting the new fields, rather than by hand: a
    hand-written payload would only prove that *this* payload parses.
    """
    from quant_engine.replay.models import ReplaySummary

    report = run_replay(
        manifest(),
        market_data=tmp_path,
        factory=lambda spec: InMemoryObservationSource(
            fixtures.small_history(), mode="SYNTHETIC", platforms=spec.platforms
        ),
        persist=False,
    )
    payload = report.summary.model_dump(mode="json")
    assert payload.pop("partial") is False
    assert payload["dataset"]["diagnostics"].pop("unreadableFiles") == 0

    restored = ReplaySummary.model_validate(payload)
    assert restored.partial is False
    assert restored.dataset.diagnostics.unreadableFiles == 0
    assert restored.replayVersion == "qst-replay-v1"
    # And everything that was already on the record still says the same thing.
    assert restored.model_dump(mode="json") == report.summary.model_dump(mode="json")


def test_the_identity_source_never_reaches_the_stored_record() -> None:
    # The Parquet record is byte-for-byte what it always was, which is the other half of why no
    # version moved: a replay cannot change the shape of the record it reads.
    from quant_engine.market_models import MarketObservation, PriceSample

    assert MarketObservation.model_fields["identitySourceType"].exclude is True
    assert PriceSample.model_fields["identitySourceType"].exclude is True
