"""Motor independente de Colab/Streamlit. Valores monetários em BRL."""
from pathlib import Path
import json
import time
import numpy as np
import pandas as pd
from scipy.optimize import minimize, milp, Bounds, LinearConstraint
from scipy.stats import linregress

COLS = ['date', 'ticker', 'close', 'adj_close', 'volume', 'dividends', 'splits']

def save_csv(df, path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    df.to_csv(tmp, index=False); tmp.replace(path)

def yahoo_history(ticker, start, end):
    import yfinance as yf
    last = None
    for attempt in range(3):
        try:
            d = yf.Ticker(ticker).history(start=str(start), end=str(end),
                auto_adjust=False, actions=True, raise_errors=True)
            if d.empty: return pd.DataFrame(columns=COLS)
            if 'Adj Close' not in d: raise ValueError('Fonte não forneceu Adj Close')
            out = pd.DataFrame({'date': d.index.strftime('%Y-%m-%d'), 'ticker': ticker,
                'close': d['Close'].to_numpy(), 'adj_close': d['Adj Close'].to_numpy(),
                'volume': d['Volume'].to_numpy(),
                'dividends': d.get('Dividends', pd.Series(0, index=d.index)).to_numpy(),
                'splits': d.get('Stock Splits', pd.Series(0, index=d.index)).to_numpy()})
            if (out[['close','adj_close']] <= 0).any().any(): raise ValueError('Preço inválido')
            return out.dropna(subset=['close','adj_close'])
        except Exception as e:
            last = e
            if attempt < 2: time.sleep(attempt + 1)
    raise RuntimeError(f'{ticker}: {last}')

def update_prices(path, tickers, start, end=None, fetch=yahoo_history, overlap=14):
    """end exclusivo; exclui o pregão em curso. Atualiza por ativo e por data.
    Dividendos/splits e revisões de ajustes podem exigir reconsulta do ativo inteiro.
    A escrita é atômica; falhas nunca apagam séries anteriores.
    """
    end = pd.Timestamp(end or pd.Timestamp.now(tz='America/Sao_Paulo').date()).date()
    start = pd.Timestamp(start).date()
    if start >= end: raise ValueError('Data inicial deve preceder a final exclusiva')
    path = Path(path)
    all_data = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=COLS)
    if not set(COLS).issubset(all_data): raise ValueError('CSV histórico incompatível')
    all_data['date'] = pd.to_datetime(all_data.date).dt.strftime('%Y-%m-%d')
    if all_data.duplicated(['date','ticker']).any(): raise ValueError('CSV contém chaves duplicadas')
    logs = []
    for ticker in dict.fromkeys(tickers):
        old = all_data[all_data.ticker.eq(ticker)].sort_values('date')
        begin = start
        full = old.empty or (not old.empty and start < pd.Timestamp(old.date.min()).date())
        if not full:
            begin = max(start, (pd.Timestamp(old.date.max()) - pd.Timedelta(days=overlap)).date())
        try:
            new = fetch(ticker, begin, end)
            if new.empty: raise ValueError('Nenhum dado retornado; não foi usado histórico obsoleto')
            new = new[COLS].copy()
            new['date'] = pd.to_datetime(new.date).dt.strftime('%Y-%m-%d')
            new = new[(new.date >= str(begin)) & (new.date < str(end))]
            if new.empty or new.ticker.ne(ticker).any(): raise ValueError('Resposta incompatível')
            if not np.isfinite(new[['close','adj_close']].to_numpy()).all() or (new[['close','adj_close']] <= 0).any().any():
                raise ValueError('Preço inválido')
            if not old.empty and not full:
                common = old.merge(new, on=['date','ticker'], suffixes=('_old','_new'))
                changed = common.empty or not np.allclose(common.adj_close_old, common.adj_close_new, rtol=1e-6, atol=1e-6)
                changed = changed or not np.allclose(common.close_old, common.close_new, rtol=1e-6, atol=1e-6)
                events = new[new.date > old.date.max()][['dividends','splits']].fillna(0).ne(0).any().any()
                if changed or events:
                    full = True
                    begin = min(start, pd.Timestamp(old.date.min()).date())
                    new = fetch(ticker, begin, end)
                    if new.empty: raise ValueError('Falha ao reconstruir ajustes corporativos')
                    new = new[COLS].copy()
                    new['date'] = pd.to_datetime(new.date).dt.strftime('%Y-%m-%d')
                    if new.ticker.ne(ticker).any() or not np.isfinite(new[['close','adj_close']].to_numpy()).all() or (new[['close','adj_close']] <= 0).any().any():
                        raise ValueError('Reconstrução inválida')
                    new = new[(new.date >= str(begin)) & (new.date < str(end))]
                    if new.empty: raise ValueError('Reconstrução vazia')
            # Mantém datas antigas se a fonte omitir temporariamente uma observação.
            # Em revisão corporativa exige cobertura de todos os pontos antigos do intervalo.
            if full and not old.empty:
                expected = set(old.loc[(old.date >= str(begin)) & (old.date < str(end)), 'date'])
                if not expected.issubset(set(new.date)):
                    raise ValueError('Reconsulta incompleta: histórico antigo preservado')
            kept = all_data[all_data.ticker.ne(ticker)] if full else all_data
            all_data = (new if kept.empty else pd.concat([kept, new], ignore_index=True)).drop_duplicates(['date','ticker'], keep='last')
            logs.append(dict(ticker=ticker, status='ok', mode='reconsulta completa' if full else 'incremental',
                             start=str(begin), last_date=new.date.max(), rows=len(new)))
        except Exception as e:
            logs.append(dict(ticker=ticker, status='erro', message=str(e)))
    all_data = all_data.sort_values(['ticker','date']).reset_index(drop=True)
    save_csv(all_data, path)
    return all_data, pd.DataFrame(logs)

