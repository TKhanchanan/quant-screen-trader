"""T15-T17, T23-T24: isolation, reset, the live trigger, and the absence of any execution path."""

import ast
from pathlib import Path

import strategy_fixtures as fixtures
from features_fixtures import CONTEXT_A, CONTEXT_B
from quant_engine.features import FeatureEngine
from quant_engine.market_api import MarketEngine
from quant_engine.market_storage import ParquetStorage
from quant_engine.strategy import StrategyEngine, evaluate_bundle
from quant_engine.strategy.engine import HISTORY_CAPACITY
from quant_engine.strategy.models import EnsembleSnapshot

STRATEGY_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "quant_engine" / "strategy"
STRATEGY_API = STRATEGY_PACKAGE.parent / "strategy_api.py"


# --- T15 slot isolation ----------------------------------------------------------------


def test_two_slots_on_one_platform_do_not_share_state() -> None:
    features = FeatureEngine()
    trending = fixtures.bundle(fixtures.trending(), slot=1, engine=features)
    ranging = fixtures.bundle(fixtures.ranging(), slot=2, spread=0.0022, engine=features)
    engine = StrategyEngine()
    first, second = engine.evaluate(trending), engine.evaluate(ranging)
    assert first.regime.primaryRegime == "TREND_UP"
    assert second.regime.primaryRegime == "RANGE"
    assert engine.latest("iqoption", 1) is first
    assert engine.latest("iqoption", 2) is second


def test_a_slot_with_no_evaluation_reports_nothing_rather_than_a_neighbour() -> None:
    engine = StrategyEngine()
    engine.evaluate(fixtures.bundle(fixtures.trending(), slot=1))
    assert engine.latest("iqoption", 3) is None
    assert engine.recent("iqoption", 3, 5) == []


# --- T16 platform isolation ------------------------------------------------------------


def test_the_same_slot_number_on_two_platforms_is_two_independent_readings() -> None:
    engine = StrategyEngine()
    iq = engine.evaluate(fixtures.bundle(fixtures.trending(), platform="iqoption", slot=1))
    micro = FeatureEngine()
    fixtures.feed_seconds(micro, fixtures.rising_seconds())
    capitalbear = engine.evaluate(
        fixtures.bundle(
            fixtures.trending(drift=0.0006), platform="capitalbear", slot=1, engine=micro
        )
    )
    assert iq.primaryTimeframe == "M1" and capitalbear.primaryTimeframe == "S5"
    assert engine.latest("iqoption", 1) is iq
    assert engine.latest("capitalbear", 1) is capitalbear
    # Policy differs, so the same strategy is not worth the same on both.
    assert (
        fixtures.by_id(iq, "micro_impulse_v1").platformFit
        < fixtures.by_id(capitalbear, "micro_impulse_v1").platformFit
    )


# --- T17 reset -------------------------------------------------------------------------


def test_resetting_a_slot_drops_every_opinion_it_held() -> None:
    engine = StrategyEngine()
    engine.evaluate(fixtures.bundle(fixtures.trending()))
    assert engine.reset_slot("iqoption", [1]) == 1
    assert engine.latest("iqoption", 1) is None
    assert engine.diagnostics() == []


def test_a_new_context_starts_a_new_history_rather_than_extending_the_old_one() -> None:
    engine = StrategyEngine()
    first = engine.evaluate(fixtures.bundle(fixtures.trending(), context=CONTEXT_A))
    second = engine.evaluate(fixtures.bundle(fixtures.falling(), context=CONTEXT_B))
    history = engine.recent("iqoption", 1, HISTORY_CAPACITY)
    assert history == [second]
    assert first.contextId != second.contextId


def test_a_reset_between_two_assets_leaves_nothing_of_the_first(tmp_path: Path) -> None:
    market = MarketEngine(ParquetStorage(tmp_path))
    features = FeatureEngine()
    for candle in fixtures.bars(fixtures.trending(), platform="capitalbear", timeframe="S5"):
        features.ingest_candle(candle)
    market.features = features
    bundle = features.latest_bundle("capitalbear", 1)
    assert bundle is not None
    market.strategy.evaluate(bundle)
    assert market.strategy.latest("capitalbear", 1) is not None

    market.reset_slots("capitalbear", [1])
    assert market.strategy.latest("capitalbear", 1) is None
    assert market.features.latest_bundle("capitalbear", 1) is None


# --- T23 the live trigger --------------------------------------------------------------


def test_one_closed_primary_snapshot_produces_exactly_one_ensemble(tmp_path: Path) -> None:
    market = MarketEngine(ParquetStorage(tmp_path))
    for candle in fixtures.bars(fixtures.trending(), platform="capitalbear", timeframe="S5"):
        snapshot = market.features.ingest_candle(candle)
        assert snapshot is not None
        market.evaluate_primary_close(snapshot)
    assert market.strategy.evaluated == len(fixtures.trending())
    assert market.strategy.duplicates == 0
    stored = [category for category, _ in market.storage.pending]
    assert stored.count("ensembles") == market.strategy.evaluated
    assert stored.count("regimes") == market.strategy.evaluated
    ensembles = [
        record
        for category, record in market.storage.pending
        if category == "ensembles" and isinstance(record, EnsembleSnapshot)
    ]
    assert stored.count("strategy_evaluations") == sum(len(item.strategies) for item in ensembles)
    # The opening bars have no history to reason about, so they store a regime and an
    # explicit SKIP and no strategy rows at all rather than six abstentions apiece.
    assert ensembles[0].direction == "SKIP"
    assert {veto.code for veto in ensembles[0].vetoes} == {"INSUFFICIENT_DATA"}
    assert ensembles[0].strategies == []
    assert len(ensembles[-1].strategies) == 6


