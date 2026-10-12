# Carteira B3 — AlphaLab

Aplicação de pesquisa que descobre ações e units em um catálogo público, atualiza o histórico,
filtra ativos por cobertura e liquidez, estima pesos e converte a carteira teórica em quantidades
inteiras compatíveis com o capital informado.

O projeto deriva do notebook `Carteira_B3_Automatica_V2(1).ipynb`, agora separado em motor,
interface, testes e notebook revisável. A identidade visual segue o kit AlphaLab usado nos demais
projetos: azul-marinho `#0F2D5B`, azul `#315FC3`, destaque `#4F8CFF` e fundo `#F6F8FC`.

## O que o projeto faz

- percorre todas as páginas anunciadas pelo catálogo da brapi;
- mantém apenas ações e units e registra os tipos excluídos;
- atualiza `historico.csv` incrementalmente, por ativo, sem apagar séries válidas quando uma consulta falha;
- usa meses completos comuns aos ativos e ao Ibovespa, sem preencher preços ausentes;
- filtra por liquidez, cobertura, preço recente, setor e segmento;
- seleciona candidatos por ranking com verificação de viabilidade por MILP;
- otimiza pesos por variância mínima ou Sharpe estimado;
- converte pesos em quantidades inteiras, respeitando capital e limites por ativo/grupo;
- salva configuração, diagnósticos, triagem, carteira, estatísticas e resumo da execução.

## Executar o aplicativo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

No Windows PowerShell, ative o ambiente com `.venv\Scripts\Activate.ps1`.

Na primeira abertura, clique em **Atualizar catálogo público**. Depois selecione o escopo, informe
o capital e execute a montagem. Consultar centenas de ativos pode demorar e provocar limitação da
fonte; o checkpoint permite retomar sem perder as séries já salvas.

## Usar no Colab

O notebook limpo está em [`notebooks/Carteira_B3_Automatica_V2.ipynb`](notebooks/Carteira_B3_Automatica_V2.ipynb).
Ele preserva o fluxo de persistência no Google Drive, mas importa o motor versionado de `src/`.
Clone o repositório no Colab e execute o notebook a partir da raiz clonada.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```

Os testes cobrem paginação do universo, exclusão de instrumentos, preservação do histórico em falha
de rede, alinhamento mensal estrito, limites da otimização e viabilidade da seleção.

## Estrutura

```text
.
├── app.py                         # interface Streamlit
├── src/
│   ├── engine.py                  # retornos, modelo, pesos e compra inteira
│   └── universe_b3.py             # catálogo, coleta, triagem e seleção
├── notebooks/                     # Colab sem outputs e sem motor duplicado
├── tests/                         # testes offline
├── scripts/                       # importação/proveniência do notebook original
├── assets/                        # identidade visual AlphaLab
├── .streamlit/config.toml         # tema institucional
└── .github/workflows/ci.yml       # validação automática
```

Detalhes das fronteiras e fontes estão em [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Publicar no Streamlit Community Cloud

Crie um app apontando para este repositório, branch desejada e arquivo `app.py`. O diretório `data/`
do Streamlit Cloud pode ser reiniciado; para uso contínuo, configure `ALPHALAB_DATA_DIR` em volume
persistente ou mantenha o fluxo do Colab/Drive. Não há segredos obrigatórios nesta versão.

## Limitações e risco

O universo corresponde ao catálogo entregue pelo provedor e não é uma certificação das listagens
oficiais da B3. Dados públicos podem conter atraso, omissões e revisões. A seleção por ranking seguida
da otimização de pesos não certifica o ótimo global entre todas as combinações nem prevê retorno
futuro. Este software não envia ordens e não constitui recomendação de investimento.

Não há licença de reutilização concedida neste repositório público.
