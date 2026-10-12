"""Descoberta de ações/units, cobertura histórica e seleção anterior aos pesos."""
from pathlib import Path
import json, re, time, urllib.request, urllib.parse
import numpy as np
import pandas as pd
from scipy.optimize import milp, Bounds, LinearConstraint
from .engine import save_csv, update_prices, yahoo_history, run_portfolio

SECTORS_PT={'Retail Trade':'Comércio varejista','Energy Minerals':'Petróleo, gás e combustíveis',
 'Health Services':'Serviços de saúde','Utilities':'Energia e saneamento','Finance':'Financeiro',
 'Consumer Services':'Serviços ao consumidor','Consumer Non-Durables':'Consumo não durável',
 'Non-Energy Minerals':'Mineração e siderurgia','Commercial Services':'Serviços comerciais',
 'Distribution Services':'Distribuição','Transportation':'Transportes','Technology Services':'Tecnologia e serviços',
 'Process Industries':'Indústrias de transformação','Communications':'Comunicações',
 'Producer Manufacturing':'Máquinas e equipamentos','Miscellaneous':'Diversos',
 'Electronic Technology':'Tecnologia eletrônica','Industrial Services':'Serviços industriais',
 'Health Technology':'Tecnologia de saúde','Consumer Durables':'Consumo durável'}

def public_catalog_page(page,limit):
    params=urllib.parse.urlencode(dict(type='stock',page=page,limit=limit,sortBy='name',sortOrder='asc'))
    req=urllib.request.Request('https://brapi.dev/api/quote/list?'+params,
                             headers={'User-Agent':'CarteiraPesquisa/2.0','Accept':'application/json'})
    last=None
    for i in range(3):
        try:
            with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
        except Exception as e:
            last=e
            if i<2:time.sleep(i+1)
    raise RuntimeError(f'Catálogo público indisponível: {last}')

def discover_catalog(fetch=public_catalog_page,limit=2000):
    rows=[];expected=None;pages=None
    for page in range(1,101):
        d=fetch(page,limit)
        if d.get('error') or not isinstance(d.get('stocks'),list):raise ValueError('Resposta de catálogo inválida')
        if not all(k in d for k in ['totalCount','totalPages','currentPage','hasNextPage']):
            raise ValueError('Fonte não confirmou paginação; catálogo completo não comprovado')
        if int(d['currentPage'])!=page:raise ValueError('Fonte retornou página incorreta')
        if expected is None:expected=int(d['totalCount']);pages=int(d['totalPages'])
        if expected!=int(d['totalCount']) or pages!=int(d['totalPages']):
            raise ValueError('Catálogo mudou durante paginação; execute novamente')
        rows+=d['stocks']
        if not d['hasNextPage']:
            if page!=pages:raise ValueError('Final de paginação incoerente')
            break
        if not d['stocks']:raise ValueError('Página vazia antes do final')
    else:raise ValueError('Limite de páginas atingido; resultado não salvo')
    if len(rows)!=expected:raise ValueError(f'Fonte anunciou {expected}, entregou {len(rows)}; catálogo incompleto')
    raw=pd.DataFrame(rows)
    if raw.empty or not {'stock','type'}.issubset(raw):raise ValueError('Catálogo sem ativos/tipos')
    if raw.stock.duplicated().any():raise ValueError('Catálogo duplicado entre páginas; execute novamente')
    # Mantém ações e units; exclui FIIs/ETFs/BDRs mesmo se a fonte misturar os tipos.
    is_stock=raw.type.eq('stock')
    if 'subType' in raw:
        subtype=raw.subType.fillna('stock').replace('', 'stock')
        is_stock &= subtype.isin(['stock','unit'])
    is_stock &= raw.stock.astype(str).str.fullmatch(r'[A-Z0-9]{4}[0-9]{1,2}')
    # Códigos 9/10 são direitos/recibos de subscrição, não ações da carteira.
    is_stock &= ~raw.stock.astype(str).str[4:].isin(['9','10'])
    u=raw[is_stock].copy()
    if u.empty:raise ValueError('Nenhuma ação/unit elegível no catálogo')
    u['ticker']=u.stock+'.SA'
    u['sector_original']=u.get('sector',pd.Series(index=u.index,dtype=str)).fillna('Sem classificação')
    u['sector_pt']=u.sector_original.map(SECTORS_PT).fillna(u.sector_original)
    u['segment']=u.get('subsector',pd.Series(index=u.index,dtype=str)).replace('',np.nan).fillna(u.sector_pt)
    u['name']=u.get('name',u.stock).fillna(u.stock)
    u['catalog_close']=pd.to_numeric(u.get('close',np.nan),errors='coerce')
    u['catalog_volume']=pd.to_numeric(u.get('volume',np.nan),errors='coerce')
    u['prefix']=u.stock.str[:4]
    now=pd.Timestamp.now(tz='UTC').isoformat()
    u['catalog_fetched_at']=now
    fields=['ticker','stock','name','sector_original','sector_pt','segment','prefix','catalog_close','catalog_volume','catalog_fetched_at']
    meta=dict(source='brapi /api/quote/list',fetched_at=now,declared_count=expected,
        returned_count=len(raw),equity_unit_count=len(u),excluded_other_types=len(raw)-len(u),pages=pages,
        coverage='Todo o catálogo entregue pelo provedor; não é certificação de todas as listagens oficiais da B3')
    return u[fields].sort_values('ticker').reset_index(drop=True),meta

