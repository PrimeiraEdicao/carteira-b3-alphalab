"""Interface Streamlit da Carteira B3 AlphaLab."""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.cvm_fundamentals import update_cvm_fundamentals
from src.engine import (
    fundamental_ratios,
    fundamental_score,
    save_csv,
    save_result,
    update_fundamentals,
)
from src.universe_b3 import refresh_catalog, refresh_history, run_b3, screen_assets


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ALPHALAB_DATA_DIR", ROOT / "data"))
CATALOG_PATH = DATA_DIR / "universo_b3.csv"
CATALOG_META_PATH = DATA_DIR / "catalogo_meta.json"


st.set_page_config(
    page_title="Carteira B3 | AlphaLab",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def apply_brand() -> None:
    st.markdown(
        """
        <style>
        :root { --navy:#0F2D5B; --blue:#315FC3; --accent:#4F8CFF;
                --light:#F6F8FC; --muted:#52647E; --line:#E0E7F1; }
        .stApp { color:var(--navy); }
        [data-testid="stSidebar"] { background:var(--light); border-right:1px solid var(--line); }
        h1,h2,h3 { color:var(--navy); letter-spacing:-0.02em; }
        .hero { background:linear-gradient(135deg,#F6F8FC 0%,#EDF3FF 100%);
                border:1px solid var(--line); border-radius:24px 64px 24px 24px;
                padding:2.2rem 2.4rem; margin-bottom:1.2rem; }
        .hero-kicker { color:var(--blue); font-size:.72rem; font-weight:700;
                       letter-spacing:.16em; text-transform:uppercase; }
        .hero h1 { margin:.5rem 0 .55rem; font-size:clamp(2rem,4vw,3.7rem); font-weight:450; }
        .hero p { color:var(--muted); max-width:72ch; margin:0; }
        div[data-testid="stMetric"] { background:#fff; border:1px solid var(--line);
                                      border-top:3px solid var(--accent); border-radius:14px;
                                      padding:1rem; }
        .note { color:var(--muted); font-size:.86rem; }
        .risk { background:#0F2D5B; color:#D2DCEC; padding:1rem 1.2rem;
                border-radius:12px; font-size:.82rem; line-height:1.55; }
        .stButton > button[kind="primary"] { border-radius:8px; font-weight:700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_catalog() -> tuple[pd.DataFrame | None, dict]:
    if not CATALOG_PATH.exists() or not CATALOG_META_PATH.exists():
        return None, {}
    return pd.read_csv(CATALOG_PATH), json.loads(CATALOG_META_PATH.read_text(encoding="utf-8"))


def result_zip(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.glob("*")):
            if path.is_file():
                archive.write(path, path.name)
    return buffer.getvalue()


def money(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def percent(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{100 * value:.1f}%".replace(".", ",")


def multiple(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.2f}x".replace(".", ",")


apply_brand()

with st.sidebar:
    logo = ROOT / "assets" / "alphalab-logo.svg"
    if logo.exists():
        st.image(str(logo), width=210)
    st.caption("PESQUISA · ALOCAÇÃO · TRANSPARÊNCIA")
    st.divider()
    st.subheader("1. Universo de ativos")
    catalog, catalog_meta = load_catalog()
    if st.button("Atualizar catálogo público", use_container_width=True):
        with st.status("Consultando o catálogo público…", expanded=True) as status:
            try:
                catalog, catalog_meta = refresh_catalog(DATA_DIR)
                status.update(label=f"Catálogo atualizado: {len(catalog)} ações/units", state="complete")
            except Exception as exc:
                status.update(label="Falha ao atualizar catálogo", state="error")
                st.error(str(exc))
    if catalog is None:
        st.info("Atualize o catálogo para liberar os filtros e a montagem da carteira.")
    else:
        st.caption(
            f"{len(catalog)} ações/units · {catalog['sector_pt'].nunique()} setores · "
            f"fonte consultada em {catalog_meta.get('fetched_at', '—')}"
        )

st.markdown(
    """
    <section class="hero">
      <div class="hero-kicker">CARTEIRA B3 · ALPHALAB</div>
      <h1>Da busca pública à carteira executável.</h1>
      <p>Descobre ações e units, aplica filtros de cobertura e liquidez, estima os pesos e
      converte a solução em quantidades inteiras compatíveis com o capital informado.</p>
    </section>
    """,
    unsafe_allow_html=True,
)

st.caption("O aplicativo é uma ferramenta de pesquisa. Ele não envia ordens e não constitui recomendação de investimento.")

if catalog is None:
    st.stop()

all_sectors = sorted(catalog["sector_pt"].dropna().unique())
with st.sidebar:
    st.subheader("2. Escopo")
    selected_sectors = st.multiselect("Setores", all_sectors, default=all_sectors)
    segment_options = sorted(
        catalog.loc[catalog["sector_pt"].isin(selected_sectors), "segment"].dropna().unique()
    )
    selected_segments = st.multiselect("Segmentos", segment_options, default=segment_options)
    scope = catalog[
        catalog["sector_pt"].isin(selected_sectors)
        & catalog["segment"].isin(selected_segments)
    ].copy()
    st.caption(f"{len(scope)} ativos serão consultados antes da seleção.")

    st.subheader("3. Capital e limites")
    capital = st.number_input("Valor disponível (R$)", min_value=100.0, value=10_000.0, step=500.0)
    start = st.date_input("Início do histórico", value=date(date.today().year - 6, 1, 1))
    window = st.slider("Meses completos", 12, 60, 24)
    min_liquidity = st.number_input(
        "Liquidez média mínima (R$/dia)", min_value=0.0, value=1_000_000.0, step=100_000.0
    )
    max_assets = st.slider("Máximo de ativos", 1, 30, 10)
    max_asset = st.slider("Máximo por ativo (%)", 5, 100, 25, 5) / 100
    max_group = st.slider("Máximo por grupo (%)", 5, 100, 100, 5) / 100
    group_column = st.selectbox("Grupo do limite", ["segment", "sector_pt"], format_func={"segment": "Segmento", "sector_pt": "Setor"}.get)

    with st.expander("Premissas avançadas"):
        ranking = st.selectbox(
            "Seleção automática",
            ["liquidity", "sharpe", "volatility", "cvm_policy", "fundamental"],
            format_func={"liquidity": "Maior liquidez", "sharpe": "Maior Sharpe histórico",
                         "volatility": "Menor volatilidade",
                         "cvm_policy": "Qualidade + tendência — CVM (experimental)",
                         "fundamental": "Qualidade fundamentalista — legado"}.get,
        )
        default_pool = min(60, max(20, 3 * max_assets))
        fundamental_pool_size = st.slider(
            "Pool para análise fundamentalista",
            min_value=max_assets,
            max_value=60,
            value=max(max_assets, default_pool),
            disabled=ranking not in {"fundamental", "cvm_policy"},
            help="Pré-seleção por liquidez antes da consulta das demonstrações anuais.",
        )
        if ranking == "cvm_policy":
            st.caption(
                "Challenger setorial: rentabilidade 40%, tendência 30% e resiliência 30%. "
                "Usa DFP consolidada da CVM e não altera o modo padrão do aplicativo."
            )
        elif ranking == "fundamental":
            st.caption("Score relativo: ROE, ROA-EBIT, Ki, estabilidade do GAF e do GAO; 20% cada e cobertura mínima 3/5.")
        objective = st.selectbox(
            "Otimização dos pesos",
            ["min_variance", "max_sharpe"],
            format_func={"min_variance": "Menor variância", "max_sharpe": "Maior Sharpe estimado"}.get,
        )
        rf_annual = st.number_input("Taxa livre anual (%)", min_value=0.0, value=10.0, step=0.5) / 100
        use_scenario = st.checkbox("Informar cenário mensal do Ibovespa")
        market_scenario = st.number_input("Cenário mensal Ibovespa (%)", value=0.0, step=0.25, disabled=not use_scenario) / 100
        lot = st.selectbox("Lote", [1, 100], format_func={1: "Fracionário — 1 ação", 100: "Lote padrão — 100 ações"}.get)
        fee_rate = st.number_input("Custos variáveis (%)", min_value=0.0, value=0.0, step=0.01) / 100
        fixed_fee = st.number_input("Reserva fixa por ativo (R$)", min_value=0.0, value=0.0, step=0.5)
        pause = st.slider("Pausa entre consultas (s)", 0.0, 5.0, 0.4, 0.1)
        one_per_prefix = st.checkbox("Manter só a classe mais líquida por empresa", value=True)
        allow_partial = st.checkbox("Prosseguir após falhas isoladas", value=True)

    run = st.button("Buscar B3 e montar carteira", type="primary", use_container_width=True)

if run:
    if scope.empty:
        st.error("Selecione pelo menos um setor e um segmento.")
        st.stop()
    timestamp = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%Y%m%d_%H%M%S")
    destination = DATA_DIR / "resultados" / timestamp
    destination.mkdir(parents=True, exist_ok=True)
    config = {
        "capital": capital,
        "start": str(start),
        "window": window,
        "min_liquidity": min_liquidity,
        "max_assets": max_assets,
        "max_asset": max_asset,
        "max_segment": max_group,
        "group_column": group_column,
        "ranking": ranking,
        "fundamental_pool_size": fundamental_pool_size,
        "objective": objective,
        "one_per_prefix": one_per_prefix,
        "allow_partial": allow_partial,
        "rf_annual": rf_annual,
        "market_scenario": market_scenario if use_scenario else None,
        "lot": lot,
        "fee_rate": fee_rate,
        "fixed_fee": fixed_fee,
        "sectors": selected_sectors,
        "segments": selected_segments,
        "scope_count": len(scope),
        "source_catalog": catalog_meta,
    }
    (destination / "configuracao.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_csv(scope, destination / "escopo_selecionado.csv")
    progress = st.progress(0, text="Preparando a consulta…")
    messages: list[str] = []

    def update_progress(message: str) -> None:
        messages.append(message)
        current = int(message.split("/", 1)[0]) if "/" in message else 0
        progress.progress(min(current / (len(scope) + 1), 1.0), text=message)

    try:
        history, logs, blocked = refresh_history(
            DATA_DIR, scope["ticker"].tolist(), str(start), progress=update_progress, pause=pause
        )
        save_csv(logs, destination / "diagnostico_coleta.csv")
        if blocked:
            raise RuntimeError(
                "A fonte aplicou limite de consultas. O histórico foi preservado; aguarde e execute novamente para retomar."
            )
        scores = None
        cvm_bundle = None
        if ranking == "fundamental":
            progress.progress(0.95, text="Definindo o pool fundamentalista por liquidez…")
            preliminary, _, _ = screen_assets(
                history, scope, logs, window, min_liquidity,
                allow_partial=allow_partial, one_per_prefix=one_per_prefix,
                capital=capital, lot=lot, max_asset=max_asset,
                ranking="liquidity", rf_annual=rf_annual,
            )
            pool = preliminary.head(fundamental_pool_size)
            if pool.empty:
                raise ValueError("Nenhum ativo elegível para a análise fundamentalista")
            progress.progress(0.97, text=f"Consultando fundamentos de {len(pool)} candidatos…")
            raw_fundamentals, fundamental_errors = update_fundamentals(
                DATA_DIR / "fundamentais.csv", pool["ticker"].tolist()
            )
            ratios = fundamental_ratios(raw_fundamentals)
            scores = fundamental_score(ratios, pool["ticker"].tolist(), min_components=3)
            save_csv(scores, destination / "scores_fundamentalistas.csv")
            save_csv(fundamental_errors, destination / "diagnostico_fundamentos.csv")
        elif ranking == "cvm_policy":
            progress.progress(0.95, text="Definindo o pool CVM por liquidez…")
            preliminary, _, _ = screen_assets(
                history, scope, logs, window, min_liquidity,
                allow_partial=allow_partial, one_per_prefix=one_per_prefix,
                capital=capital, lot=lot, max_asset=max_asset,
                ranking="liquidity", rf_annual=rf_annual,
            )
            pool = preliminary.head(fundamental_pool_size)
            if pool.empty:
                raise ValueError("Nenhum ativo elegível para a política CVM")
            cvm_universe = scope[scope.ticker.isin(pool.ticker)].copy()
            progress.progress(0.97, text=f"Lendo DFPs da CVM para {len(cvm_universe)} candidatos…")
            cvm_bundle = update_cvm_fundamentals(DATA_DIR / "cvm", cvm_universe)
            scores = cvm_bundle["scores"]
            save_csv(scores, destination / "scores_politica_cvm.csv")
            save_csv(cvm_bundle["mapping"], destination / "mapeamento_cvm.csv")
            save_csv(cvm_bundle["diagnostics"], destination / "diagnostico_cvm.csv")
        result = run_b3(
            history,
            scope,
            logs,
            capital,
            window=window,
            min_liquidity=min_liquidity,
            max_assets=max_assets,
            max_asset=max_asset,
            max_segment=max_group,
            ranking=ranking,
            one_per_prefix=one_per_prefix,
            allow_partial=allow_partial,
            objective=objective,
            rf_annual=rf_annual,
            market_scenario=market_scenario if use_scenario else None,
            lot=lot,
            fee_rate=fee_rate,
            fixed_fee=fixed_fee,
            group_column=group_column,
            diagnostic_folder=destination,
            fundamental_scores=scores,
        )
        # O painel descritivo é independente da política escolhida. Uma falha da
        # CVM não invalida a carteira de mercado, mas bloqueia o ranking CVM.
        try:
            if cvm_bundle is None:
                chosen_tickers = result["selected"]["ticker"].tolist()
                cvm_universe = scope[scope.ticker.isin(chosen_tickers)].copy()
                cvm_bundle = update_cvm_fundamentals(DATA_DIR / "cvm", cvm_universe)
            result["cvm_indicators"] = cvm_bundle["indicators"]
            result["cvm_scores"] = cvm_bundle["scores"]
            result["cvm_mapping"] = cvm_bundle["mapping"]
            result["cvm_diagnostics"] = cvm_bundle["diagnostics"]
            save_csv(cvm_bundle["indicators"], destination / "indicadores_cvm.csv")
            save_csv(cvm_bundle["scores"], destination / "scores_politica_cvm.csv")
            save_csv(cvm_bundle["mapping"], destination / "mapeamento_cvm.csv")
        except Exception as cvm_exc:
            if ranking == "cvm_policy":
                raise
            result["cvm_error"] = str(cvm_exc)
            result["cvm_indicators"] = pd.DataFrame()
            result["cvm_scores"] = pd.DataFrame()
            result["cvm_mapping"] = pd.DataFrame()
        save_result(result, destination)
        save_csv(result["selected"], destination / "ativos_selecionados.csv")
        st.session_state["portfolio_result"] = result
        st.session_state["portfolio_folder"] = str(destination)
        progress.progress(1.0, text="Carteira concluída.")
    except Exception as exc:
        progress.empty()
        st.error(f"Execução não concluída: {exc}")

result = st.session_state.get("portfolio_result")
folder_value = st.session_state.get("portfolio_folder")
if result is not None and folder_value:
    folder = Path(folder_value)
    summary = result["summary"]
    st.subheader("Carteira calculada")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Capital", money(summary["capital"]))
    c2.metric("Investido", money(summary["invested"]))
    c3.metric("Caixa", money(summary["cash"]))
    c4.metric("Ativos comprados", int(summary["bought_count"]))

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Plano de compra", "Risco e retorno", "Auditoria da seleção", "Fundamentos CVM"]
    )
    with tab1:
        plan = result["plan"].copy()
        plan["peso_teorico_%"] = plan["target_weight"] * 100
        plan["peso_efetivo_%"] = plan["actual_weight_capital"] * 100
        plan_event = st.dataframe(
            plan[["ticker", "segment", "reference_price", "quantity", "invested", "peso_teorico_%", "peso_efetivo_%"]],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="portfolio_plan_table",
        )
        if plan_event.selection.rows:
            st.session_state["selected_portfolio_ticker"] = plan.iloc[plan_event.selection.rows[0]]["ticker"]
            st.caption("Ativo selecionado. Abra a aba **Fundamentos CVM** para ver a análise do negócio.")
        st.bar_chart(plan.set_index("ticker")[["peso_teorico_%", "peso_efetivo_%"]])
    with tab2:
        left, center, right = st.columns(3)
        left.metric("Retorno mensal estimado", f"{100 * summary['expected_month']:.2f}%")
        center.metric("Volatilidade mensal", f"{100 * summary['volatility_month']:.2f}%")
        right.metric("Sharpe mensal estimado", f"{summary['sharpe_month']:.3f}")
        risk_table = plan[["ticker", "alpha_month", "beta", "r_squared", "beta_pvalue",
                           "beta_stderr", "historical_mean_month", "expected_month",
                           "volatility_month", "historical_sharpe_month",
                           "risk_contribution_pct"]].copy()
        for column in ["alpha_month", "historical_mean_month", "expected_month",
                       "volatility_month", "risk_contribution_pct"]:
            risk_table[column] *= 100
        risk_table = risk_table.rename(columns={
            "alpha_month": "alpha_mensal_%", "beta": "beta", "r_squared": "r_quadrado",
            "beta_pvalue": "p_valor_beta", "beta_stderr": "erro_padrao_beta",
            "historical_mean_month": "media_historica_mensal_%",
            "expected_month": "retorno_estimado_mensal_%",
            "volatility_month": "volatilidade_mensal_%",
            "historical_sharpe_month": "sharpe_historico_mensal",
            "risk_contribution_pct": "contribuicao_risco_%",
        })
        st.dataframe(risk_table, use_container_width=True, hide_index=True)
        st.caption("Contribuição de risco calculada com os pesos teóricos; pode ser negativa quando um ativo reduz o risco conjunto.")
        st.bar_chart(risk_table.set_index("ticker")[["contribuicao_risco_%"]])
        monte_carlo = result["monte_carlo"]
        if not monte_carlo.empty:
            chart = monte_carlo[["volatility_month", "expected_month", "sharpe_month"]].copy()
            chart[["volatility_month", "expected_month"]] *= 100
            st.scatter_chart(chart, x="volatility_month", y="expected_month", color="sharpe_month")
        st.caption(
            f"Cotações de {summary['quote_date']} · janela de {summary['first_month']} a {summary['last_month']}. "
            "O retorno é uma estimativa condicional, não uma promessa de desempenho."
        )
    with tab3:
        st.write(
            f"Escopo: **{summary['universe_considered']}** · elegíveis: **{summary['eligible_count']}** · "
            f"selecionados: **{summary['selected_count']}** · falhas de coleta: **{summary['download_failed_count']}**"
        )
        st.dataframe(result["eligible"], use_container_width=True, hide_index=True)
        excluded = result["screening"].loc[lambda frame: ~frame["eligible"]]
        with st.expander(f"Ativos excluídos ({len(excluded)})"):
            st.dataframe(excluded[["ticker", "name", "exclusion"]], use_container_width=True, hide_index=True)

        if summary["ranking"] == "fundamental":
            st.info("O score fundamentalista é relativo ao pool pré-selecionado por liquidez e não constitui previsão de retorno.")
            fundamental_columns = [c for c in ["ticker", "period", "roe", "roa_ebit", "gaf", "gao",
                                                "ki_effective", "fundamental_coverage", "fundamental_score"]
                                   if c in result["eligible"]]
            st.dataframe(result["eligible"][fundamental_columns], use_container_width=True, hide_index=True)
        elif summary["ranking"] == "cvm_policy":
            st.info("A política CVM é um challenger experimental; o baseline permanece inalterado.")
            cvm_columns = [c for c in ["ticker", "period", "available_at", "company_name",
                                       "cvm_profitability", "cvm_trend", "cvm_resilience",
                                       "cvm_policy_coverage", "cvm_policy_score"]
                           if c in result["eligible"]]
            st.dataframe(result["eligible"][cvm_columns], use_container_width=True, hide_index=True)

    with tab4:
        bought = plan.loc[plan.quantity.gt(0), "ticker"].tolist()
        options = bought or plan["ticker"].tolist()
        current = st.session_state.get("selected_portfolio_ticker")
        if current not in options:
            current = options[0]
        ticker = st.selectbox(
            "Ação da carteira",
            options,
            index=options.index(current),
            help="Você também pode selecionar uma linha na aba Plano de compra.",
        )
        st.session_state["selected_portfolio_ticker"] = ticker
        indicators = result.get("cvm_indicators", pd.DataFrame())
        mapping = result.get("cvm_mapping", pd.DataFrame())
        scores_cvm = result.get("cvm_scores", pd.DataFrame())
        asset_history = indicators[indicators.ticker.eq(ticker)].sort_values("period") if not indicators.empty else pd.DataFrame()
        if asset_history.empty or asset_history["period"].isna().all():
            reason = result.get("cvm_error", "Não houve correspondência segura entre o ticker e a companhia na CVM.")
            st.warning(f"Indicadores CVM indisponíveis para {ticker}. {reason}")
            if not mapping.empty and ticker in set(mapping.ticker):
                st.dataframe(mapping[mapping.ticker.eq(ticker)], use_container_width=True, hide_index=True)
        else:
            latest = asset_history.iloc[-1]
            company_value = latest.get("company_name")
            company = ticker if pd.isna(company_value) else str(company_value)
            match = mapping[mapping.ticker.eq(ticker)].tail(1) if not mapping.empty else pd.DataFrame()
            confidence = match.match_score.iloc[0] if not match.empty else float("nan")
            st.markdown(f"### {company}")
            st.caption(
                f"{ticker} · exercício {pd.Timestamp(latest.period).year} · CD_CVM {latest.get('cd_cvm', '—')} · "
                f"confiança do vínculo {percent(confidence)}"
            )
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("ROE", percent(latest.get("roe")))
            m2.metric("Margem EBIT", percent(latest.get("ebit_margin")))
            m3.metric("Receita YoY", percent(latest.get("revenue_growth_yoy")))
            m4.metric("Dívida / ativos", percent(latest.get("debt_assets")))
            m5.metric("Liquidez corrente", multiple(latest.get("current_ratio")))
            score_row = scores_cvm[scores_cvm.ticker.eq(ticker)].tail(1) if not scores_cvm.empty else pd.DataFrame()
            if not score_row.empty and pd.notna(score_row.iloc[0].get("cvm_policy_score")):
                score_value = float(score_row.iloc[0]["cvm_policy_score"])
                st.info(
                    f"Score challenger CVM: **{score_value:.1f}/100** · "
                    f"rentabilidade {100 * score_row.iloc[0]['cvm_profitability']:.0f} · "
                    f"tendência {100 * score_row.iloc[0]['cvm_trend']:.0f} · "
                    f"resiliência {100 * score_row.iloc[0]['cvm_resilience']:.0f}."
                )

            st.markdown("#### Análise horizontal")
            horizontal = asset_history[[
                "period", "revenue", "revenue_growth_yoy", "ebit", "ebit_growth_yoy",
                "net_income", "net_income_growth_yoy", "operating_cash_flow",
            ]].copy()
            horizontal["period"] = pd.to_datetime(horizontal.period).dt.year
            horizontal[["revenue", "ebit", "net_income", "operating_cash_flow"]] /= 1_000_000
            horizontal[["revenue_growth_yoy", "ebit_growth_yoy", "net_income_growth_yoy"]] *= 100
            horizontal = horizontal.rename(columns={
                "period": "ano", "revenue": "receita_R$mi", "revenue_growth_yoy": "receita_YoY",
                "ebit": "EBIT_R$mi", "ebit_growth_yoy": "EBIT_YoY",
                "net_income": "lucro_R$mi", "net_income_growth_yoy": "lucro_YoY",
                "operating_cash_flow": "FCO_R$mi",
            })
            st.dataframe(
                horizontal,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "receita_YoY": st.column_config.NumberColumn(format="%.1f%%"),
                    "EBIT_YoY": st.column_config.NumberColumn(format="%.1f%%"),
                    "lucro_YoY": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            chart = asset_history.set_index("period")[["revenue", "ebit", "net_income"]] / 1_000_000
            chart = chart.rename(columns={"revenue": "Receita", "ebit": "EBIT", "net_income": "Lucro"})
            st.line_chart(chart, y=list(chart.columns), y_label="R$ milhões")

            st.markdown("#### Análise vertical e estrutura financeira")
            vertical_columns = [
                "period", "cash_assets", "receivables_assets", "inventory_assets", "debt_assets",
                "equity_assets", "short_debt_share", "gross_margin", "ebit_margin", "net_margin",
                "current_ratio", "net_debt_ebit", "cfo_net_income",
            ]
            vertical = asset_history[[column for column in vertical_columns if column in asset_history]].tail(5).copy()
            vertical["period"] = pd.to_datetime(vertical.period).dt.year
            vertical = vertical.rename(columns={
                "period": "ano", "cash_assets": "caixa/ativos", "receivables_assets": "recebíveis/ativos",
                "inventory_assets": "estoques/ativos", "debt_assets": "dívida/ativos",
                "equity_assets": "PL/ativos", "short_debt_share": "dívida_CP/dívida",
                "gross_margin": "margem_bruta", "ebit_margin": "margem_EBIT",
                "net_margin": "margem_líquida", "current_ratio": "liquidez_corrente",
                "net_debt_ebit": "dívida_líquida/EBIT", "cfo_net_income": "FCO/lucro",
            })
            st.dataframe(vertical, use_container_width=True, hide_index=True)
            received = latest.get("available_at")
            received_text = "não informado" if pd.isna(received) else pd.Timestamp(received).strftime("%d/%m/%Y")
            st.caption(
                f"Fonte: DFP consolidada estruturada da CVM · recebida em {received_text}. "
                "Ausências permanecem vazias e não são convertidas em zero."
            )
            if "Financeiro" in str(latest.get("sector_pt", "")):
                st.caption("Instituição financeira: endividamento e liquidez corrente não têm a mesma interpretação de uma empresa não financeira.")

    with st.expander("Metodologia das métricas"):
        st.markdown(
            """
            **Mercado:** retornos mensais ajustados; regressão `ação = alpha + beta × Ibovespa`;
            risco pela matriz de covariância; pesos por variância mínima ou Sharpe máximo.

            **Contribuição de risco:** `peso × risco marginal / volatilidade da carteira`.

            **Fundamentos:** percentis transversais com pesos iguais para ROE, ROA-EBIT, menor Ki,
            GAF próximo de 1 e GAO próximo de 1. O cálculo exige ao menos três componentes e usa Ki
            revisado quando disponível; caso contrário, usa despesa de juros/dívida total como proxy.

            **CVM:** análise anual consolidada com contas fixas da DFP. O challenger combina
            rentabilidade (40%), tendência horizontal (30%) e resiliência/estrutura vertical (30%),
            com percentis setoriais e cobertura mínima. A data de recebimento é preservada para um
            futuro backtest point-in-time; o score não é validação de desempenho.
            """
        )

    st.download_button(
        "Baixar execução completa (.zip)",
        data=result_zip(folder),
        file_name=f"carteira_b3_{folder.name}.zip",
        mime="application/zip",
    )

st.markdown(
    """
    <div class="risk"><strong>Aviso de risco.</strong> Dados públicos podem conter atrasos, omissões e revisões.
    A seleção por ranking seguida da otimização de pesos não prova superioridade futura nem certifica o ótimo
    global entre todas as combinações possíveis. Revise cotações, custos, liquidez e adequação ao seu perfil
    antes de qualquer decisão.</div>
    """,
    unsafe_allow_html=True,
)

