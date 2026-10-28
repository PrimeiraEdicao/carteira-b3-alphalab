# Arquitetura

## Fluxo

1. `src/universe_b3.py` pagina o catálogo público da brapi e mantém apenas ações e units.
2. O mesmo módulo atualiza as séries do Yahoo Finance por ativo, com checkpoint em CSV e circuito para HTTP 429.
3. A triagem exige cobertura mensal comum, cotação na data do benchmark e liquidez mínima.
4. `src/engine.py` estima o modelo de mercado, decompõe o risco, otimiza pesos e resolve a compra inteira por MILP.
5. `src/cvm_fundamentals.py` baixa DFPs consolidadas da CVM, escolhe a última versão recebida,
   mapeia companhia e ticker com limiar conservador e calcula análises horizontal/vertical.
6. Quando solicitado, a política challenger ordena o pool líquido por rentabilidade, tendência e
   resiliência normalizadas por setor; o ranking padrão continua inalterado.
7. `app.py` coleta parâmetros, executa o fluxo, abre a análise da companhia selecionada e oferece
   diagnósticos e feature store da execução em ZIP.

## Fronteiras

- O catálogo é o universo entregue pela API consultada; não certifica todas as listagens oficiais da B3.
- O Yahoo Finance é uma fonte pública de conveniência, não um feed oficial de execução.
- O aplicativo não possui integração com corretora e não envia ordens.
- `data/` é estado local/efêmero e não é versionado. O notebook mantém o fluxo de persistência no Google Drive.
- Os testes unitários não acessam a rede; respostas externas são simuladas.
- O score fundamentalista é transversal ao pool corrente e exige ao menos três de cinco componentes;
  ele não possui validação preditiva nesta versão.
- A DFP é a fonte oficial para o painel anual; PDFs e notas explicativas não são processados.
- `available_at` vem da data de recebimento do documento. O challenger CVM ainda exige backtest
  point-in-time antes de qualquer decisão de promoção.
- O vínculo automático ticker–companhia falha fechado abaixo do limiar e aceita override explícito
  por `ticker,cd_cvm`; o ticker nunca é tratado como identidade contábil.
- A decomposição de risco usa os pesos teóricos e reconcilia as contribuições componentes com a
  volatilidade total da carteira.

## Fonte canônica

O primeiro commit foi extraído de `Carteira_B3_Automatica_V2(1).ipynb`. Depois da extração,
`src/` é a fonte canônica do motor. A cópia em `notebooks/` importa esses módulos e teve saídas e
contadores removidos para produzir diffs revisáveis.