def refresh_catalog(folder,fetch=public_catalog_page):
    folder=Path(folder);u,meta=discover_catalog(fetch)
    save_csv(u,folder/'universo_b3.csv')
    p=folder/'catalogo_meta.json';p.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    return u,meta

class PoliteHistory:
    def __init__(self,fetch=yahoo_history,pause=.4):
        self.fetch=fetch;self.pause=pause;self.blocked=False;self.rate_errors=0
    def __call__(self,*args):
        if self.blocked:raise RuntimeError('RATE_LIMIT_CIRCUIT: consultas pausadas após HTTP 429')
        time.sleep(self.pause)
        try:
            d=self.fetch(*args);self.rate_errors=0;return d
        except Exception as e:
            if any(s in str(e).lower() for s in ['429','too many requests','rate limit']):
                self.rate_errors+=1
                if self.rate_errors>=2:self.blocked=True
            raise

def refresh_history(folder,tickers,start,fetch=yahoo_history,end=None,progress=print,pause=.4):
    """Checkpoint em cada ativo; retomada e reuso no mesmo dia sem nova consulta."""
    folder=Path(folder);path=folder/'historico.csv';logpath=folder/'atualizacoes.csv'
    today=str(pd.Timestamp(end or pd.Timestamp.now(tz='America/Sao_Paulo').date()).date())
    oldlog=pd.read_csv(logpath).fillna('') if logpath.exists() else pd.DataFrame()
    history=pd.read_csv(path) if path.exists() else pd.DataFrame()
    cached={}
    if not oldlog.empty:
        for _,r in oldlog.iterrows():cached[r.ticker]=r.to_dict()
    hstats={} if history.empty else history.groupby('ticker').date.agg(['min','max']).to_dict('index')
    polite=PoliteHistory(fetch,pause);logs=[];ordered=list(dict.fromkeys(['^BVSP']+list(tickers)))
    for i,t in enumerate(ordered,1):
        previous=cached.get(t,{});span=hstats.get(t,{})
        reuse=(previous.get('status')=='ok' and previous.get('updated_on')==today
               and previous.get('requested_start')==str(start) and t in hstats
               and previous.get('last_date')==span.get('max'))
        if reuse:
            log=dict(previous,mode='cache validado hoje')
        elif polite.blocked:
            log=dict(ticker=t,status='erro',message='RATE_LIMIT_CIRCUIT: consulta não realizada',updated_on=today,requested_start=str(start))
        else:
            progress(f'{i}/{len(ordered)} — {t}')
            history,one=update_prices(path,[t],start,end=end,fetch=polite)
            log=one.iloc[0].to_dict();log.update(updated_on=today,requested_start=str(start))
        logs.append(log)
        cached[t]=log
        save_csv(pd.DataFrame(cached.values()),logpath)
    if history.empty:raise ValueError('Nenhum histórico disponível')
    return history,pd.DataFrame(logs),polite.blocked

