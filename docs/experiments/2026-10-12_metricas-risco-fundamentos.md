# Protocolo — métricas de risco e ranking fundamentalista

## Identidade

- `experiment_id`: CARTEIRA_B3_METRICAS_FUNDAMENTOS_V1
- título: Exposição de métricas de mercado, contribuição de risco e ranking fundamentalista opcional
- responsável: AlphaLab / Daniel Vieira de Souza
- freeze: 2026-10-12, America/Sao_Paulo
- branch: `feat/metricas-risco-fundamentos`
- baseline: `main` no merge `c583250a3ea95020e2cb937d3b2297e23c95a2dd`
- hipótese: métricas transparentes e um ranking fundamentalista explícito melhoram auditabilidade e permitem comparar uma seleção de qualidade com o ranking de liquidez
- evidência anterior: motor atual usa liquidez, Sharpe/volatilidade, modelo de mercado, covariância e otimização; as razões fundamentalistas existem, mas não participam do fluxo do app

## Hipótese e limites

- problema: alpha, beta, R², significância, Sharpe e contribuição de risco não aparecem integralmente na interface; fundamentos não têm regra explícita de seleção
- H0: o ranking fundamentalista não oferece evidência suficiente para substituir o ranking padrão de liquidez
- H1: após teste histórico/forward separado, o ranking fundamentalista pode melhorar qualidade/robustez sem concentração ou degradação material de risco
- ação nesta branch: somente disponibilizar métricas e ranking opcional; nenhuma alegação de superioridade
- baseline operacional permanece: ranking por liquidez + variância mínima

## Dados e disponibilidade

- preços e retornos: Yahoo Finance, somente meses completos anteriores ao mês corrente
- benchmark: Ibovespa (`^BVSP`)
- fundamentos: demonstrações anuais mais recentes disponíveis no momento da consulta
- unidade: ação/unit no catálogo público filtrado
- pool fundamentalista: ativos elegíveis pré-ordenados por liquidez; tamanho configurável, padrão automático de `max(20, 3 × máximo de ativos)`, limitado a 60
- causalidade: o ranking corrente usa somente dados já disponíveis na execução; não está autorizado para backtest histórico sem datas de publicação das demonstrações
- faltantes: não imputar zero; exigir pelo menos 3 dos 5 componentes

## Regra fundamentalista congelada

Para o último exercício disponível de cada ticker:

1. ROE: percentil crescente, peso 20%
2. ROA baseado em EBIT: percentil crescente, peso 20%
3. custo da dívida: percentil decrescente, peso 20%; usa `Ki` revisado e, na ausência, o proxy `|despesa de juros| / dívida total`
4. GAF: percentil decrescente de `|GAF - 1|`, peso 20%
5. GAO: percentil decrescente de `|GAO - 1|`, peso 20%

O score é a média ponderada dos componentes disponíveis, renormalizada. Exige cobertura mínima de 3/5. O score é relativo ao pool consultado, não uma nota absoluta. GAF/GAO são tratados como estabilidade em torno de 1, não como “quanto maior, melhor”.

## Métricas expostas

- alpha mensal, beta, R², p-valor e erro-padrão do beta
- média, retorno condicional estimado, volatilidade e Sharpe mensais
- contribuição marginal, contribuição componente e participação percentual no risco
- ROE, ROA-EBIT, GAF, GAO, Ki efetivo, cobertura e score fundamentalista

Contribuição percentual ao risco:

[
PCR_i = rac{w_i(\Sigma w)_i}{w^T\Sigma w}
]

## Parâmetros e reprodutibilidade

- pesos do score: 20% por componente
- cobertura mínima: 3 componentes
- seed Monte Carlo: 42
- simulações: 10.000
- objetivo padrão: variância mínima
- ranking padrão: liquidez
- nenhuma mudança automática na carteira padrão

## Critérios desta implementação

- KEEP/REFERENCE: testes unitários passam; métricas reconciliam com as fórmulas; ranking fundamental é opcional; faltantes falham de forma explícita
- REJECT/DO_NOT_DEPLOY: erro de fórmula, imputação silenciosa, violação de limites ou alteração do baseline padrão
- INVESTIGATE/REFERENCE: implementação correta, mas sem evidência comparativa de desempenho — classificação esperada para a hipótese econômica

## Proibições

- não promover o score como preditivo
- não usar esta implementação em conta REAL
- não chamar a amostra corrente de validação independente
- não fazer merge sem autorização humana