def monthly_returns(history, tickers, window=24, asof=None):
    """Meses completos, mesma frequência/datas; nunca preenche preço ausente."""
    cutoff = pd.Timestamp(asof or pd.Timestamp.now(tz='America/Sao_Paulo').date()).to_period('M').start_time
    h = history.copy(); h['date'] = pd.to_datetime(h.date)
    h = h[(h.date < cutoff) & h.ticker.isin(tickers)]
    p = h.pivot(index='date', columns='ticker', values='adj_close').sort_index().reindex(columns=tickers)
    # Evita aproveitar o último preço de um ativo suspenso/ausente no fim do mês.
    # Usa a data do último pregão mensal do benchmark, sem ffill de outros ativos.
    benchmark = tickers[-1]
    b = p[benchmark].dropna()
    month_dates = b.groupby(b.index.to_period('M')).apply(lambda s:s.index[-1])
    if month_dates.empty: raise ValueError('Benchmark sem histórico mensal')
    ends = p.reindex(pd.DatetimeIndex(month_dates.to_numpy()))
    ends.index = ends.index.to_period('M')
    ends = ends.reindex(pd.period_range(ends.index.min(), ends.index.max(), freq='M'))
    r = ends.pct_change(fill_method=None).tail(window).dropna(how='any')
    if len(r) < window:
        raise ValueError(f'Precisamos de {window} meses completos comuns; disponíveis {len(r)}. Amplie o histórico ou retire ativos sem cobertura.')
    if not np.isfinite(r.to_numpy()).all(): raise ValueError('Retornos não finitos')
    return r

def fit_model(returns, benchmark='^BVSP', market_scenario=None, rf_annual=.10):
    x = returns[benchmark].to_numpy()
    if np.var(x) <= 1e-15: raise ValueError('Benchmark sem variância')
    scenario = float(x.mean() if market_scenario is None else market_scenario)
    rf_month = (1 + rf_annual)**(1/12) - 1
    rows = []
    for ticker in returns.columns.drop(benchmark):
        y = returns[ticker].to_numpy(); f = linregress(x, y)
        volatility = y.std(ddof=1)
        rows.append(dict(ticker=ticker, alpha_month=f.intercept, beta=f.slope,
            r_squared=f.rvalue**2, beta_pvalue=f.pvalue, beta_stderr=f.stderr,
            expected_month=f.intercept + f.slope*scenario,
            historical_mean_month=y.mean(), volatility_month=volatility,
            historical_sharpe_month=(y.mean()-rf_month)/volatility,
            observations=len(y)))
    stats = pd.DataFrame(rows).set_index('ticker')
    cov = returns[stats.index].cov(ddof=1)
    return stats, cov, scenario

