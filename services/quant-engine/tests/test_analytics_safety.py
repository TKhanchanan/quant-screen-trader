"""T-CF, T-CG, T-CH, T-CI: what Phase 10 could not possibly do.

Four guarantees are enforced here rather than described, by scanning the names the code binds,
reads and calls. Prose saying the layer does not press a button cannot pass this file, and prose
cannot hide a real press either.

Phase 10 is the layer with the most obvious temptation attached to it: it is the first one that
can tell which threshold *would have* worked. So the boundary is asserted in both directions —
it cannot reach an execution surface, and it cannot reach the score gates, session limits or
stake it has just finished forming an opinion about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import analytics_fixtures as fixtures
from quant_engine.analytics import AnalyticsEngine, build

ROOT = Path(__file__).resolve().parents[1] / "src" / "quant_engine"
PACKAGE = ROOT / "analytics"
API = ROOT / "analytics_api.py"
REPOSITORY = ROOT / "storage" / "analytics_repository.py"

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
    }
)

MUTATION_NAMES = frozenset(
    {
        "dailyprofittarget",
        "dailylosslimit",
        "canopennewentry",
        "stopsession",
        "updatesettings",
        "savesettings",
        "apply_threshold",
        "applythreshold",
        "minselectionscore",
        "minleadmargin",
        "baseweights",
        "executionsettings",
    }
)
"""Phase 10 may describe a threshold. It may not name the live one, and it may not name the
session limits or the settings writer either: a measurement layer that can spell the setting it
just formed an opinion about is one refactor away from setting it."""

PROBABILITY_NAMES = frozenset({"brier"})
"""Scoring an uncalibrated ordering as if it were a probability would put a number on a claim
nobody has earned. The absence is deliberate, so it is asserted."""

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "hashlib",
        "json",
        "math",
        "random",
        "statistics",
        "datetime",
        "typing",
        "uuid",
        "zoneinfo",
        "pydantic",
        "quant_engine",
    }
)

ENGINE_MODULES = frozenset(
    {
        "quant_engine.features",
        "quant_engine.features.engine",
        "quant_engine.features.math",
        "quant_engine.features.micro",
        "quant_engine.features.momentum",
        "quant_engine.features.noise",
        "quant_engine.features.structure",
        "quant_engine.features.trend",
        "quant_engine.features.volatility",
        "quant_engine.strategy.engine",
        "quant_engine.strategy.ensemble",
        "quant_engine.strategy.regime",
        "quant_engine.strategy.common",
        "quant_engine.strategy.base",
        "quant_engine.strategy.policy",
        "quant_engine.opportunity",
        "quant_engine.opportunity.engine",
        "quant_engine.opportunity.scoring",
        "quant_engine.paper",
        "quant_engine.paper.engine",
        "quant_engine.paper.policy",
        "quant_engine.paper.resolver",
        "quant_engine.paper.accounting",
        "quant_engine.session_guard",
        "quant_engine.session_guard.engine",
        "quant_engine.session_guard.policy",
        "quant_engine.session_guard.accounting",
        "quant_engine.session_guard.settings",
        "quant_engine.session_guard.models",
        "quant_engine.market_api",
        "quant_engine.market_storage",
        "quant_engine.market_builder",
    }
)


def package_sources() -> list[Path]:
    found = sorted(PACKAGE.rglob("*.py"))
    assert len(found) >= 7, "the scan must actually be reading the package"
    return found


def all_sources() -> list[Path]:
    return [*package_sources(), API, REPOSITORY]


def modules_imported(paths: list[Path]) -> set[str]:
    imported: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


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


def bound_names(paths: list[Path]) -> set[str]:
    return {name for path in paths for name in identifiers(ast.parse(path.read_text()))}


# --- T-CH no execution surface ---------------------------------------------------------


def test_no_name_in_the_analytics_layer_refers_to_a_broker_control() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in all_sources()
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


def test_the_analytics_layer_imports_nothing_that_could_reach_a_broker() -> None:
    roots = {module.split(".")[0] for module in modules_imported(package_sources())}
    assert roots <= {name.split(".")[0] for name in ALLOWED_IMPORTS}
    assert roots.isdisjoint({"socket", "http", "subprocess", "asyncio", "requests", "httpx"})


def test_directional_words_are_allowed_because_they_describe_the_market() -> None:
    # UP and DOWN are what the market did. They are not the broker's two buttons, and nothing
    # in this package could reach those even if it wanted to.
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert snapshot.regimeDirectionMatrix.columns == ["UP", "DOWN"]


# --- T-CG no quant mutation ------------------------------------------------------------


def test_the_package_recomputes_no_feature_regime_strategy_ranking_or_outcome() -> None:
    # Phase 10 measures what Phases 6-9 decided. A second definition of an indicator, a regime,
    # a rank score or an outcome rule here would let the measurement disagree with the thing
    # being measured.
    imported = modules_imported(package_sources())
    assert imported & ENGINE_MODULES == set()
    assert {
        "quant_engine.paper.models",
        "quant_engine.strategy.models",
        "quant_engine.opportunity.models",
    } <= imported


def test_analysing_a_history_does_not_alter_a_single_stored_trade() -> None:
    trades, evaluations = fixtures.corpus()
    before = [trade.model_dump(mode="json") for trade in trades]
    votes_before = [item.model_dump(mode="json") for item in evaluations]
    AnalyticsEngine().analyze(build(trades, evaluations))
    assert [trade.model_dump(mode="json") for trade in trades] == before
    assert [item.model_dump(mode="json") for item in evaluations] == votes_before


def test_the_phase_six_to_nine_contracts_are_untouched_by_a_full_analysis() -> None:
    from quant_engine.features.models import FEATURE_VERSION
    from quant_engine.opportunity.models import RANKING_VERSION
    from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN, MIN_SELECTION_SCORE
    from quant_engine.paper.policy import PAPER_DURATION_MS
    from quant_engine.strategy.policy import BASE_WEIGHTS

    before = (
        FEATURE_VERSION,
        RANKING_VERSION,
        MIN_SELECTION_SCORE,
        MIN_LEAD_MARGIN,
        dict(BASE_WEIGHTS),
        dict(PAPER_DURATION_MS),
    )
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert snapshot.thresholdCandidates, "the analysis must actually have found something"
    from quant_engine.opportunity.scoring import MIN_LEAD_MARGIN as lead_after
    from quant_engine.opportunity.scoring import MIN_SELECTION_SCORE as score_after

    assert (score_after, lead_after) == (before[2], before[3])
    assert dict(BASE_WEIGHTS) == before[4]
    assert dict(PAPER_DURATION_MS) == before[5]


# --- T-CI session guard isolation ------------------------------------------------------


def test_the_analytics_layer_cannot_name_a_session_limit_or_a_settings_writer() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in all_sources()
        for name in identifiers(ast.parse(path.read_text())) & MUTATION_NAMES
    }
    assert offences == set()


def test_a_full_analysis_leaves_the_daily_session_permission_exactly_as_it_was() -> None:
    from quant_engine.session_guard import SessionGuard
    from quant_engine.session_guard.settings import SessionGuardSettings

    guard = SessionGuard(
        SessionGuardSettings(enabled=True, dailyProfitTarget=500, dailyLossLimit=300)
    )
    before = guard.state(fixtures.BASE_MS).model_dump(mode="json")
    AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert guard.state(fixtures.BASE_MS).model_dump(mode="json") == before


# --- T-CF, T-BF nothing is applied -----------------------------------------------------


def test_the_read_api_exposes_reads_only_and_has_no_apply() -> None:
    source = API.read_text()
    for verb in ("@router.post", "@router.put", "@router.delete", "@router.patch"):
        assert verb not in source
    assert source.count("@router.get") == 10
    # Scanned as bound names rather than as text, so the prose explaining that nothing is
    # applied cannot fail the check and cannot disguise a real one either.
    names = identifiers(ast.parse(source))
    assert not {name for name in names if name.startswith("apply")} - {"appliedtoliveexecution"}


def test_a_snapshot_states_on_its_own_record_that_it_changes_nothing() -> None:
    snapshot = AnalyticsEngine().analyze(build(*fixtures.corpus()))
    assert snapshot.researchOnly is True
    assert snapshot.appliedToLiveExecution is False
    assert all(item.appliedToLiveExecution is False for item in snapshot.research)
    assert all(item.appliedToLiveExecution is False for item in snapshot.thresholdCandidates)


# --- the layer never reads a clock and never scores a probability -----------------------


def test_the_analysis_never_reads_a_wall_clock() -> None:
    # Two runs over the same recorded history must produce the same snapshot id, which is
    # impossible if anything in the layer can see what time it is now.
    names = bound_names(package_sources())
    assert names.isdisjoint({"now", "utcnow", "today", "perf_counter", "process_time"})
    # "monotonic" is deliberately not on that list: here it is a property of a calibration
    # curve, and banning the word would ban the diagnostic this whole layer exists to report.
    assert "monotonic" in names
    assert "time" not in modules_imported(package_sources())


def test_there_is_no_brier_score_anywhere_in_the_package() -> None:
    offences = {
        name for path in all_sources() for name in identifiers(ast.parse(path.read_text()))
    } & PROBABILITY_NAMES
    assert offences == set()


def test_the_only_randomness_is_an_explicitly_seeded_bootstrap() -> None:
    # An interval that moved on every refresh would invite rerunning until it looked narrow.
    uses = [
        node
        for path in package_sources()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "random"
    ]
    assert [node.attr for node in uses] == ["Random"]
