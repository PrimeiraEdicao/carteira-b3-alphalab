from __future__ import annotations

from io import BytesIO
import zipfile

import numpy as np
import pandas as pd
import pytest

from src.cvm_fundamentals import (
    compute_cvm_indicators,
    cvm_policy_score,
    extract_annual_fundamentals,
    match_cvm_companies,
    read_dfp_archive,
)


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, sep=";").encode("latin1")


def _statement(code_values: dict[str, float], year: int, version: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "CD_CVM": [9512] * len(code_values),
            "DT_REFER": [f"{year}-12-31"] * len(code_values),
            "VERSAO": [version] * len(code_values),
            "ORDEM_EXERC": ["ÚLTIMO"] * len(code_values),
            "CD_CONTA": list(code_values),
            "DS_CONTA": list(code_values),
            "VL_CONTA": list(code_values.values()),
            "ESCALA_MOEDA": ["MIL"] * len(code_values),
        }
    )


def _archive(tmp_path, year: int, revenue: float, ebit: float, net_income: float):
    master = pd.DataFrame(
        {
            "CD_CVM": [9512], "DENOM_CIA": ["PETROLEO BRASILEIRO S.A. PETROBRAS"],
            "DT_REFER": [f"{year}-12-31"], "VERSAO": [1], "DT_RECEB": [f"{year + 1}-03-15"],
        }
    )
    frames = {
        f"dfp_cia_aberta_{year}.csv": master,
        f"dfp_cia_aberta_BPA_con_{year}.csv": _statement(
            {"1": 1000, "1.01": 400, "1.01.01": 100, "1.01.03": 80, "1.01.04": 50}, year
        ),
        f"dfp_cia_aberta_BPP_con_{year}.csv": _statement(
            {"2.01": 250, "2.01.04": 100, "2.02.01": 200, "2.03": 450}, year
        ),
        f"dfp_cia_aberta_DRE_con_{year}.csv": _statement(
            {"3.01": revenue, "3.03": revenue * 0.4, "3.05": ebit, "3.06": -20, "3.11": net_income}, year
        ),
        f"dfp_cia_aberta_DFC_MI_con_{year}.csv": _statement({"6.01": net_income * 1.2}, year),
    }
    path = tmp_path / f"dfp_cia_aberta_{year}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, frame in frames.items():
            archive.writestr(name, _csv(frame))
    return read_dfp_archive(path, year)


def test_read_extract_and_compute_horizontal_vertical_analysis(tmp_path):
    archives = [
        _archive(tmp_path, 2024, revenue=500, ebit=100, net_income=60),
        _archive(tmp_path, 2025, revenue=600, ebit=150, net_income=90),
    ]
    annual = extract_annual_fundamentals(archives)
    raw = annual.assign(
        ticker="PETR4.SA", name="Petrobras", sector_pt="Petróleo, gás e combustíveis",
        segment="Petróleo", company_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
    )
    indicators = compute_cvm_indicators(raw).sort_values("period")
    latest = indicators.iloc[-1]

    assert latest["revenue"] == pytest.approx(600_000)
    assert latest["revenue_growth_yoy"] == pytest.approx(0.20)
    assert latest["ebit_growth_yoy"] == pytest.approx(0.50)
    assert latest["cash_assets"] == pytest.approx(0.10)
    assert latest["debt_assets"] == pytest.approx(0.30)
    assert latest["current_ratio"] == pytest.approx(1.60)
    assert latest["ebit_margin"] == pytest.approx(0.25)
    assert latest["cfo_net_income"] == pytest.approx(1.20)
    assert pd.notna(latest["available_at"])


def test_company_matching_fails_closed_and_accepts_strong_name_match():
    universe = pd.DataFrame(
        {
            "ticker": ["PETR4.SA", "XXXX3.SA"],
            "name": ["Petrobras", "Empresa sem correspondência"],
        }
    )
    companies = pd.DataFrame(
        {
            "cd_cvm": ["9512", "4170"],
            "company_name": ["PETROLEO BRASILEIRO S.A. PETROBRAS", "BANCO BRADESCO S.A."],
        }
    )

    matched = match_cvm_companies(universe, companies).set_index("ticker")

    assert matched.loc["PETR4.SA", "match_ok"]
    assert matched.loc["PETR4.SA", "cd_cvm"] == "9512"
    assert not matched.loc["XXXX3.SA", "match_ok"]
    assert pd.isna(matched.loc["XXXX3.SA", "cd_cvm"])


def test_policy_requires_coverage_and_all_three_pillars():
    rows = []
    for index, ticker in enumerate(["A", "B", "C", "D"]):
        rows.append(
            {
                "ticker": ticker, "period": "2025-12-31", "available_at": "2026-03-01",
                "sector_pt": "Indústria", "company_name": ticker, "cd_cvm": str(index),
                "roe": 0.10 + index * 0.03, "roa_ebit": 0.05 + index * 0.02,
                "roa_net": 0.04 + index * 0.01, "ebit_margin": 0.10 + index * 0.02,
                "net_margin": 0.06 + index * 0.01, "revenue_growth_yoy": 0.02 + index * 0.02,
                "ebit_growth_yoy": 0.01 + index * 0.03,
                "net_income_growth_yoy": 0.01 + index * 0.02,
                "operating_cash_flow_growth_yoy": 0.03 + index * 0.02,
                "current_ratio": 1.0 + index * 0.2, "debt_assets": 0.60 - index * 0.1,
                "equity_assets": 0.30 + index * 0.05, "cfo_net_income": 0.8 + index * 0.1,
            }
        )
    rows.append({"ticker": "E", "period": "2025-12-31", "sector_pt": "Indústria", "roe": 0.30})
    scores = cvm_policy_score(pd.DataFrame(rows), ["A", "B", "C", "D", "E"]).set_index("ticker")

    assert scores.loc["D", "cvm_policy_score"] > scores.loc["A", "cvm_policy_score"]
    assert scores.loc["D", "cvm_policy_coverage"] == 10
    assert np.isnan(scores.loc["E", "cvm_policy_score"])
