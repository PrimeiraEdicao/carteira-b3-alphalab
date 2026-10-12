from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.engine import COLS, allocate, monthly_returns, optimize, update_prices


def price_frame(ticker: str, dates: list[str], prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "ticker": ticker,
            "close": prices,
            "adj_close": prices,
            "volume": np.repeat(1_000, len(dates)),
            "dividends": np.zeros(len(dates)),
            "splits": np.zeros(len(dates)),
        }
    )[COLS]


def test_update_prices_preserves_previous_series_when_fetch_fails(tmp_path):
    path = tmp_path / "historico.csv"
    original = price_frame("AAAA3.SA", ["2025-01-02", "2025-01-03"], [10.0, 10.5])
    original.to_csv(path, index=False)

    def failing_fetch(*_args):
        raise RuntimeError("fonte indisponível")

    history, log = update_prices(path, ["AAAA3.SA"], "2025-01-01", "2025-01-10", fetch=failing_fetch)

    pd.testing.assert_frame_equal(history.reset_index(drop=True), original.reset_index(drop=True))
    assert log.loc[0, "status"] == "erro"
    assert "fonte indisponível" in log.loc[0, "message"]


def test_monthly_returns_requires_common_complete_months():
    history = pd.concat(
        [
            price_frame("AAAA3.SA", ["2025-01-31", "2025-02-28", "2025-03-31"], [10, 11, 12]),
            price_frame("BBBB3.SA", ["2025-01-31", "2025-03-31"], [20, 22]),
            price_frame("^BVSP", ["2025-01-31", "2025-02-28", "2025-03-31"], [100, 102, 104]),
        ],
        ignore_index=True,
    )

    with pytest.raises(ValueError, match="meses completos comuns"):
        monthly_returns(history, ["AAAA3.SA", "BBBB3.SA", "^BVSP"], window=2, asof="2025-04-01")


def test_optimize_and_allocate_respect_asset_and_segment_caps():
    names = ["AAAA3.SA", "BBBB3.SA", "CCCC3.SA", "DDDD3.SA"]
    stats = pd.DataFrame(
        {"expected_month": [0.01, 0.012, 0.009, 0.011]}, index=names
    )
    cov = pd.DataFrame(np.eye(4) * 0.01, index=names, columns=names)
    segments = {names[0]: "A", names[1]: "A", names[2]: "B", names[3]: "B"}

    weights = optimize(stats, cov, segments, max_asset=0.30, max_segment=0.60)
    plan, summary = allocate(
        weights,
        pd.Series([10.0, 20.0, 25.0, 40.0], index=names),
        budget=10_000,
        segments=segments,
        max_asset=0.30,
        max_segment=0.60,
    )

    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert weights.max() <= 0.30 + 1e-7
    assert weights.groupby(pd.Series(segments)).sum().max() <= 0.60 + 1e-7
    assert summary["invested"] + summary["cash"] <= 10_000 + 1e-8
    assert plan["invested"].max() <= 3_000 + 0.01

