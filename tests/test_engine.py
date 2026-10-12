from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.engine import (
    COLS,
    allocate,
    fundamental_score,
    monthly_returns,
    optimize,
    risk_contributions,
    update_prices,
)


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


def test_fundamental_score_is_relative_and_requires_three_components():
    ratios = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "period": ["2025-12-31"] * 3,
            "roe": [0.20, 0.10, np.nan],
            "roa_ebit": [0.14, 0.07, np.nan],
            "gaf": [1.10, 2.50, 1.00],
            "gao": [1.05, 3.00, np.nan],
            "ki": [0.04, np.nan, np.nan],
            "ki_interest_proxy": [0.05, 0.12, np.nan],
        }
    )

    scores = fundamental_score(ratios, ["A", "B", "C", "D"], min_components=3).set_index("ticker")

    assert scores.loc["A", "fundamental_score"] > scores.loc["B", "fundamental_score"]
    assert scores.loc["A", "ki_effective"] == pytest.approx(0.04)
    assert scores.loc["B", "ki_effective"] == pytest.approx(0.12)
    assert np.isnan(scores.loc["C", "fundamental_score"])
    assert scores.loc["D", "fundamental_coverage"] == 0


def test_risk_contributions_reconcile_to_total_volatility():
    names = ["A", "B"]
    weights = pd.Series([0.60, 0.40], index=names)
    cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=names, columns=names)

    risk = risk_contributions(weights, cov)

    assert risk["risk_contribution_pct"].sum() == pytest.approx(1.0)
    portfolio_volatility = np.sqrt(weights.to_numpy() @ cov.to_numpy() @ weights.to_numpy())
    assert risk["component_risk_month"].sum() == pytest.approx(portfolio_volatility)

