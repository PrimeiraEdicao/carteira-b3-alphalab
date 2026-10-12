# Arquitetura

## Fluxo

1. `src/universe_b3.py` pagina o catálogo público da brapi e mantém apenas ações e units.
2. O mesmo módulo atualiza as séries do Yahoo Finance por ativo, com checkpoint em CSV e circuito para HTTP 429.
3. A triagem exige cobertura mensal comum, cotação na data do benchmark e liquidez mínima.
4. `src/engine.py` estima o modelo de mercado, decompõe o risco, otimiza pesos e resolve a compra inteira por MILP.
5. Quando solicitado, o app consulta demonstrações anuais apenas para um pool pré-selecionado por liquidez e calcula um score fundamentalista explícito.
6. `app.py` coleta parâmetros, executa o fluxo e oferece os diagnósticos da execução em ZIP.

## Fronteiras

- O catálogo é o universo entregue pela API consultada; não certifica todas as listagens oficiais da B3.
- O Yahoo Finance é uma fonte pública de conveniência, não um feed oficial de execução.
- O aplicativo não possui integração com corretora e não envia ordens.
- `data/` é estado local/efêmero e não é versionado. O notebook mantém o fluxo de persistência no Google Drive.
- Os testes unitários não acessam a rede; respostas externas são simuladas.
- O score fundamentalista é transversal ao pool corrente e exige ao menos três de cinco componentes;
  ele não possui validação preditiva nesta versão.
- A decomposição de risco usa os pesos teóricos e reconcilia as contribuições componentes com a
  volatilidade total da carteira.

## Fonte canônica

O primeiro commit foi extraído de `Carteira_B3_Automatica_V2(1).ipynb`. Depois da extração,
`src/` é a fonte canônica do motor. A cópia em `notebooks/` importa esses módulos e teve saídas e
contadores removidos para produzir diffs revisáveis.