def test_re_evaluating_the_same_close_does_not_produce_a_second_ensemble() -> None:
    engine = StrategyEngine()
    bundle = fixtures.bundle(fixtures.trending())
    first = engine.evaluate(bundle)
    assert engine.evaluate(bundle) is first
    assert engine.evaluated == 1 and engine.duplicates == 1


def test_a_closed_context_timeframe_does_not_trigger_an_evaluation(tmp_path: Path) -> None:
    # CapitalBear decides on S5. A closed M1 updates the context a later S5 close will read,
    # and produces no ensemble of its own.
    market = MarketEngine(ParquetStorage(tmp_path))
    for candle in fixtures.bars(fixtures.trending(), platform="capitalbear", timeframe="M1"):
        snapshot = market.features.ingest_candle(candle)
        assert snapshot is not None
        market.evaluate_primary_close(snapshot)
    assert market.strategy.evaluated == 0
    assert market.strategy.latest("capitalbear", 1) is None


def test_history_stays_bounded_under_a_long_replay() -> None:
    engine = StrategyEngine()
    features = FeatureEngine()
    closes = fixtures.trending(count=140)
    for candle in fixtures.bars(closes, timeframe="M1"):
        features.ingest_candle(candle)
        bundle = features.latest_bundle("iqoption", 1)
        assert bundle is not None
        engine.evaluate(bundle)
    assert engine.evaluated == len(closes)
    assert len(engine.recent("iqoption", 1, 1000)) == HISTORY_CAPACITY


def test_diagnostics_summarise_the_latest_reading_per_slot() -> None:
    engine = StrategyEngine()
    engine.evaluate(fixtures.bundle(fixtures.trending(), slot=1))
    engine.evaluate(fixtures.bundle(fixtures.ranging(), slot=2, spread=0.0022))
    summary = {item.slotId: item for item in engine.diagnostics()}
    assert summary[1].primaryRegime == "TREND_UP" and summary[1].direction == "UP"
    assert summary[2].primaryRegime == "RANGE"
    assert all(item.evaluations == 6 for item in summary.values())


def test_evaluate_regime_and_evaluate_strategies_match_the_full_evaluation() -> None:
    engine = StrategyEngine()
    bundle = fixtures.bundle(fixtures.trending())
    regime = engine.evaluate_regime(bundle)
    evaluations = engine.evaluate_strategies(bundle, regime)
    combined = evaluate_bundle(bundle)
    assert regime.model_dump() == combined.regime.model_dump()
    assert [item.model_dump() for item in evaluations] == [
        item.model_dump() for item in combined.strategies
    ]


# --- T24 no execution surface ----------------------------------------------------------


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


def sources() -> list[Path]:
    found = [*STRATEGY_PACKAGE.rglob("*.py"), STRATEGY_API]
    assert len(found) >= 13, "the scan must actually be reading the package"
    return found


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


def test_no_name_in_the_strategy_layer_refers_to_an_execution_control() -> None:
    # Phase 7 is analytical. Nothing in it may reach a broker control, choose a stake or send
    # an input event, and this test is what keeps that true as the package grows. The scan is
    # over names the code actually binds and calls, so prose describing what the layer does
    # not do cannot fail it and cannot hide a real one either.
    offences = {
        f"{path.name}:{name}"
        for path in sources()
        for name in identifiers(ast.parse(path.read_text())) & EXECUTION_NAMES
    }
    assert offences == set()


def test_the_strategy_layer_imports_nothing_that_could_reach_a_broker() -> None:
    # A stronger guarantee than a name scan: with no networking, no subprocess and no IPC in
    # scope, there is no route out of this package at all.
    imported: set[str] = set()
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(
                    node.module if node.module in ALLOWED_IMPORTS else node.module.split(".")[0]
                )
    assert imported <= ALLOWED_IMPORTS


def test_the_strategy_api_exposes_reads_only() -> None:
    source = STRATEGY_API.read_text()
    assert "@router.post" not in source
    assert "@router.put" not in source
    assert "@router.delete" not in source
    assert source.count("@router.get") == 3


def test_no_strategy_output_carries_a_size_a_price_or_an_instruction() -> None:
    snapshot = evaluate_bundle(fixtures.bundle(fixtures.trending()))
    fields = set(snapshot.model_dump())
    assert fields.isdisjoint({"stake", "amount", "order", "payout", "expectedValue", "action"})
    assert fields.isdisjoint({"winProbability", "edge", "positionSize"})
