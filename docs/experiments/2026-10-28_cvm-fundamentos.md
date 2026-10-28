# Protocolo — fundamentos CVM na carteira B3

## Identidade

- `experiment_id`: `cvm-fundamentos-20261028-r1`
- título: Análise horizontal, vertical e política fundamentalista setorial
- responsável: AlphaLab / Daniel Souza
- freeze: 2026-10-28, America/Sao_Paulo
- branch planejada: `feature/cvm-fundamentals-analysis`
- baseline: seleção atual por liquidez/Sharpe/volatilidade e otimização de risco
- evidência anterior: ranking anual Yahoo existente, sem `available_at` histórico

## Hipótese

- problema econômico: o motor de risco não informa a trajetória financeira do negócio e o ranking fundamentalista atual tem baixa rastreabilidade temporal;
- informação nova: DFP anual estruturada da CVM, data de recebimento, análise horizontal, análise vertical e normalização setorial;
- H0: a política CVM não melhora seleção/robustez fora da amostra em relação ao baseline;
- H1: a política CVM melhora métricas pareadas fora da amostra sem piorar drawdown/cauda;
- ação nesta entrega: observar e permitir seleção challenger somente por escolha explícita;
- não é repetição: adiciona fonte oficial, dimensões de tendência, estrutura patrimonial e `available_at`.

## Dados e causalidade

- população: ações e units elegíveis no catálogo do aplicativo; instrumentos sem correspondência CVM ficam sem score;
- unidade: companhia × exercício anual, replicada para a classe de ação selecionada;
- universo upstream: pool pré-selecionado por liquidez;
- período inicial do painel: cinco DFPs anuais encerradas;
- `feature_available_at`: `DT_RECEB` do documento DFP; a fotografia atual usa somente versões recebidas até a consulta;
- chave estável: `CD_CVM + DT_REFER + VERSAO`, com ticker apenas como mapeamento de instrumento;
- duplicatas: última versão recebida por companhia e data de referência;
- faltantes/não finitos: permanecem ausentes; não viram zero; score exige cobertura mínima;
- demonstração: consolidada (`*_con_`), ordem do exercício mais recente;
- escala: valores convertidos para BRL conforme `ESCALA_MOEDA`.

## Política challenger congelada para implementação

- não financeiras:
  - rentabilidade 40%: ROE, ROA-EBIT e margem EBIT;
  - tendência 30%: crescimento anual de receita, EBIT e fluxo de caixa operacional;
  - resiliência 30%: liquidez corrente, menor dívida/ativos, patrimônio/ativos e conversão caixa/lucro;
  - cobertura mínima: 6 componentes e presença nas três dimensões.
- financeiras:
  - rentabilidade 40%: ROE, ROA-lucro e margem líquida;
  - tendência 30%: crescimento anual de receita e lucro;
  - resiliência 30%: patrimônio/ativos;
  - cobertura mínima: 4 componentes e presença nas três dimensões.
- transformação: winsorização transversal 5%/95%, percentis por setor quando houver ao menos três empresas; caso contrário, pool total.
- desempate: ticker em ordem crescente.

## Métricas e gates futuros

- comparador: mesma data de rebalanceamento, universo, restrições, custos e motor de pesos;
- mínimo: 30 decisões alteradas, múltiplos períodos e setores;
- avaliar: retorno líquido, Sharpe, drawdown, turnover, custos, cauda 20/25 e estabilidade temporal/setorial;
- `KEEP`: vantagem economicamente relevante e estável fora da amostra, sem piora material de drawdown/cauda;
- `REJECT`: erro causal ou falha dos gates pré-fixados;
- `INVESTIGATE`: implementação/painel concluídos sem evidência prospectiva suficiente.

## Proibições e limite desta entrega

- o score não será padrão e não será promovido automaticamente;
- nenhum resultado desta implementação será chamado de validação independente;
- não altera conta REAL, execução, serviços ou artefatos congelados;
- o painel descritivo pode ser usado independentemente da política de seleção.
