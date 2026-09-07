from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from quant_engine.app import create_app
from quant_engine.market_api import MarketEngine, ObservationBatch
from quant_engine.market_builder import TimeSeriesBuilder
from quant_engine.market_models import DataQuality, MarketObservation, price_sample
from quant_engine.market_storage import ParquetStorage

BASE = datetime(2026, 9, 7, 12, 30, tzinfo=UTC)
CONTEXT = UUID("11111111-1111-4111-8111-111111111111")


def observation(second: float, price: object = 1.2, **changes: object) -> MarketObservation:
    stamp = BASE + timedelta(seconds=second)
    values: dict[str, object] = dict(
        id=uuid4(),
        platform="capitalbear",
        slotId=1,
        assetName="EUR/USD OTC",
        contextId=CONTEXT,
        observedAt=stamp,
        parsedAt=stamp,
        sourceType="SYNTHETIC",
        price=price,
        payout=0.82,
        timerSeconds=5,
        parserConfidence=0.95,
        captureLatencyMs=0,
        parseLatencyMs=0,
        calibrationProfileId=None,
        parserVersion="fixture/1",
        dataQuality=DataQuality(
            state="GOOD",
            confidence=0.95,
            freshness=1,
            completeness=1,
            sourceReliability=1,
            latencyMs=0,
        ),
    )
    values.update(changes)
    return MarketObservation.model_validate(values)


def test_contract_and_quality_gate() -> None:
    assert price_sample(observation(0)) is not None
    for change in [
        {"price": None},
        {"parserConfidence": 0.79},
        {"dataQuality": observation(0).dataQuality.model_copy(update={"state": "UNCERTAIN"})},
    ]:
        assert price_sample(observation(0, **cast(dict[str, object], change))) is None
    for change in [
        {"cookie": "secret"},
        {"price": float("nan")},
        {"slotId": 10},
        {"timerSeconds": 1.5},
        {"parsedAt": BASE - timedelta(seconds=1)},
    ]:
        with pytest.raises(ValidationError):
            observation(0, **cast(dict[str, object], change))


def test_latest_second_and_ohlc_preserves_intrasecond_extrema() -> None:
    builder = TimeSeriesBuilder()
    for t, price in [(0, 2), (0.1, 10), (0.9, 3), (1, 4), (4.9, 1)]:
        builder.ingest(observation(t, price))
    assert builder.seconds[0].price == 3
    assert not builder.candles
    candle = builder.forming["S5"]
    assert (candle.open, candle.high, candle.low, candle.close) == (2, 10, 1, 1)
    assert candle.state == "FORMING"
    builder.advance(int(BASE.timestamp() * 1000) + 5000)
    assert builder.candles[-1].state == "CLOSED"
    assert builder.candles[-1].coverage == 0.6
    assert builder.candles[-1].gapDurationMs == 2000


@pytest.mark.parametrize("tf,duration", [("S5", 5), ("M1", 60), ("M5", 300), ("M10", 600)])
def test_aligned_no_lookahead(tf: str, duration: int) -> None:
    builder = TimeSeriesBuilder()
    for second in range(duration):
        builder.ingest(observation(second, second + 1))
    assert all(c.timeframe != tf for c in builder.candles)
    builder.ingest(observation(duration, 9999))
    candle = next(c for c in builder.candles if c.timeframe == tf)
    assert candle.openTime == int(BASE.timestamp() * 1000)
    assert candle.closeTime == int(BASE.timestamp() * 1000) + duration * 1000
    assert candle.close == duration
    assert candle.high == duration
    assert candle.coverage == 1
    assert candle.quality == "GOOD"
    assert candle.sampleCount == duration


def test_duplicate_out_of_order_future_watermark() -> None:
    builder = TimeSeriesBuilder()
    assert builder.ingest(observation(1, 5))
    assert builder.ingest(observation(1, 999)) is None
    assert builder.ingest(observation(0.5, 999)) is None
    builder.advance(int(BASE.timestamp() * 1000) + 60000)
    assert builder.ingest(observation(2, 999)) is None
    assert builder.candles[0].close == 5
    assert builder.rejected == 3


@pytest.mark.parametrize(
    "change",
    [
        {"assetName": "GBP/USD"},
        {"contextId": uuid4()},
        {"calibrationProfileId": uuid4()},
        {"sourceType": "REPLAY"},
    ],
)
def test_context_boundaries(change: dict[str, object]) -> None:
    builder = TimeSeriesBuilder()
    builder.ingest(observation(0, 100))
    builder.ingest(observation(1, 2, **change))
    assert len(builder.samples) == 1
    assert builder.forming["M1"].high == 2


