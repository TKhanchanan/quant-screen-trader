"""T-CC, T-CD: what the paper layer consumes, and what it could not possibly do.

Two guarantees are enforced here rather than described. Phase 9 reads finished Phase 8
decisions and canonical prices, and reconstructs none of the analysis behind them; and there is
no route from it to a broker at all — no execution name is bound, and nothing capable of
reaching one is imported.

The scan is over names the code binds, reads and calls, so prose describing what the layer does
*not* do cannot fail it and cannot hide a real one either. Unlike Phase 8, a simulated
``stake`` and ``payoutRate`` are legitimate here: they are numbers an operator states so a
directional outcome can be expressed in money. What may never appear is a way to press, size,
arm or read anything belonging to a real broker.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import paper_fixtures as fixtures
from quant_engine.paper import PaperEngine
from quant_engine.paper.engine import PaperUpdate

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "quant_engine" / "paper"
API = PACKAGE.parent / "paper_api.py"

EXECUTION_NAMES = frozenset(
    {
        "sendinputevent",
        "webcontents",
        "webcontentsview",
        "executionmanager",
        "orderexecutor",
        "orderpanel",
        "orderticket",
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
    }
)

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "math",
        "os",
        "types",
        "typing",
        "uuid",
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
        "quant_engine.features.micro",
        "quant_engine.features.momentum",
        "quant_engine.features.noise",
        "quant_engine.features.structure",
        "quant_engine.features.trend",
        "quant_engine.features.volatility",
        "quant_engine.strategy.ensemble",
        "quant_engine.strategy.regime",
        "quant_engine.strategy.engine",
        "quant_engine.strategy.common",
        "quant_engine.strategy.base",
        "quant_engine.strategy.policy",
        "quant_engine.opportunity.scoring",
        "quant_engine.opportunity.engine",
    }
)


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


def bound_names() -> set[str]:
    return {name for path in sources() for name in identifiers(ast.parse(path.read_text()))}


# --- T-CC the input contract -----------------------------------------------------------


def test_the_paper_layer_consumes_a_finished_board_and_a_canonical_price() -> None:
    signature = inspect.signature(PaperEngine.on_board)
    assert signature.parameters["board"].annotation == "OpportunityBoard"
    assert (
        inspect.signature(PaperEngine.on_market_sample).parameters["sample"].annotation
        == "PriceSample"
    )
    engine = PaperEngine()
    board = fixtures.board()
    assert isinstance(engine.on_board(board, fixtures.decision_time(board)), PaperUpdate)


def test_the_package_recomputes_no_feature_regime_strategy_or_ranking() -> None:
    # Phase 9 measures what Phase 6-8 decided. A second definition of an indicator, a regime or
    # a rank score here would let the measurement disagree with the thing being measured.
    assert modules_imported() & ANALYSIS_MODULES == set()
    assert {"quant_engine.opportunity.models"} <= modules_imported()


def test_phase_eight_output_is_read_and_never_rewritten() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    before = board.model_dump(mode="json")
    engine.on_board(board, fixtures.decision_time(board))
    engine.on_market_sample(fixtures.sample(fixtures.decision_time(board), 100.0))
    assert board.model_dump(mode="json") == before


def test_the_lifecycle_never_reads_a_clock() -> None:
    # Every timestamp on a paper outcome must replay identically from the same recorded events.
    # Anything derived from "now" would make the same market produce a different history.
    imported = modules_imported(include_api=False)
    assert "time" not in imported and "datetime" not in imported
    names = bound_names()
    assert names.isdisjoint({"now", "utcnow", "monotonic", "perf_counter", "today"})


def test_the_only_operating_system_facility_used_is_the_environment() -> None:
    used = {
        node.attr
        for path in sources()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    }
    assert used <= {"environ"}


# --- T-CD no execution surface ---------------------------------------------------------


def test_no_name_in_the_paper_layer_refers_to_a_broker_control() -> None:
    # "higher" and "lower" are the broker's two buttons, so the package folds case with
    # ``casefold`` rather than ``lower``: the scan cannot tell a string method from a control,
    # and a boundary test that has to guess is not a boundary test.
    offences = {
        f"{path.name}:{name}"
        for path in sources()
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


def test_the_paper_layer_imports_nothing_that_could_reach_a_broker() -> None:
    roots = {module.split(".")[0] for module in modules_imported()}
    assert roots <= {name.split(".")[0] for name in ALLOWED_IMPORTS}
    assert roots.isdisjoint({"socket", "http", "subprocess", "asyncio", "requests", "httpx"})


def test_directional_words_are_allowed_because_they_are_not_controls() -> None:
    # UP and DOWN describe what the market did. They are not the broker's Higher and Lower
    # buttons, and nothing in this package could reach those even if it wanted to.
    engine = PaperEngine()
    board = fixtures.board(direction="DOWN")
    engine.on_board(board, fixtures.decision_time(board))
    assert engine.live[("capitalbear", 1)].direction == "DOWN"


def test_the_paper_api_exposes_reads_only() -> None:
    source = API.read_text()
    assert "@router.post" not in source
    assert "@router.put" not in source
    assert "@router.delete" not in source
    assert "@router.patch" not in source
    assert source.count("@router.get") == 7


# --- T-AQ a paper result is not an execution ticket ------------------------------------


def test_a_paper_trade_carries_no_order_ticket_field_and_no_confirmation() -> None:
    engine = PaperEngine()
    board = fixtures.board()
    engine.on_board(board, fixtures.decision_time(board))
    fields = set(engine.live[("capitalbear", 1)].model_dump())
    assert fields.isdisjoint({"ticketId", "confirmed", "pressedAt", "verified", "brokerStake"})
    assert fields.isdisjoint({"orderId", "controlId", "armed", "mode"})


def test_confirmed_is_not_a_paper_outcome() -> None:
    # An execution ticket saying CONFIRMED means a broker panel reacted. A paper trade saying
    # WIN means the market moved the way the analysis said. They are never the same statement,
    # and the paper vocabulary cannot express the first one at all.
    from quant_engine.paper.models import PaperOutcome

    values = set(PaperOutcome.__value__.__args__)
    assert values == {"UNRESOLVED", "WIN", "LOSS", "DRAW", "INVALID"}
    assert "CONFIRMED" not in values
