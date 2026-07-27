# football-data.co.uk — reserva de dados brutos

Espelho local de tudo que está disponível em [football-data.co.uk/data.php](https://www.football-data.co.uk/data.php), baixado em 27/07/2026. Fonte gratuita, sem chave de API — mesma já usada pelo pipeline (`ingestao_odds_footballdata.py`, `ingestao_odds_footballdata_brasil.py`). Guardado aqui como **reserva/backup** — a maioria dos arquivos ainda não foi processada/importada pro banco (ver seção "O que já está em uso" abaixo).

## Estrutura

```
football_data_co_uk/
├── notes.txt                      # dicionário de colunas oficial da fonte (baixado de football-data.co.uk/notes.txt)
├── principais_ligas/              # "Main Leagues" — 1 arquivo por divisão POR TEMPORADA
│   ├── 1993-1994/
│   │   ├── D1.csv
│   │   └── ...
│   └── 2025-2026/
│       ├── B1.csv, D1.csv, D2.csv, E0.csv, E1.csv, E2.csv, E3.csv, EC.csv,
│       │   F1.csv, F2.csv, G1.csv, I1.csv, I2.csv, N1.csv, P1.csv,
│       │   SC0.csv, SC1.csv, SC2.csv, SC3.csv, SP1.csv, SP2.csv, T1.csv
├── ligas_extra/                   # "Extra Leagues" — 1 arquivo por país com TODAS as temporadas juntas
│   ├── ARG.csv (Argentina), AUT.csv (Áustria), BRA.csv (Brasil),
│   │   CHN.csv (China), DNK.csv (Dinamarca), FIN.csv (Finlândia),
│   │   IRL.csv (Irlanda), JPN.csv (Japão), MEX.csv (México),
│   │   NOR.csv (Noruega), POL.csv (Polônia), ROU.csv (Romênia),
│   │   RUS.csv (Rússia), SWE.csv (Suécia), SWZ.csv (Suíça), USA.csv (EUA/MLS)
└── scripts/
    └── README.md                  # orientações de como tratar/ingerir cada tipo de arquivo
```

## Legenda de códigos de divisão (`principais_ligas/`)

| Código | Liga | Código | Liga |
|---|---|---|---|
| E0 | Inglaterra — Premier League | SC0 | Escócia — Premiership |
| E1 | Inglaterra — Championship | SC1 | Escócia — Championship |
| E2 | Inglaterra — League One | SC2 | Escócia — League One |
| E3 | Inglaterra — League Two | SC3 | Escócia — League Two |
| EC | Inglaterra — National League (Conference) | D1 | Alemanha — Bundesliga |
| SP1 | Espanha — La Liga | D2 | Alemanha — 2. Bundesliga |
| SP2 | Espanha — Segunda División | F1 | França — Ligue 1 |
| I1 | Itália — Serie A | F2 | França — Ligue 2 |
| I2 | Itália — Serie B | B1 | Bélgica — Pro League |
| N1 | Holanda — Eredivisie | P1 | Portugal — Primeira Liga |
| T1 | Turquia — Süper Lig | G1 | Grécia — Super League |

Cobertura: 1993/94 até 2025/26 (32 temporadas), mas cada divisão só tem dado completo a partir do ano em que foi incluída na fonte (nem todas entraram em 1993 — algumas divisões menores começam décadas depois, ex: `EC` só a partir de meados dos anos 2000). Estatísticas de jogo (chutes, escanteios, cartões) só a partir de 2000/01; odds de fechamento (`...C` no fim do nome da coluna) só a partir de 2019/20, exceto Pinnacle que tem fechamento desde 2012/13.

## Legenda de países (`ligas_extra/`)

| Código | País | Código | País |
|---|---|---|---|
| ARG | Argentina | NOR | Noruega |
| AUT | Áustria | POL | Polônia |
| BRA | Brasil (Série A) | ROU | Romênia |
| CHN | China | RUS | Rússia |
| DNK | Dinamarca | SWE | Suécia |
| FIN | Finlândia | SWZ | Suíça |
| IRL | Irlanda | USA | EUA (MLS) |
| JPN | Japão | MEX | México |

Diferença de formato importante: cada arquivo já traz TODAS as temporadas num único CSV (filtra pela coluna `Season`), tem `Date` (dd/mm/yyyy, confirmado funcionando em `ingestao_odds_footballdata_brasil.py`), mas só tem odds de **fechamento** (sufixo "C" nas colunas — sem pré-fechamento) e não tem estatísticas de jogo (chutes/escanteios/cartões) — só placar e odds 1X2.

## O que já está em uso no pipeline (não é só reserva)

- **`principais_ligas` (E0/SP1/I1/D1/F1 apenas)**: já processado via `arquivos_do_claude/ingestao_odds_footballdata.py`, que baixa DIRETO da fonte a cada execução (não lê os arquivos daqui) — Premier League, La Liga, Serie A, Bundesliga, Ligue 1.
- **`ligas_extra/BRA.csv`**: já processado via `arquivos_do_claude/ingestao_odds_footballdata_brasil.py`, 19.890 linhas gravadas em `odds_market` cobrindo 2019-2026 (ver `CONTEXTO_PROJETO.md`).
- **Todo o resto** (as outras 21 divisões de `principais_ligas` — E1/E2/E3/EC/SC0-3/D2/I2/N1/P1/T1/G1/F2/SP2/B1 — e as outras 15 ligas_extra) está aqui só como reserva, sem processamento nenhum ainda. A maioria dessas competições nem tem `leagues`/`teams`/`matches` cadastrados no banco — ingerir odds delas exigiria primeiro importar as partidas (mesmo passo que falta pra Argentina/Bélgica/Colômbia/etc. discutido com o dataset do Kaggle).

## Licença / termos de uso

O site não publica uma licença formal — os dados são disponibilizados como "FREE" pra uso pessoal em análise de apostas (mesmo espírito do restante do projeto, particular e sem pretensão de tornar público). Ver `notes.txt` pra créditos das fontes originais (XScores, BBC, Flashscore, ESPN, Betbrain, Oddsportal etc.).