def constraints_for(names, segments, max_asset, max_segment):
    n = len(names)
    if not 0 < max_asset <= 1 or not 0 < max_segment <= 1: raise ValueError('Limites devem estar entre 0 e 1')
    groups = list(dict.fromkeys(segments[t] for t in names))
    masks = [np.array([segments[t] == g for t in names], dtype=float) for g in groups]
    capacity = sum(min(max_segment, m.sum()*max_asset) for m in masks)
    if capacity < 1 - 1e-9: raise ValueError('Limites inviáveis para o número de ativos/segmentos selecionados')
    cons = [dict(type='eq', fun=lambda w: w.sum()-1)]
    cons += [dict(type='ineq', fun=lambda w,m=m: max_segment-m@w) for m in masks]
    return cons, masks

def optimize(stats, cov, segments, objective='min_variance', max_asset=1., max_segment=1., rf_annual=.10):
    names = list(stats.index); n = len(names)
    cons, masks = constraints_for(names, segments, max_asset, max_segment)
    sigma = cov.loc[names,names].to_numpy(); mu = stats.expected_month.to_numpy()
    rf = (1+rf_annual)**(1/12)-1
    if objective not in ['min_variance','max_sharpe']: raise ValueError('Objetivo desconhecido')
    def loss(w):
        v = max(float(w@sigma@w), 1e-16)
        return v*1000 if objective == 'min_variance' else -(w@mu-rf)/np.sqrt(v)
    if objective == 'max_sharpe' and np.max(mu) <= rf:
        raise ValueError('Nenhum ativo supera a taxa livre de risco no cenário. Use variância mínima ou reveja o cenário.')
    rng = np.random.default_rng(42)
    starts = [np.ones(n)/n] + [rng.dirichlet(np.ones(n)) for _ in range(12)]
    valid = []
    for w in starts:
        z = minimize(loss, w, method='SLSQP', bounds=[(0,max_asset)]*n,
                     constraints=cons, options=dict(maxiter=1500, ftol=1e-12))
        if z.success and abs(z.x.sum()-1)<1e-6 and min(z.x)>-1e-7 and max(z.x)<=max_asset+1e-7 and all(m@z.x<=max_segment+1e-7 for m in masks): valid.append(z)
    if not valid: raise ValueError('Otimizador não encontrou solução válida')
    w = min(valid, key=lambda z:z.fun).x
    # Sem normalização posterior que possa violar limites.
    return pd.Series(w, index=names, name='target_weight')

def monte_carlo(stats, cov, segments, count=10000, seed=42, max_asset=1., max_segment=1., rf_annual=.10):
    names = list(stats.index); _, masks = constraints_for(names,segments,max_asset,max_segment)
    rng = np.random.default_rng(seed); w = rng.dirichlet(np.ones(len(names)), int(count))
    valid = w.max(axis=1)<=max_asset+1e-9
    for m in masks: valid &= w@m<=max_segment+1e-9
    w = w[valid]
    if not len(w): return pd.DataFrame()
    ret = w@stats.expected_month.to_numpy()
    vol = np.sqrt(np.einsum('ij,jk,ik->i', w, cov.to_numpy(), w))
    out = pd.DataFrame(w, columns=names)
    out['expected_month']=ret; out['volatility_month']=vol
    out['sharpe_month']=(ret-((1+rf_annual)**(1/12)-1))/np.maximum(vol,1e-12)
    return out

def risk_contributions(weights, cov):
    """Contribuições marginais e componentes para a volatilidade mensal."""
    names=list(weights.index)
    w=weights.to_numpy(dtype=float);sigma=cov.loc[names,names].to_numpy(dtype=float)
    variance=float(w@sigma@w)
    if not np.isfinite(variance) or variance<=1e-16:
        raise ValueError('Variância da carteira inválida para decomposição de risco')
    volatility=float(np.sqrt(variance));marginal=(sigma@w)/volatility
    component=w*marginal
    return pd.DataFrame({'ticker':names,'marginal_risk_month':marginal,
        'component_risk_month':component,'risk_contribution_pct':component/volatility})