def screen_assets(history,universe,log,window=24,min_liquidity=1e6,liquidity_days=60,
                  allow_partial=True,one_per_prefix=True,capital=10000,lot=1,
                  max_asset=1.,asof=None,ranking='liquidity',rf_annual=.10):
    """Avalia todos os selecionados; liquidez média histórica BRL/dia, cobertura estrita."""
    if window<12 or min_liquidity<0:raise ValueError('Janela/liquidez inválidas')
    if not allow_partial and log.status.ne('ok').any():raise ValueError('Consulta parcial bloqueada; veja atualizacoes.csv')
    statuses=log.set_index('ticker').status.to_dict()
    if statuses.get('^BVSP')!='ok':raise ValueError('Benchmark não atualizado')
    today=pd.Timestamp(asof or pd.Timestamp.now(tz='America/Sao_Paulo').date())
    h=history.copy();h['date']=pd.to_datetime(h.date)
    h=h[h.date<today.normalize()]
    bench=h[h.ticker.eq('^BVSP')].set_index('date').sort_index()
    if bench.empty:raise ValueError('Benchmark vazio')
    last=bench.index.max()
    if (today-last).days>10:raise ValueError('Benchmark sem cotação recente')
    # Calendário de pregões fechado, usado também para calcular liquidez e cobertura.
    last_days=bench.index[-liquidity_days:]
    before=h[h.date<today.to_period('M').start_time]
    b=before[before.ticker.eq('^BVSP')].set_index('date').sort_index()
    dates=b.groupby(b.index.to_period('M')).apply(lambda d:d.index[-1])
    if len(dates)<window+1:raise ValueError('Histórico do benchmark insuficiente; amplie a data inicial')
    months=pd.period_range(dates.index.min(),dates.index.max(),freq='M')
    p=before.pivot(index='date',columns='ticker',values='adj_close').reindex(pd.DatetimeIndex(dates.to_numpy()))
    p.index=dates.index;p=p.reindex(months)
    returns=p.pct_change(fill_method=None).tail(window)
    groups={t:d.sort_values('date') for t,d in h.groupby('ticker')}
    rows=[]
    for _,u in universe.iterrows():
        t=u.ticker;reason=[];d=groups.get(t,pd.DataFrame())
        liq=np.nan;price=np.nan;nmonths=0;mean=np.nan;vol=np.nan;sharpe=np.nan
        if statuses.get(t)!='ok':reason.append('Falha/ausência de atualização; ativo não avaliado')
        if d.empty:reason.append('Sem histórico')
        else:
            price=float(d.close.iloc[-1])
            if d.date.iloc[-1]!=last:reason.append('Última cotação diferente do benchmark')
            aligned=d.set_index('date').reindex(last_days)
            valid=aligned[['close','volume']].notna().all(axis=1)
            # Missing observação não vira volume zero; ativos com baixa cobertura são rejeitados.
            if len(last_days)<liquidity_days or valid.sum()<liquidity_days*.90:
                reason.append('Cobertura diária insuficiente para liquidez')
            liq=float((aligned.close*aligned.volume).mean())
            if not np.isfinite(liq) or liq<min_liquidity:reason.append('Liquidez média abaixo do mínimo')
            r=returns[t] if t in returns else pd.Series(dtype=float)
            nmonths=int(r.notna().sum())
            if nmonths<window:reason.append('Meses completos insuficientes')
            else:
                mean=float(r.mean());vol=float(r.std(ddof=1))
                if vol<=1e-12 or not np.isfinite(vol):reason.append('Volatilidade inválida')
                else:sharpe=(mean-((1+rf_annual)**(1/12)-1))/vol
            if not np.isfinite(price) or price<=0:reason.append('Preço inválido')
            elif np.ceil(price*100)/100*lot>capital*max_asset+1e-8:reason.append('Lote excede o máximo monetário por ativo')
        rows.append(dict(ticker=t,name=u['name'],sector_pt=u.sector_pt,segment=u.segment,prefix=u.prefix,
             mean_liquidity_brl=liq,reference_price=price,complete_months=nmonths,
             historical_mean_month=mean,volatility_month=vol,historical_sharpe_month=sharpe,
             eligible=not reason,exclusion='; '.join(reason)))
    report=pd.DataFrame(rows)
    key,ascending={'liquidity':('mean_liquidity_brl',False),'sharpe':('historical_sharpe_month',False),
                   'volatility':('volatility_month',True)}[ranking]
    eligible=report[report.eligible].sort_values([key,'ticker'],ascending=[ascending,True])
    if one_per_prefix:
        # ON/PN/units com mesmo prefixo: mantém a classe mais líquida, independentemente do ranking.
        liquid=eligible.sort_values(['mean_liquidity_brl','ticker'],ascending=[False,True])
        keep=set(liquid.drop_duplicates('prefix').ticker)
        removed=report.eligible & ~report.ticker.isin(keep)
        report.loc[removed,'eligible']=False
        report.loc[removed,'exclusion']='Outra classe com o mesmo prefixo é mais líquida'
        eligible=eligible[eligible.ticker.isin(keep)]
    eligible=eligible.copy();eligible['rank']=np.arange(1,len(eligible)+1)
    return eligible,report,returns