def test_bounded_buffers_gaps_and_uncertainty() -> None:
    builder = TimeSeriesBuilder(capacity=5)
    for second in range(100):
        builder.ingest(observation(second))
        builder.drain()
    assert len(builder.samples) == len(builder.seconds) == len(builder.candles) == 5
    builder.ingest(observation(120))
    assert builder.missingSeconds == 20
    assert builder.ingest(observation(121, parserConfidence=0.1)) is None
    assert builder.forming["M1"].sampleCount == 1


def test_parquet_roundtrip_and_retention(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path, batch_size=2)
    record = observation(0, assetName="../../EUR/USD OTC")
    sample = price_sample(record)
    assert sample is not None
    storage.append("observations", record)
    storage.append("samples", sample)
    storage.append("seconds", sample)
    storage.flush()
    assert storage.reload("observations") == [record]
    assert storage.reload("samples") == [sample]
    assert storage.reload("seconds") == [sample]
    assert storage.retention() == []
    candidates = storage.retention(datetime.now(UTC) + timedelta(days=1))
    assert len(candidates) == 3
    assert all(p.is_relative_to(tmp_path) and p.exists() for p in candidates)


def test_replay_equivalence_and_isolation(tmp_path: Path) -> None:
    live, replay = TimeSeriesBuilder(), TimeSeriesBuilder()
    for second in range(601):
        live.ingest(observation(second))
        replay.ingest(observation(second, sourceType="REPLAY"))
    excluded = {"sourceType"}
    assert [c.model_dump(exclude=excluded) for c in live.candles] == [
        c.model_dump(exclude=excluded) for c in replay.candles
    ]
    engine = MarketEngine(ParquetStorage(tmp_path))
    batch = ObservationBatch(
        observations=[observation(0), observation(0, platform="iqoption"), observation(0, slotId=2)]
    )
    assert engine.ingest(batch, 0) == 3
    assert len(engine.builders) == 3


def test_market_api_batch_validation_and_browser_rejection(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        body = {"observations": [observation(0).model_dump(mode="json")]}
        assert client.post("/api/market/observations", json=body).json()["accepted"] == 1
        assert (
            client.post(
                "/api/market/observations", json=body, headers={"origin": "https://capitalbear.com"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/market/observations", json={"observations": body["observations"] * 19}
            ).status_code
            == 422
        )
        assert client.get("/api/market/state").json()["slots"][0]["slotId"] == 1
        app.state.market.busy = True
        assert client.post("/api/market/observations", json=body).status_code == 429
        app.state.market.busy = False


def test_degraded_quality_is_not_erased() -> None:
    builder = TimeSeriesBuilder()
    for second in range(6):
        quality = observation(0).dataQuality.model_copy(
            update={"state": "DEGRADED" if second == 0 else "GOOD"}
        )
        builder.ingest(observation(second, dataQuality=quality))
    assert builder.candles[0].coverage == 1
    assert builder.candles[0].quality == "DEGRADED"


def test_partial_candle_parquet_and_null_fields(tmp_path: Path) -> None:
    storage = ParquetStorage(tmp_path)
    builder = TimeSeriesBuilder()
    raw = observation(0, payout=None, timerSeconds=None)
    builder.ingest(raw)
    candle = builder.forming["M1"]
    storage.append("observations", raw)
    storage.append("candles", candle)
    storage.flush()
    assert storage.reload("candles") == [candle]
    assert storage.reload("observations") == [raw]


def test_live_future_stale_rejection_and_tail_closure(tmp_path: Path) -> None:
    engine = MarketEngine(ParquetStorage(tmp_path))
    now = int(BASE.timestamp() * 1000)
    assert (
        engine.ingest(ObservationBatch(observations=[observation(1, sourceType="DOM")]), now) == 0
    )
    assert (
        engine.ingest(ObservationBatch(observations=[observation(-4, sourceType="DOM")]), now) == 0
    )
    assert (
        engine.ingest(ObservationBatch(observations=[observation(0, sourceType="DOM")]), now) == 1
    )
    engine.advance_live(now + 8000)
    assert engine.builders[("capitalbear", 1)].candles[0].state == "CLOSED"