def allocate(weights, prices, budget, segments, max_asset=1., max_segment=1., lot=1, fee_rate=0., fixed_fee=0.):
    """MILP: minimiza desvio L1 em reais mais caixa. Quantidades inteiras.
    Limites monetários aplicados ao capital informado, inclusive após arredondamento.
    Preços convertidos a centavos, arredondados para cima; taxa fixa reservada por ativo.
    """
    names = list(weights.index); n=len(names)
    if budget<=0 or not np.isfinite(budget): raise ValueError('Capital deve ser positivo')
    if lot < 1 or int(lot)!=lot or fee_rate<0 or fixed_fee<0: raise ValueError('Custos/lote inválidos')
    p = prices.reindex(names).to_numpy(dtype=float)
    if not np.isfinite(p).all() or min(p)<=0: raise ValueError('Preço de compra ausente/inválido')
    b = int(np.floor(budget*100)); unit = np.ceil(p*100).astype(int)*int(lot)
    cost = np.ceil(unit*(1+fee_rate)).astype(int)
    reserve = int(np.ceil(fixed_fee*100))*n; available=b-reserve
    if available <= 0: raise ValueError('Custos fixos excedem capital')
    target = weights.to_numpy()*available
    # x=[lotes inteiros, desvios absolutos, caixa]; sum(cost*q)+caixa=available.
    c = np.r_[np.zeros(n),np.ones(n),1.]
    rows=[]; lo=[]; hi=[]
    rows.append(np.r_[cost,np.zeros(n),1]);lo.append(available);hi.append(available)
    for i in range(n):
        row=np.zeros(2*n+1); row[i]=unit[i]; row[n+i]=-1
        rows.append(row);lo.append(-np.inf);hi.append(target[i])
        row=np.zeros(2*n+1); row[i]=-unit[i]; row[n+i]=-1
        rows.append(row);lo.append(-np.inf);hi.append(-target[i])
    for seg in set(segments[t] for t in names):
        mask=np.array([segments[t]==seg for t in names])
        rows.append(np.r_[unit*mask,np.zeros(n+1)]);lo.append(-np.inf);hi.append(max_segment*b)
    ub=np.r_[np.floor(np.minimum(available/cost, max_asset*b/unit)),np.repeat(np.inf,n+1)]
    z=milp(c,integrality=np.r_[np.ones(n),np.zeros(n+1)],bounds=Bounds(np.zeros(2*n+1),ub),
           constraints=LinearConstraint(np.array(rows),lo,hi),options=dict(time_limit=30,mip_rel_gap=0.))
    if z.x is None: raise ValueError(f'Alocação inteira falhou: {z.message}')
    q=np.rint(z.x[:n]).astype(int); spent=int(cost@q)+reserve
    if spent>b or (unit*q>max_asset*b+1).any(): raise ValueError('Alocação violou orçamento/limites')
    for seg in set(segments[t] for t in names):
        mask=np.array([segments[t]==seg for t in names])
        if (unit*q*mask).sum()>max_segment*b+1: raise ValueError('Alocação violou limite de segmento')
    out=pd.DataFrame(dict(ticker=names,segment=[segments[t] for t in names],
        target_weight=weights.to_numpy(),reference_price=unit/100/lot,
        quantity=q*int(lot),invested=unit*q/100,estimated_variable_cost=(cost-unit)*q/100))
    out['actual_weight_capital']=out.invested/(b/100)
    summary=dict(capital=b/100, invested=float(out.invested.sum()), reserved_fixed_cost=reserve/100,
                 cash=(b-spent)/100, solver_optimal=bool(z.success), solver_message=str(z.message),
                 mip_gap=float(getattr(z,'mip_gap',np.nan)))
    return out,summary

def update_fundamentals(path, tickers, loader=None, cache_days=7):
    """Demonstrações anuais públicas Yahoo: atualização por ticker/exercício.
    Ausências são NaN, nunca zero. ROA segue EBIT/Ativo da planilha.
    Ki exato exige despesa financeira total e dívida onerosa revisadas manualmente.
    """
    if loader is None:
        import yfinance as yf
        def loader(t):
            obj=yf.Ticker(t)
            return obj.get_income_stmt(freq='yearly'),obj.get_balance_sheet(freq='yearly')
    path=Path(path); old=pd.read_csv(path) if path.exists() else pd.DataFrame()
    cached=set()
    if not old.empty and 'fetched_at' in old:
        fetched=pd.to_datetime(old.fetched_at,utc=True,errors='coerce')
        cutoff=pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=cache_days)
        cached=set(old.loc[fetched.ge(cutoff),'ticker'])
    rows=[]; errors=[]
    for t in tickers:
        if t in cached:
            continue
        try:
            inc, bal=loader(t)
            if inc.empty or bal.empty: raise ValueError('Demonstração anual indisponível')
            def val(df,key,dt):
                return float(df.loc[key,dt]) if key in df.index and dt in df.columns and pd.notna(df.loc[key,dt]) else np.nan
            for dt in sorted(set(inc.columns)&set(bal.columns)):
                rows.append(dict(ticker=t,period=str(pd.Timestamp(dt).date()),
                    revenue=val(inc,'TotalRevenue',dt),ebit=val(inc,'EBIT',dt),
                    net_income=val(inc,'NetIncome',dt),assets=val(bal,'TotalAssets',dt),
                    equity=val(bal,'StockholdersEquity',dt),total_debt_proxy=val(bal,'TotalDebt',dt),
                    interest_expense_proxy=val(inc,'InterestExpense',dt),
                    financial_expense=np.nan,interest_bearing_debt=np.nan,
                    source='Yahoo Finance anual',fetched_at=pd.Timestamp.now(tz='UTC').isoformat()))
        except Exception as e: errors.append(dict(ticker=t,message=str(e)))
    new=pd.DataFrame(rows)
    result=pd.concat([old,new],ignore_index=True)
    if not result.empty:
        result=result.drop_duplicates(['ticker','period'],keep='last').sort_values(['ticker','period'])
        save_csv(result,path)
    return result,pd.DataFrame(errors)