def select_ranked(eligible,max_assets=10,max_asset=.25,max_segment=.5,group_column='segment'):
    """Seleção por ranking com MILP de viabilidade de pesos. Não é ótimo global de risco/retorno."""
    n=len(eligible)
    if n==0:raise ValueError('Nenhum ativo passou nos critérios; veja exclusões')
    if max_assets<1 or not 0<max_asset<=1 or not 0<max_segment<=1:raise ValueError('Limites inválidos')
    # z=ativos selecionados, w=pesos contínuos apenas para assegurar viabilidade.
    score=np.arange(n,0,-1,dtype=float)
    c=np.r_[-score,np.zeros(n)]
    rows=[np.r_[np.ones(n),np.zeros(n)],np.r_[np.zeros(n),np.ones(n)]]
    lo=[1,1];hi=[min(max_assets,n),1]
    for i in range(n):
        a=np.zeros(2*n);a[i]=-max_asset;a[n+i]=1
        rows.append(a);lo.append(-np.inf);hi.append(0)
    for g in eligible[group_column].unique():
        mask=eligible[group_column].eq(g).to_numpy(dtype=float)
        rows.append(np.r_[np.zeros(n),mask]);lo.append(-np.inf);hi.append(max_segment)
    z=milp(c,integrality=np.r_[np.ones(n),np.zeros(n)],bounds=Bounds(np.zeros(2*n),np.ones(2*n)),
        constraints=LinearConstraint(np.array(rows),lo,hi),options={'time_limit':30,'mip_rel_gap':0.})
    if z.x is None:raise ValueError('Limites de quantidade, ativo e segmento inviáveis; aumente limites ou selecione mais segmentos')
    selected=eligible.iloc[np.flatnonzero(z.x[:n]>.5)].copy()
    selected['selection_solver_optimal']=bool(z.success)
    if group_column=='sector_pt':selected['segment']=selected.sector_pt
    return selected

def run_b3(history,universe,log,capital,window=24,min_liquidity=1e6,max_assets=10,
           max_asset=.25,max_segment=.5,ranking='liquidity',one_per_prefix=True,
           allow_partial=True,objective='min_variance',rf_annual=.10,market_scenario=None,
           lot=1,fee_rate=0.,fixed_fee=0.,asof=None,simulations=10000,group_column='segment',
           diagnostic_folder=None,fundamental_scores=None):
    market_ranking='liquidity' if ranking=='fundamental' else ranking
    elig,report,_=screen_assets(history,universe,log,window,min_liquidity,
         allow_partial=allow_partial,one_per_prefix=one_per_prefix,capital=capital,
         lot=lot,max_asset=max_asset,asof=asof,ranking=market_ranking,rf_annual=rf_annual)
    if ranking=='fundamental':
        if fundamental_scores is None or fundamental_scores.empty:
            raise ValueError('Ranking fundamentalista requer fundamentos do pool de candidatos')
        fields=['ticker','period','roe','roa_ebit','gaf','gao','ki_effective',
                'fundamental_coverage','fundamental_score']
        available=[c for c in fields if c in fundamental_scores]
        scores=fundamental_scores[available].drop_duplicates('ticker',keep='last')
        pool=set(scores.ticker)
        elig=elig.merge(scores,on='ticker',how='left')
        insufficient=elig.fundamental_score.isna()
        outside=~elig.ticker.isin(pool)
        report=report.merge(scores,on='ticker',how='left')
        report.loc[report.ticker.isin(elig.loc[insufficient & ~outside,'ticker']),'eligible']=False
        report.loc[report.ticker.isin(elig.loc[insufficient & ~outside,'ticker']),'exclusion']='Fundamentos insuficientes: menos de 3 dos 5 componentes'
        report.loc[report.ticker.isin(elig.loc[outside,'ticker']),'eligible']=False
        report.loc[report.ticker.isin(elig.loc[outside,'ticker']),'exclusion']='Fora do pool fundamentalista pré-ordenado por liquidez'
        elig=(elig.loc[~insufficient].sort_values(['fundamental_score','ticker'],ascending=[False,True])
              .reset_index(drop=True))
        elig['rank']=np.arange(1,len(elig)+1)
        if elig.empty:
            raise ValueError('Nenhum ativo possui cobertura fundamental suficiente no pool consultado')
    if diagnostic_folder is not None:
        save_csv(report,Path(diagnostic_folder)/'triagem.csv')
        save_csv(elig,Path(diagnostic_folder)/'ranking_elegiveis.csv')
    chosen=select_ranked(elig,max_assets,max_asset,max_segment,group_column)
    result=run_portfolio(history,chosen,capital,window,objective,max_asset,max_segment,
         rf_annual,market_scenario,lot,fee_rate,fixed_fee,asof,simulations)
    result.update(screening=report,eligible=elig,selected=chosen)
    summary=result['summary']
    summary.update(universe_considered=len(universe),eligible_count=len(elig),
        selected_count=len(chosen),positive_weight_count=int((result['plan'].target_weight>1e-6).sum()),
        bought_count=int((result['plan'].quantity>0).sum()),download_failed_count=int(log.ticker.isin(universe.ticker).mul(log.status.ne('ok')).sum()),
        ranking=ranking,selection_solver_optimal=bool(chosen.selection_solver_optimal.all()),
        min_mean_liquidity_brl=min_liquidity,max_assets=max_assets,group_limit_column=group_column,
        fundamental_rule=('Score relativo de 5 componentes, pesos iguais e cobertura mínima 3/5'
                          if ranking=='fundamental' else 'Não aplicado'),
        caveat='Seleção por ranking seguida de otimização de pesos; não certifica ótimo global entre todas as combinações de ações')
    return result
