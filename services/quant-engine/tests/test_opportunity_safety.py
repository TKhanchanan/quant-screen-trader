"""T-AZ, T-BA: what the ranking layer consumes, and what it cannot possibly do.

Two guarantees are enforced here rather than described. The layer reads finished Phase 7
opinions and never reconstructs the facts behind them; and there is no route from it to a
broker at all — no execution name is bound, and nothing capable of reaching one is imported.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import opportunity_fixtures as fixtures
from quant_engine.opportunity import OpportunityEngine
from quant_engine.opportunity.engine import Ingestion
from quant_engine.strategy.models import EnsembleSnapshot

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "quant_engine" / "opportunity"
API = PACKAGE.parent / "opportunity_api.py"

EXECUTION_NAMES = frozenset(
    {
        "sendinputevent",
        "webcontents",
        "higher",
        "lower",
        "stake",
        "wager",
        "bankroll",
        "martingale",
        "order",
        "buy",
        "sell",
        "trade",
        "execute",
        "submit",
        "click",
        "press",
        "payout",
        "positionsize",
        "amount",
        "balance",
    }
)

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "types",
        "typing",
        "uuid",
        "fastapi",
        "pydantic",
        "quant_engine",
    }
)

FEATURE_MODULES = frozenset(
    {
        "quant_engine.features",
        "quant_engine.features.engine",
        "quant_engine.features.math",
        "quant_engine.features.models",
        "quant_engine.features.micro",
        "quant_engine.features.momentum",
        "quant_engine.features.noise",
        "quant_engine.features.structure",
        "quant_engine.features.trend",
        "quant_engine.features.volatility",
    }
)

STRATEGY_INTERNALS = frozenset(
    {
        "quant_engine.strategy.ensemble",
        "quant_engine.strategy.regime",
        "quant_engine.strategy.common",
        "quant_engine.strategy.base",
        "quant_engine.strategy.policy",
        "quant_engine.strategy.breakout",
        "quant_engine.strategy.mean_reversion",
        "quant_engine.strategy.micro",
        "quant_engine.strategy.momentum",
        "quant_engine.strategy.pullback",
        "quant_engine.strategy.trend_follow",
    }
)


def sources() -> list[Path]:
    found = [*PACKAGE.rglob("*.py"), API]
    assert len(found) >= 5, "the scan must actually be reading the package"
    return found


def modules_imported() -> set[str]:
    imported: set[str] = set()
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    return imported


def identifiers(tree: ast.AST) -> set[str]:
    """Every name the module actually binds, reads or calls, ignoring prose."""
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


# --- T-AZ the input contract -----------------------------------------------------------


def test_ranking_consumes_a_finished_ensemble_and_nothing_else() -> None:
    signature = inspect.signature(OpportunityEngine.ingest)
    assert signature.parameters["ensemble"].annotation == "EnsembleSnapshot"
    engine = OpportunityEngine()
    result = engine.ingest(fixtures.ensemble(), {1})
    assert isinstance(result, Ingestion)
    assert isinstance(fixtures.ensemble(), EnsembleSnapshot)


def test_the_package_imports_no_feature_or_indicator_module() -> None:
    # Phase 8 compares opinions. Recomputing an EMA, an ATR or a candle here would mean two
    # definitions of the same fact could disagree, and Phase 6 would no longer be the source.
    assert modules_imported() & FEATURE_MODULES == set()


def test_the_package_holds_no_copy_of_a_phase_seven_strategy_or_regime() -> None:
    # Only the Phase 7 wire models are read. The classifier, the catalog, the platform policy
    # and the ensemble arithmetic all stay on the other side of the contract.
    assert modules_imported() & STRATEGY_INTERNALS == set()
    assert {"quant_engine.strategy.models"} <= modules_imported()


def test_no_raw_observation_price_or_candle_reaches_the_ranking_layer() -> None:
    forbidden = {"marketobservation", "candle", "pricesample", "featurebundle", "featuresnapshot"}
    bound = {name for path in sources() for name in identifiers(ast.parse(path.read_text()))}
    assert bound & forbidden == set()


def test_the_score_never_reads_a_clock() -> None:
    # F1: a stored rankScore must replay identically. Anything derived from "now" would make
    # the same inputs produce a different board a second later.
    imported = modules_imported()
    assert "time" not in imported and "datetime" not in imported
    bound = {name for path in sources() for name in identifiers(ast.parse(path.read_text()))}
    assert bound.isdisjoint({"now", "utcnow", "monotonic", "perf_counter"})


# --- T-BA no execution surface ---------------------------------------------------------


def test_no_name_in_the_ranking_layer_refers_to_an_execution_control() -> None:
    # Phase 8 orders analysis. Nothing in it may reach a broker control, choose a stake or
    # send an input event, and this test is what keeps that true as the package grows. The
    # scan is over names the code binds and calls, so prose describing what the layer does
    # not do cannot fail it and cannot hide a real one either.
    offences = {
        f"{path.name}:{name}"
        for path in sources()
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


def test_the_ranking_layer_imports_nothing_that_could_reach_a_broker() -> None:
    roots = {module.split(".")[0] for module in modules_imported()}
    assert roots <= {name.split(".")[0] for name in ALLOWED_IMPORTS}
    assert roots.isdisjoint({"socket", "http", "subprocess", "asyncio", "requests", "httpx"})


def test_the_ranking_api_exposes_reads_only() -> None:
    source = API.read_text()
    assert "@router.post" not in source
    assert "@router.put" not in source
    assert "@router.delete" not in source
    assert "@router.patch" not in source
    assert source.count("@router.get") == 3


def test_no_ranking_output_carries_a_size_a_payout_or_an_instruction() -> None:
    engine = OpportunityEngine()
    result = engine.ingest(fixtures.ensemble(), {1})
    assert result is not None
    for values in (result.board.model_dump(), result.board.candidates[0].model_dump()):
        fields = set(values)
        assert fields.isdisjoint({"stake", "amount", "order", "payout", "expectedValue"})
        assert fields.isdisjoint({"action", "winProbability", "edge", "positionSize"})
        assert fields.isdisjoint({"bankroll", "martingale", "entry", "exit"})


def test_the_word_selected_names_an_analysis_and_never_an_instruction() -> None:
    # "Selected" is the top analytical candidate. The board carries no verb that could be
    # mistaken for one, and nothing downstream is handed anything but a score and a slot.
    engine = OpportunityEngine()
    result = engine.ingest(fixtures.ensemble(confidence=0.75), {1})
    assert result is not None
    board = result.board
    assert board.selectedSlotId == 1
    assert set(board.model_dump()) & {"selectedStake", "selectedAction", "selectedOrder"} == set()
    assert board.selectedScore is not None and 0 <= board.selectedScore <= 1
