"""Motor de seleção e alocação da Carteira B3 AlphaLab."""

from .engine import run_portfolio
from .cvm_fundamentals import update_cvm_fundamentals
from .universe_b3 import discover_catalog, run_b3

__all__ = ["discover_catalog", "run_b3", "run_portfolio", "update_cvm_fundamentals"]