def fundamental_ratios(raw):
    if raw.empty: return raw.copy()
    d=raw.sort_values(['ticker','period']).copy()
    def ratio(a,b): return a/b.where(b.ne(0))
    d['roe']=ratio(d.net_income,d.equity)
    d['roa_ebit']=ratio(d.ebit,d.assets)
    d['gaf']=ratio(d.roe,d.roa_ebit)
    prev=d.groupby('ticker')[['revenue','ebit']].shift(1)
    adjacent=pd.to_datetime(d.period).dt.year-pd.to_datetime(d.groupby('ticker').period.shift(1)).dt.year==1
    growth_rev=ratio(d.revenue-prev.revenue,prev.revenue)
    growth_ebit=ratio(d.ebit-prev.ebit,prev.ebit)
    d['gao']=ratio(growth_ebit,growth_rev).where(adjacent)
    d['ki']=ratio(d.financial_expense.abs(),d.interest_bearing_debt)
    d['ki_interest_proxy']=ratio(d.interest_expense_proxy.abs(),d.total_debt_proxy)
    return d.replace([np.inf,-np.inf],np.nan)

def fundamental_score(ratios, tickers=None, min_components=3):
    """Score transversal, explícito e relativo ao pool consultado.

    ROE/ROA maiores recebem percentil maior. Ki menor e GAF/GAO mais próximos
    de 1 recebem percentil maior. Cada componente vale 20%; pesos disponíveis
    são renormalizados e menos de ``min_components`` invalida o score.
    """
    if min_components < 1 or min_components > 5:
        raise ValueError('Cobertura fundamental deve estar entre 1 e 5')
    requested=list(dict.fromkeys(tickers or ratios.get('ticker',pd.Series(dtype=str)).tolist()))
    if ratios.empty:
        return pd.DataFrame({'ticker':requested,'fundamental_coverage':0,
            'fundamental_score':np.nan})
    d=ratios.copy();d['period']=pd.to_datetime(d.period,errors='coerce')
    latest=(d.sort_values(['ticker','period']).groupby('ticker',as_index=False).tail(1)
              .set_index('ticker'))
    if requested:
        latest=latest.reindex(requested)
    latest['ki_effective']=latest['ki'].combine_first(latest['ki_interest_proxy'])
    raw_components=pd.DataFrame(index=latest.index)
    raw_components['roe_score']=latest['roe'].rank(pct=True,method='average')
    raw_components['roa_score']=latest['roa_ebit'].rank(pct=True,method='average')
    raw_components['ki_score']=(-latest['ki_effective']).rank(pct=True,method='average')
    raw_components['gaf_stability_score']=(-(latest['gaf']-1).abs()).rank(pct=True,method='average')
    raw_components['gao_stability_score']=(-(latest['gao']-1).abs()).rank(pct=True,method='average')
    coverage=raw_components.notna().sum(axis=1)
    score=raw_components.mean(axis=1,skipna=True).where(coverage.ge(min_components))
    columns=['period','roe','roa_ebit','gaf','gao','ki','ki_interest_proxy','ki_effective']
    out=latest.reindex(columns=columns).join(raw_components)
    out['fundamental_coverage']=coverage
    out['fundamental_score']=score
    return out.reset_index().rename(columns={'index':'ticker'})

