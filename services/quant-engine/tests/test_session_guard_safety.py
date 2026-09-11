"""T-CZ, T-DA, T-AT: what the guard consumes, and what it could not possibly do.

Phase 9.5 is a stop system. The two things it must never become are a position-sizing system
and a second opinion about the market, and both are enforced here rather than described: the
package binds no recovery or escalation name, and it imports nothing that scores, ranks or
directs anything.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import session_guard_fixtures as fixtures
from quant_engine.session_guard import SessionGuard
from quant_engine.session_guard.engine import GuardUpdate

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "quant_engine" / "session_guard"
API = PACKAGE.parent / "session_guard_api.py"

RECOVERY_NAMES = frozenset(
    {
        "martingale",
        "stakemultiplier",
        "lossrecovery",
        "doubleafterloss",
        "chaseloss",
        "increasestake",
        "recoverloss",
        "stakestep",
        "positionsize",
        "sizeup",
        "compounding",
        "rankscore",
        "confidence",
        "threshold",
        "direction",
        "slotid",
        "strategy",
        "regime",
    }
)
"""Two families in one scan. The first is every way a session layer could start deciding *how
much* to risk, which is the failure mode that turns a loss limit into a loss engine. The second
is every way it could start deciding *what* to trade, which belongs to Phase 6-8 and to nothing
here. ``outcome``, ``currency`` and ``realizedPnl`` are what a stop system is allowed to know."""

EXECUTION_NAMES = frozenset(
    {
        "sendinputevent",
        "webcontents",
        "executionmanager",
        "orderexecutor",
        "orderpanel",
        "presspoint",
        "press",
        "click",
        "arm",
        "disarm",
        "armed",
        "higher",
        "lower",
        "buy",
        "sell",
        "wallet",
        "balance",
        "bankroll",
    }
)

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "datetime",
        "json",
        "math",
        "pathlib",
        "sqlite3",
        "time",
        "typing",
        "uuid",
        "zoneinfo",
        "fastapi",
        "pydantic",
        "quant_engine",
    }
)

ANALYSIS_MODULES = frozenset(
    {
        "quant_engine.features",
        "quant_engine.features.engine",
        "quant_engine.features.math",
        "quant_engine.features.models",
        "quant_engine.strategy",
        "quant_engine.strategy.engine",
        "quant_engine.strategy.ensemble",
        "quant_engine.strategy.regime",
        "quant_engine.strategy.models",
        "quant_engine.opportunity",
        "quant_engine.opportunity.engine",
        "quant_engine.opportunity.models",
        "quant_engine.opportunity.scoring",
        "quant_engine.paper.engine",
        "quant_engine.paper.resolver",
    }
)
"""Not one of these. The guard reads a settled outcome — ``quant_engine.paper.models`` and
nothing else from Phase 9 — so it cannot re-derive an opinion about a market, and cannot reach
the layer that priced the trade either."""


def sources(include_api: bool = True) -> list[Path]:
    found = [*PACKAGE.rglob("*.py")]
    if include_api:
        found.append(API)
    assert len(found) >= 6, "the scan must actually be reading the package"
    return found


def modules_imported(include_api: bool = True) -> set[str]:
    imported: set[str] = set()
    for path in sources(include_api):
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
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg.lower())
    return names


def bound_names(include_api: bool = True) -> set[str]:
    return {
        name for path in sources(include_api) for name in identifiers(ast.parse(path.read_text()))
    }


# --- T-CZ no recovery, no escalation ---------------------------------------------------


def test_nothing_in_the_guard_can_change_how_much_is_risked() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in sources()
        for name in identifiers(ast.parse(path.read_text())) & RECOVERY_NAMES
    }
    assert offences == set()


def test_the_guard_output_carries_no_size_direction_or_score() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    engine.apply_settlement(fixtures.settlement(620))
    assert engine.current is not None
    fields = set(engine.current.model_dump())
    assert fields.isdisjoint({"stake", "nextStake", "positionSize", "multiplier"})
    assert fields.isdisjoint({"direction", "slotId", "rankScore", "assetName", "platform"})
    state = engine.state(fixtures.at(12, 1)).model_dump()
    assert set(state).isdisjoint({"direction", "rankScore", "slotId", "stake"})


def test_a_losing_run_changes_nothing_but_the_permission() -> None:
    # The one behaviour this layer must never have: reacting to losses with anything other
    # than stopping.
    engine = fixtures.guard(dailyLossLimit=300)
    fixtures.feed(engine, [-50, -50, -50, -50])
    assert engine.current is not None
    before = engine.current.model_dump()
    fixtures.feed(engine, [-150], start=fixtures.at(13))
    after = engine.current.model_dump()
    changed = {key for key in after if after[key] != before.get(key)}
    assert changed <= {
        "realizedPnl",
        "grossLoss",
        "losses",
        "resolvedTrades",
        "monetaryTrades",
        "largestLoss",
        "troughRealizedPnl",
        "maxRealizedDrawdown",
        "status",
        "stopReason",
        "canOpenNewEntry",
        "blockReason",
        "lossLimitReachedAt",
        "notifiedLossLimit",
        "revision",
    }, changed


# --- T-DA, T-AT the quant layers are not here ------------------------------------------


def test_the_guard_consumes_a_settled_outcome_and_nothing_else() -> None:
    signature = inspect.signature(SessionGuard.apply_settlement)
    assert signature.parameters["settlement"].annotation == "PaperSettlement"
    engine = fixtures.guard()
    assert isinstance(engine.apply_settlement(fixtures.settlement(10)), GuardUpdate)


def test_the_package_imports_no_feature_regime_strategy_or_ranking_module() -> None:
    assert modules_imported() & ANALYSIS_MODULES == set()
    assert {"quant_engine.paper.models"} <= modules_imported()


def test_the_package_imports_nothing_that_could_reach_a_broker() -> None:
    roots = {module.split(".")[0] for module in modules_imported()}
    assert roots <= {name.split(".")[0] for name in ALLOWED_IMPORTS}
    assert roots.isdisjoint({"socket", "http", "subprocess", "requests", "httpx"})


def test_no_name_in_the_guard_refers_to_a_broker_control() -> None:
    offences = {
        f"{path.name}:{name}"
        for path in sources()
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


# --- T-AQ the accounting clock ---------------------------------------------------------


def test_the_accounting_never_reads_a_clock_of_its_own() -> None:
    # Which session a settlement belongs to comes from its own settledAt. A guard that asked
    # the machine what time it was would put the same replayed day in different places.
    names = bound_names(include_api=False)
    assert names.isdisjoint({"now", "utcnow", "today", "monotonic", "perf_counter"})
    assert "time" not in modules_imported(include_api=False)


def test_reading_the_state_never_changes_it() -> None:
    engine = fixtures.guard(dailyProfitTarget=600)
    engine.apply_settlement(fixtures.settlement(120))
    assert engine.current is not None
    before = engine.current.model_dump(mode="json")
    for hour in range(13, 20):
        engine.state(fixtures.at(hour))
    assert engine.current.model_dump(mode="json") == before


def test_the_api_writes_only_through_one_explicit_command(t_api: None = None) -> None:
    source = API.read_text()
    assert source.count("@router.post") == 1
    assert "@router.put" not in source
    assert "@router.delete" not in source
    assert "@router.patch" not in source
    assert source.count("@router.get") == 5
