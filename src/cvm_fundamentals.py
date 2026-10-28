"""DFP estruturada da CVM: coleta, mapeamento, indicadores e política challenger.

O módulo usa apenas demonstrações consolidadas anuais e preserva ``available_at``
(``DT_RECEB``) para permitir uso point-in-time em um backtest futuro. A política
é opcional; os indicadores descritivos podem ser exibidos sem participar da seleção.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
import re
import time
import unicodedata
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from .engine import save_csv


CVM_DFP_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip"
STATEMENT_MARKERS = {
    "bpa": "BPA_con",
    "bpp": "BPP_con",
    "dre": "DRE_con",
    "dfc_md": "DFC_MD_con",
    "dfc_mi": "DFC_MI_con",
}
ACCOUNT_CODES = {
    "bpa": {"1", "1.01", "1.01.01", "1.01.03", "1.01.04"},
    "bpp": {"2.01", "2.01.04", "2.02.01", "2.03"},
    "dre": {"3.01", "3.03", "3.05", "3.06", "3.09", "3.11"},
    "dfc_md": {"6.01"},
    "dfc_mi": {"6.01"},
}
COMPANY_STOPWORDS = {
    "SA", "S", "A", "CIA", "COMPANHIA", "LTDA", "BRASIL", "HOLDING", "HOLDINGS",
    "PARTICIPACOES", "PARTICIPACAO", "EM", "DE", "DO", "DA", "DOS", "DAS",
    "ON", "PN", "UNIT", "UNT", "ORDINARIA", "PREFERENCIAL",
}


def _normalize_text(value: object) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()


def _company_tokens(value: object) -> list[str]:
    return [token for token in _normalize_text(value).split() if token not in COMPANY_STOPWORDS]


def _numeric(series: pd.Series) -> pd.Series:
    direct = pd.to_numeric(series, errors="coerce")
    missing = direct.isna() & series.notna()
    if not missing.any():
        return direct
    text = series.loc[missing].astype(str).str.strip()
    both = text.str.contains(",", regex=False) & text.str.contains(".", regex=False)
    comma_decimal = both & (text.str.rfind(",") > text.str.rfind("."))
    text.loc[comma_decimal] = text.loc[comma_decimal].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    text.loc[both & ~comma_decimal] = text.loc[both & ~comma_decimal].str.replace(",", "", regex=False)
    only_comma = text.str.contains(",", regex=False) & ~text.str.contains(".", regex=False)
    text.loc[only_comma] = text.loc[only_comma].str.replace(",", ".", regex=False)
    direct.loc[missing] = pd.to_numeric(text, errors="coerce")
    return direct


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AlphaLab-CVM/1.0", "Accept": "application/zip"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read()
            if not payload.startswith(b"PK"):
                raise ValueError("Resposta da CVM não é um ZIP válido")
            return payload
        except Exception as exc:  # pragma: no cover - depende de rede
            last = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Falha ao baixar {url}: {last}")


def _archive_path(folder: Path, year: int) -> Path:
    return folder / "archives" / f"dfp_cia_aberta_{year}.zip"


def get_dfp_archive(folder: str | Path, year: int, fetch_bytes=None, cache_days: int = 7) -> Path:
    """Baixa com cache e escrita atômica. Arquivos antigos também são revalidados."""
    folder = Path(folder)
    path = _archive_path(folder, int(year))
    fresh = path.exists() and (time.time() - path.stat().st_mtime) <= cache_days * 86400
    if fresh:
        return path
    payload = (fetch_bytes or _download_bytes)(CVM_DFP_URL.format(year=int(year)))
    if not payload.startswith(b"PK"):
        raise ValueError(f"DFP {year}: conteúdo inválido")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zip.tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
    return path


def _read_csv_from_zip(
    archive: zipfile.ZipFile, filename: str, columns: set[str] | None = None
) -> pd.DataFrame:
    with archive.open(filename) as handle:
        usecols = (lambda column: column in columns) if columns else None
        return pd.read_csv(handle, sep=";", encoding="latin1", low_memory=False, usecols=usecols)


def _find_member(names: list[str], marker: str, year: int) -> str | None:
    expected = f"dfp_cia_aberta_{marker}_{year}.csv".lower()
    for name in names:
        if Path(name).name.lower() == expected:
            return name
    return next((name for name in names if marker.lower() in Path(name).name.lower()), None)


def _scale_multiplier(series: pd.Series) -> pd.Series:
    normalized = series.fillna("").map(_normalize_text)
    out = pd.Series(1.0, index=series.index)
    out.loc[normalized.str.contains("MILHAO")] = 1_000_000.0
    out.loc[normalized.str.fullmatch(r"MIL|MILHARES?")] = 1_000.0
    return out


def _prepare_statement(frame: pd.DataFrame, statement: str, receipts: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    required = {"CD_CVM", "DT_REFER", "VERSAO", "CD_CONTA", "VL_CONTA"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{statement}: colunas CVM ausentes: {sorted(required - set(frame.columns))}")
    data = frame.copy()
    if "ORDEM_EXERC" in data:
        order = data["ORDEM_EXERC"].map(_normalize_text)
        latest = order.eq("ULTIMO")
        if latest.any():
            data = data[latest].copy()
    data["CD_CVM"] = data["CD_CVM"].astype(str).str.replace(r"\.0$", "", regex=True)
    data["DT_REFER"] = pd.to_datetime(data["DT_REFER"], errors="coerce")
    data["VERSAO"] = _numeric(data["VERSAO"])
    data["VL_CONTA"] = _numeric(data["VL_CONTA"])
    if "ESCALA_MOEDA" in data:
        data["VL_CONTA"] *= _scale_multiplier(data["ESCALA_MOEDA"])
    data["CD_CONTA"] = data["CD_CONTA"].astype(str).str.strip()
    data["statement"] = statement
    keys = ["CD_CVM", "DT_REFER", "VERSAO"]
    if not receipts.empty:
        data = data.merge(receipts, on=keys, how="left")
    else:
        data["available_at"] = pd.NaT
    return data.dropna(subset=["CD_CVM", "DT_REFER", "CD_CONTA", "VL_CONTA"])


def read_dfp_archive(path: str | Path, year: int | None = None) -> dict[str, pd.DataFrame]:
    """Lê somente tabelas necessárias e conserva a última versão recebida."""
    path = Path(path)
    year = int(year or re.search(r"(20\d{2})", path.name).group(1))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        master_name = next(
            (name for name in names if Path(name).name.lower() == f"dfp_cia_aberta_{year}.csv"),
            None,
        )
        master_columns = {"CD_CVM", "DENOM_CIA", "DT_REFER", "VERSAO", "DT_RECEB", "DT_ENTREGA", "DT_APRESENTACAO"}
        master = _read_csv_from_zip(archive, master_name, master_columns) if master_name else pd.DataFrame()
        receipts = pd.DataFrame()
        if not master.empty and {"CD_CVM", "DT_REFER", "VERSAO"}.issubset(master):
            master["CD_CVM"] = master["CD_CVM"].astype(str).str.replace(r"\.0$", "", regex=True)
            master["DT_REFER"] = pd.to_datetime(master["DT_REFER"], errors="coerce")
            master["VERSAO"] = _numeric(master["VERSAO"])
            received = next((c for c in ["DT_RECEB", "DT_ENTREGA", "DT_APRESENTACAO"] if c in master), None)
            master["available_at"] = pd.to_datetime(master[received], errors="coerce", utc=True) if received else pd.NaT
            receipts = (
                master.sort_values(["CD_CVM", "DT_REFER", "VERSAO", "available_at"])
                .drop_duplicates(["CD_CVM", "DT_REFER", "VERSAO"], keep="last")
                [["CD_CVM", "DT_REFER", "VERSAO", "available_at"]]
            )
        statements = {}
        for key, marker in STATEMENT_MARKERS.items():
            member = _find_member(names, marker, year)
            statement_columns = {"CD_CVM", "DT_REFER", "VERSAO", "ORDEM_EXERC", "CD_CONTA", "VL_CONTA", "ESCALA_MOEDA"}
            raw = _read_csv_from_zip(archive, member, statement_columns) if member else pd.DataFrame()
            prepared = _prepare_statement(raw, key, receipts) if not raw.empty else raw
            statements[key] = prepared[prepared.CD_CONTA.isin(ACCOUNT_CODES[key])].copy() if not prepared.empty else prepared
    return {"master": master, **statements}


def _latest_documents(statements: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in statements if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True)
    doc = (
        data[["CD_CVM", "DT_REFER", "VERSAO", "available_at"]]
        .drop_duplicates()
        .sort_values(["CD_CVM", "DT_REFER", "available_at", "VERSAO"], na_position="first")
        .groupby(["CD_CVM", "DT_REFER"], as_index=False)
        .tail(1)
    )
    return data.merge(doc[["CD_CVM", "DT_REFER", "VERSAO"]], on=["CD_CVM", "DT_REFER", "VERSAO"], how="inner")


def _account_table(frame: pd.DataFrame, mapping: dict[str, list[str]]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    code_to_feature = {code: feature for feature, codes in mapping.items() for code in codes}
    priority = {code: order for _, codes in mapping.items() for order, code in enumerate(codes)}
    selected = frame[frame["CD_CONTA"].isin(code_to_feature)].copy()
    if selected.empty:
        return pd.DataFrame()
    selected["feature"] = selected["CD_CONTA"].map(code_to_feature)
    selected["priority"] = selected["CD_CONTA"].map(priority)
    keys = ["CD_CVM", "DT_REFER", "VERSAO"]
    selected = (
        selected.sort_values(keys + ["feature", "priority"])
        .drop_duplicates(keys + ["feature"], keep="first")
    )
    values = selected.pivot_table(index=keys, columns="feature", values="VL_CONTA", aggfunc="first").reset_index()
    available = selected.groupby(keys, as_index=False, dropna=False)["available_at"].max()
    return values.merge(available, on=keys, how="left")


def extract_annual_fundamentals(archives: list[dict[str, pd.DataFrame]]) -> pd.DataFrame:
    """Extrai contas fixas da DFP consolidada para uma linha por companhia/exercício."""
    if not archives:
        return pd.DataFrame()
    bpa = _latest_documents([item.get("bpa") for item in archives])
    bpp = _latest_documents([item.get("bpp") for item in archives])
    dre = _latest_documents([item.get("dre") for item in archives])
    dfc = _latest_documents([item.get("dfc_md") for item in archives] + [item.get("dfc_mi") for item in archives])
    tables = [
        _account_table(bpa, {
            "assets": ["1"], "current_assets": ["1.01"], "cash": ["1.01.01"],
            "receivables": ["1.01.03"], "inventory": ["1.01.04"],
        }),
        _account_table(bpp, {
            "current_liabilities": ["2.01"], "short_debt": ["2.01.04"],
            "long_debt": ["2.02.01"], "equity": ["2.03"],
        }),
        _account_table(dre, {
            "revenue": ["3.01"], "gross_profit": ["3.03"], "ebit": ["3.05"],
            "financial_result": ["3.06"], "net_income": ["3.11", "3.09"],
        }),
        _account_table(dfc, {"operating_cash_flow": ["6.01"]}),
    ]
    tables = [table for table in tables if not table.empty]
    if not tables:
        return pd.DataFrame()
    keys = ["CD_CVM", "DT_REFER", "VERSAO"]
    base = tables[0]
    for table in tables[1:]:
        # A data de recebimento é a mesma no documento; combine_first evita sufixos.
        table = table.drop(columns=["available_at"], errors="ignore")
        base = base.merge(table, on=keys, how="outer")
    base = base.rename(columns={"CD_CVM": "cd_cvm", "DT_REFER": "period", "VERSAO": "version"})
    return base.sort_values(["cd_cvm", "period"]).reset_index(drop=True)


def company_registry_from_archives(archives: list[dict[str, pd.DataFrame]]) -> pd.DataFrame:
    rows = []
    for archive in archives:
        master = archive.get("master", pd.DataFrame())
        if not master.empty and {"CD_CVM", "DENOM_CIA"}.issubset(master):
            part = master[["CD_CVM", "DENOM_CIA"]].dropna().copy()
            part["CD_CVM"] = part["CD_CVM"].astype(str).str.replace(r"\.0$", "", regex=True)
            rows.append(part)
    if not rows:
        return pd.DataFrame(columns=["cd_cvm", "company_name"])
    return (
        pd.concat(rows, ignore_index=True)
        .drop_duplicates("CD_CVM", keep="last")
        .rename(columns={"CD_CVM": "cd_cvm", "DENOM_CIA": "company_name"})
        .sort_values("cd_cvm")
        .reset_index(drop=True)
    )


def match_cvm_companies(
    universe: pd.DataFrame,
    companies: pd.DataFrame,
    overrides: pd.DataFrame | None = None,
    threshold: float = 0.68,
) -> pd.DataFrame:
    """Mapeamento conservador por razão social/nome; baixa confiança falha fechada."""
    required = {"ticker", "name"}
    if not required.issubset(universe):
        raise ValueError("Universo precisa de ticker e name")
    if not {"cd_cvm", "company_name"}.issubset(companies):
        raise ValueError("Cadastro CVM incompatível")
    manual = {}
    if overrides is not None and not overrides.empty:
        if not {"ticker", "cd_cvm"}.issubset(overrides):
            raise ValueError("Overrides precisam de ticker e cd_cvm")
        manual = overrides.assign(cd_cvm=overrides.cd_cvm.astype(str)).set_index("ticker").cd_cvm.to_dict()
    registry = companies.copy()
    registry["tokens"] = registry.company_name.map(_company_tokens)
    registry["normalized"] = registry.tokens.map(" ".join)
    by_code = registry.set_index("cd_cvm")
    rows = []
    for _, asset in universe.drop_duplicates("ticker").iterrows():
        ticker = str(asset.ticker)
        if ticker in manual and manual[ticker] in by_code.index:
            company = by_code.loc[manual[ticker]]
            rows.append({"ticker": ticker, "cd_cvm": manual[ticker], "company_name": company.company_name,
                         "match_score": 1.0, "match_method": "manual", "match_ok": True})
            continue
        tokens = _company_tokens(asset["name"])
        normalized = " ".join(tokens)
        token_set = set(tokens)
        prefix = re.sub(r"\.SA$", "", ticker).upper()[:4]
        best = None
        for _, company in registry.iterrows():
            comp_tokens = set(company.tokens)
            union = token_set | comp_tokens
            jaccard = len(token_set & comp_tokens) / len(union) if union else 0.0
            sequence = SequenceMatcher(None, normalized, company.normalized).ratio()
            score = 0.55 * sequence + 0.45 * jaccard
            compact = company.normalized.replace(" ", "")
            if token_set and token_set.issubset(comp_tokens):
                score += 0.25
            if len(prefix) == 4 and prefix in compact:
                score += 0.08
            score = min(score, 1.0)
            candidate = (score, str(company.cd_cvm), company.company_name)
            if best is None or candidate > best:
                best = candidate
        score, code, name = best if best else (0.0, None, None)
        ok = bool(code is not None and score >= threshold)
        rows.append({"ticker": ticker, "cd_cvm": code if ok else np.nan,
                     "company_name": name if ok else np.nan, "match_score": score,
                     "match_method": "automatic" if ok else "unmatched", "match_ok": ok})
    return pd.DataFrame(rows)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.where(denominator.ne(0))


def _positive_growth(current: pd.Series, previous: pd.Series) -> pd.Series:
    return _safe_ratio(current - previous, previous).where(previous.gt(0))


def compute_cvm_indicators(raw: pd.DataFrame) -> pd.DataFrame:
    """Análises horizontal/vertical e indicadores financeiros por ticker/exercício."""
    if raw.empty:
        return raw.copy()
    data = raw.copy()
    data["period"] = pd.to_datetime(data["period"], errors="coerce")
    data = data.sort_values(["ticker", "period"])
    value_columns = [
        "assets", "current_assets", "cash", "receivables", "inventory",
        "current_liabilities", "short_debt", "long_debt", "equity", "revenue",
        "gross_profit", "ebit", "financial_result", "net_income", "operating_cash_flow",
    ]
    for column in value_columns:
        if column not in data:
            data[column] = np.nan
        data[column] = pd.to_numeric(data[column], errors="coerce")
    previous = data.groupby("ticker")[value_columns].shift(1)
    previous_period = data.groupby("ticker")["period"].shift(1)
    adjacent = (data.period.dt.year - previous_period.dt.year).eq(1)
    for column in ["revenue", "ebit", "net_income", "assets", "equity", "operating_cash_flow"]:
        data[f"{column}_growth_yoy"] = _positive_growth(data[column], previous[column]).where(adjacent)
    avg_assets = (data.assets + previous.assets) / 2
    avg_equity = (data.equity + previous.equity) / 2
    data["roe"] = _safe_ratio(data.net_income, avg_equity.where(adjacent).combine_first(data.equity))
    data["roa_ebit"] = _safe_ratio(data.ebit, avg_assets.where(adjacent).combine_first(data.assets))
    data["roa_net"] = _safe_ratio(data.net_income, avg_assets.where(adjacent).combine_first(data.assets))
    data["gross_margin"] = _safe_ratio(data.gross_profit, data.revenue)
    data["ebit_margin"] = _safe_ratio(data.ebit, data.revenue)
    data["net_margin"] = _safe_ratio(data.net_income, data.revenue)
    data["cash_assets"] = _safe_ratio(data.cash, data.assets)
    data["receivables_assets"] = _safe_ratio(data.receivables, data.assets)
    data["inventory_assets"] = _safe_ratio(data.inventory, data.assets)
    data["equity_assets"] = _safe_ratio(data.equity, data.assets)
    data["current_ratio"] = _safe_ratio(data.current_assets, data.current_liabilities)
    data["gross_debt"] = data[["short_debt", "long_debt"]].sum(axis=1, min_count=1)
    data["debt_assets"] = _safe_ratio(data.gross_debt, data.assets)
    data["short_debt_share"] = _safe_ratio(data.short_debt, data.gross_debt)
    data["net_debt"] = data.gross_debt - data.cash
    data["net_debt_ebit"] = _safe_ratio(data.net_debt, data.ebit).where(data.ebit.gt(0))
    data["cfo_net_income"] = _safe_ratio(data.operating_cash_flow, data.net_income).where(data.net_income.gt(0))
    data["available_at"] = pd.to_datetime(data.get("available_at"), errors="coerce", utc=True)
    return data.replace([np.inf, -np.inf], np.nan)


def _percentile_by_sector(frame: pd.DataFrame, column: str, higher_is_better: bool = True) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    valid = values.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=frame.index)
    low, high = valid.quantile([0.05, 0.95])
    clipped = values.clip(low, high)
    signed = clipped if higher_is_better else -clipped
    result = signed.rank(pct=True, method="average")
    if "sector_pt" not in frame:
        return result
    for _, index in frame.groupby("sector_pt", dropna=False).groups.items():
        idx = list(index)
        if signed.loc[idx].notna().sum() >= 3:
            result.loc[idx] = signed.loc[idx].rank(pct=True, method="average")
    return result


def cvm_policy_score(indicators: pd.DataFrame, tickers: list[str] | None = None) -> pd.DataFrame:
    """Score opcional, setorial e coberto por três pilares; retorna escala 0–100."""
    requested = list(dict.fromkeys(tickers or indicators.get("ticker", pd.Series(dtype=str)).tolist()))
    if indicators.empty:
        return pd.DataFrame({"ticker": requested, "cvm_policy_coverage": 0, "cvm_policy_score": np.nan})
    latest = (
        indicators.sort_values(["ticker", "period", "available_at"], na_position="first")
        .groupby("ticker", as_index=False).tail(1).set_index("ticker")
    )
    if requested:
        latest = latest.reindex(requested)
    scores = pd.DataFrame(index=latest.index)
    directions = {
        "roe": True, "roa_ebit": True, "roa_net": True, "ebit_margin": True, "net_margin": True,
        "revenue_growth_yoy": True, "ebit_growth_yoy": True, "net_income_growth_yoy": True,
        "operating_cash_flow_growth_yoy": True, "current_ratio": True, "debt_assets": False,
        "equity_assets": True, "cfo_net_income": True,
    }
    for column, direction in directions.items():
        if column not in latest:
            latest[column] = np.nan
        scores[column] = _percentile_by_sector(latest, column, direction)
    financial = latest.get("sector_pt", pd.Series("", index=latest.index)).fillna("").str.contains("Financeiro", case=False)
    pillars = pd.DataFrame(index=latest.index, columns=["profitability", "trend", "resilience"], dtype=float)
    non_fin = ~financial
    pillars.loc[non_fin, "profitability"] = scores.loc[non_fin, ["roe", "roa_ebit", "ebit_margin"]].mean(axis=1, skipna=True)
    pillars.loc[non_fin, "trend"] = scores.loc[non_fin, ["revenue_growth_yoy", "ebit_growth_yoy", "operating_cash_flow_growth_yoy"]].mean(axis=1, skipna=True)
    pillars.loc[non_fin, "resilience"] = scores.loc[non_fin, ["current_ratio", "debt_assets", "equity_assets", "cfo_net_income"]].mean(axis=1, skipna=True)
    pillars.loc[financial, "profitability"] = scores.loc[financial, ["roe", "roa_net", "net_margin"]].mean(axis=1, skipna=True)
    pillars.loc[financial, "trend"] = scores.loc[financial, ["revenue_growth_yoy", "net_income_growth_yoy"]].mean(axis=1, skipna=True)
    pillars.loc[financial, "resilience"] = scores.loc[financial, ["equity_assets"]].mean(axis=1, skipna=True)
    non_fin_components = ["roe", "roa_ebit", "ebit_margin", "revenue_growth_yoy", "ebit_growth_yoy",
                          "operating_cash_flow_growth_yoy", "current_ratio", "debt_assets", "equity_assets", "cfo_net_income"]
    fin_components = ["roe", "roa_net", "net_margin", "revenue_growth_yoy", "net_income_growth_yoy", "equity_assets"]
    coverage = pd.Series(0, index=latest.index, dtype=int)
    coverage.loc[non_fin] = scores.loc[non_fin, non_fin_components].notna().sum(axis=1)
    coverage.loc[financial] = scores.loc[financial, fin_components].notna().sum(axis=1)
    complete_pillars = pillars.notna().all(axis=1)
    eligible = complete_pillars & ((non_fin & coverage.ge(6)) | (financial & coverage.ge(4)))
    final = (0.40 * pillars.profitability + 0.30 * pillars.trend + 0.30 * pillars.resilience).where(eligible) * 100
    columns = ["period", "available_at", "sector_pt", "company_name", "cd_cvm"]
    out = latest.reindex(columns=[c for c in columns if c in latest]).join(
        pillars.add_prefix("cvm_")
    )
    out["cvm_policy_coverage"] = coverage
    out["cvm_policy_score"] = final
    out["cvm_policy_type"] = np.where(financial, "financeira", "não financeira")
    return out.reset_index().rename(columns={"index": "ticker"})


def update_cvm_fundamentals(
    folder: str | Path,
    universe: pd.DataFrame,
    years: list[int] | None = None,
    fetch_bytes=None,
    cache_days: int = 7,
) -> dict[str, pd.DataFrame]:
    """Atualiza feature store anual e devolve dados, score, mapeamento e erros."""
    folder = Path(folder)
    completed_year = pd.Timestamp.now(tz="America/Sao_Paulo").year - 1
    years = sorted(set(int(year) for year in (years or range(completed_year - 4, completed_year + 1))))
    archives = []
    errors = []
    for year in years:
        try:
            path = get_dfp_archive(folder, year, fetch_bytes=fetch_bytes, cache_days=cache_days)
            archives.append(read_dfp_archive(path, year))
        except Exception as exc:
            errors.append({"year": year, "message": str(exc)})
    if not archives:
        raise RuntimeError("Nenhuma DFP anual pôde ser carregada")
    companies = company_registry_from_archives(archives)
    override_path = folder / "mapeamento_manual.csv"
    overrides = pd.read_csv(override_path, dtype={"cd_cvm": str}) if override_path.exists() else None
    mapping = match_cvm_companies(universe, companies, overrides=overrides)
    company_data = extract_annual_fundamentals(archives)
    selected_columns = ["ticker", "name", "sector_pt", "segment"]
    selected_columns = [column for column in selected_columns if column in universe]
    bridge = universe[selected_columns].drop_duplicates("ticker").merge(mapping, on="ticker", how="left")
    raw = bridge.merge(company_data, on="cd_cvm", how="left")
    indicators = compute_cvm_indicators(raw)
    scores = cvm_policy_score(indicators, universe.ticker.tolist())
    folder.mkdir(parents=True, exist_ok=True)
    save_csv(mapping, folder / "mapeamento_cvm.csv")
    save_csv(indicators, folder / "fundamentais_cvm_anuais.csv")
    save_csv(scores, folder / "scores_cvm.csv")
    diagnostics = pd.DataFrame(errors)
    save_csv(diagnostics, folder / "diagnostico_cvm.csv")
    return {"indicators": indicators, "scores": scores, "mapping": mapping, "diagnostics": diagnostics}