def apply_fundamental_overrides(raw, manual):
    required={'ticker','period'}
    if not required.issubset(manual.columns): raise ValueError('Revisão precisa de ticker e period')
    manual=manual.copy();manual['period']=pd.to_datetime(manual.period).dt.strftime('%Y-%m-%d')
    if manual.duplicated(['ticker','period']).any(): raise ValueError('Revisão com chave duplicada')
    fields=['revenue','ebit','net_income','assets','equity','financial_expense','interest_bearing_debt','interest_expense_proxy','total_debt_proxy']
    allowed=[c for c in fields if c in manual.columns]
    for c in allowed: manual[c]=pd.to_numeric(manual[c],errors='raise')
    idx=['ticker','period']
    base=raw.set_index(idx) if not raw.empty else pd.DataFrame(index=pd.MultiIndex.from_tuples([],names=idx))
    corrected=manual[idx+allowed].set_index(idx).combine_first(base)
    for c in fields:
        if c not in corrected: corrected[c]=np.nan
    corrected.loc[manual.set_index(idx).index,'source']='Manual revisado + dados da fonte quando disponíveis'
    return corrected.reset_index()

def run_portfolio(history, universe, capital, window=24, objective='min_variance',
                  max_asset=1.,max_segment=1.,rf_annual=.10,market_scenario=None,
                  lot=1,fee_rate=0.,fixed_fee=0.,asof=None,simulations=10000,max_staleness_days=10):
    names=universe.ticker.tolist(); segments=universe.set_index('ticker').segment.to_dict()
    if len(names)!=len(set(names)) or not names: raise ValueError('Universo vazio ou duplicado')
    r=monthly_returns(history,names+['^BVSP'],window,asof)
    stats,cov,scenario=fit_model(r,market_scenario=market_scenario,rf_annual=rf_annual)
    weights=optimize(stats,cov,segments,objective,max_asset,max_segment,rf_annual)
    current=history[history.ticker.isin(names)].sort_values('date').groupby('ticker').tail(1).set_index('ticker')
    # Todas as ações devem ter a mesma data do benchmark mais recente retornado.
    bench=history[history.ticker.eq('^BVSP')].date.max()
    today=pd.Timestamp(asof or pd.Timestamp.now(tz='America/Sao_Paulo').date())
    if (today-pd.Timestamp(bench)).days>max_staleness_days:
        raise ValueError('Fonte está há mais de 10 dias sem cotação; cálculo bloqueado para evitar preços antigos')
    if current.date.ne(bench).any(): raise ValueError('Preços de compra com datas diferentes do benchmark; atualize a fonte ou retire ativos sem cotação recente')
    plan,summary=allocate(weights,current.close,capital,segments,max_asset,max_segment,lot,fee_rate,fixed_fee)
    plan=plan.merge(stats.reset_index(),on='ticker');plan['quote_date']=plan.ticker.map(current.date)
    w=weights.to_numpy();sigma=cov.to_numpy();mu=stats.expected_month.to_numpy()
    portfolio_variance=float(w@sigma@w)
    portfolio_volatility=float(np.sqrt(portfolio_variance))
    risk=risk_contributions(weights,cov)
    plan=plan.merge(risk,on='ticker',how='left')
    summary.update(expected_month=float(w@mu),volatility_month=portfolio_volatility,
        variance_month=portfolio_variance,annualized_volatility=float(np.sqrt(12*portfolio_variance)),
        market_scenario_month=scenario,window_months=window,first_month=str(r.index.min()),last_month=str(r.index.max()),
        objective=objective,quote_date=str(bench),rf_annual=rf_annual)
    summary['sharpe_month']=(summary['expected_month']-((1+rf_annual)**(1/12)-1))/portfolio_volatility
    actual=plan.set_index('ticker').actual_weight_capital.reindex(weights.index).to_numpy()
    summary['rounded_risky_expected_month']=float(actual@mu)
    summary['rounded_risky_volatility_month']=float(np.sqrt(actual@sigma@actual))
    mc=monte_carlo(stats,cov,segments,simulations,42,max_asset,max_segment,rf_annual)
    return dict(plan=plan,summary=summary,returns=r,covariance=cov,stats=stats,monte_carlo=mc)

def save_result(result, folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    save_csv(result['plan'],folder/'carteira.csv')
    for k in ['returns','covariance','stats']:
        save_csv(result[k].reset_index(),folder/f'{k}.csv')
    save_csv(result['monte_carlo'],folder/'monte_carlo.csv')
    tmp=folder/'resumo.json.tmp';tmp.write_text(json.dumps(result['summary'],ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(folder/'resumo.json')
