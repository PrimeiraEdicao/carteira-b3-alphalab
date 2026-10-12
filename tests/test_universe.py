from __future__ import annotations

import pandas as pd
import pytest

from src.universe_b3 import discover_catalog, select_ranked


def test_discover_catalog_checks_pagination_and_filters_non_equities():
    pages = {
        1: {
            "stocks": [
                {"stock": "AAAA3", "name": "A", "type": "stock", "subType": "stock", "sector": "Finance"},
                {"stock": "BBBB11", "name": "B", "type": "stock", "subType": "unit", "sector": "Utilities"},
            ],
            "totalCount": 3,
            "totalPages": 2,
            "currentPage": 1,
            "hasNextPage": True,
        },
        2: {
            "stocks": [
                {"stock": "CCCC11", "name": "FII", "type": "fund", "subType": "reit", "sector": "Finance"}
            ],
            "totalCount": 3,
            "totalPages": 2,
            "currentPage": 2,
            "hasNextPage": False,
        },
    }

    universe, meta = discover_catalog(lambda page, _limit: pages[page])

    assert universe["ticker"].tolist() == ["AAAA3.SA", "BBBB11.SA"]
    assert meta["returned_count"] == 3
    assert meta["excluded_other_types"] == 1


def test_discover_catalog_rejects_incomplete_page_count():
    def incomplete(_page, _limit):
        return {
            "stocks": [{"stock": "AAAA3", "type": "stock"}],
            "totalCount": 2,
            "totalPages": 1,
            "currentPage": 1,
            "hasNextPage": False,
        }

    with pytest.raises(ValueError, match="catálogo incompleto"):
        discover_catalog(incomplete)


def test_rank_selection_returns_feasible_group_mix():
    eligible = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D"],
            "segment": ["X", "X", "Y", "Y"],
            "sector_pt": ["S1", "S1", "S2", "S2"],
            "rank": [1, 2, 3, 4],
        }
    )

    selected = select_ranked(eligible, max_assets=4, max_asset=0.40, max_segment=0.60)

    assert 3 <= len(selected) <= 4
    assert set(selected["segment"]) == {"X", "Y"}
    assert selected["selection_solver_optimal"].all()

