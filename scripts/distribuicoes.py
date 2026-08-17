#!/usr/bin/env python3
"""Núcleo paramétrico do modelo misto: parâmetros de distribuição -> mercados.

A ideia do modelo híbrido é separar em duas camadas o que hoje está
misturado no projeto:

  CAMADA 1 (fora daqui, `treinar_modelo_hibrido.py`): ML estima os
      PARÂMETROS -- λ dos gols de cada equipe, ρ do Dixon-Coles, λ e
      dispersão dos escanteios, α/β do split.
  CAMADA 2 (este módulo): os parâmetros viram a distribuição conjunta, e
      TODOS os mercados saem dela por integração.

O ganho estrutural é coerência: hoje cada mercado tem um classificador
próprio (`TARGETS` em `treinar_modelo_custom.py`), então o P(over 2.5) de
um modelo não bate com o que o 1X2 do MESMO modelo implica. Derivando tudo
da mesma matriz de placares, essa incoerência deixa de ser possível.

Este módulo é PURO -- não conhece Supabase, não faz I/O, não importa nada
do projeto. Isso é de propósito: dá pra validar as identidades matemáticas
(soma 1, casos-limite, consistência entre mercados) sem tocar no banco,
mesma disciplina do "núcleo do modelo" de `modelo_dixon_coles.py`.

Escolha de distribuição por estatística -- medida no banco (2.335-3.548
partidas por liga, 6 ligas), não herdada de suposição:

  Gols totais       var/média = 0,96-1,02  -> Poisson (+ correção τ de
                                              Dixon-Coles nos placares baixos)
  Escanteios totais var/média = 1,15-1,24  -> Binomial Negativa (leve
                                              superdispersão)
  Escanteios/time   var/média = 1,54-1,68  -> sai do split Beta-Binomial,
                                              não é modelado direto

Por que escanteios são modelados como TOTAL + SPLIT, e não como duas
marginais independentes: `corr(escanteios_casa, escanteios_fora)` medida é
NEGATIVA (-0,24 a -0,31 nas 6 ligas) -- posse é soma zero, quem domina faz
muito escanteio enquanto o adversário faz pouco. Somar duas Binomiais
Negativas independentes SUPERESTIMARIA a variância do total. Já o split
Beta-Binomial condicionado ao total gera essa correlação negativa
naturalmente, sem precisar impor nada.

Atenção a um detalhe do projeto: o achado #5 do `CONTEXTO_PROJETO.md`
registra superdispersão de 1,60-1,76 em escanteios. Esse número é POR
TIME; aplicá-lo ao TOTAL (como `api/corners-model.js` faz hoje) engorda a
cauda além do devido. Aqui a dispersão do total é sempre estimada em cima
de totais.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import betaln, gammaln
from scipy.stats import nbinom, poisson

# Teto da matriz de placares. 10 gols por equipe deixa massa residual
# desprezível (P(>10) < 1e-6 pra qualquer λ realista de futebol) e é o
# mesmo teto já usado em `modelo_dixon_coles.py` e em AnaliseEvento.jsx.
MAX_GOLS = 10
# Teto de escanteios num jogo. O recorde registrado em ligas de elite fica
# na casa dos 30; 40 dá folga larga sem custar nada (vetor de 41 posições).
MAX_ESCANTEIOS = 40


# ---------------------------------------------------------------------------
# Gols: Poisson bivariada com a correção de Dixon-Coles
# ---------------------------------------------------------------------------
def tau(x, y, lam, mu, rho):
    """Correção de Dixon-Coles (1997) para os 4 placares baixos.

    A Poisson dupla independente erra sistematicamente em 0x0/1x0/0x1/1x1;
    τ reescala só essas 4 células. Mesma fórmula de
    `arquivos_do_claude/modelo_dixon_coles.py:66` e de `dixonColesTau` em
    `src/utils/poisson.js` -- as três precisam concordar (ver o golden
    fixture de paridade Python<->JS).

    Aceita escalares ou arrays (usado vetorizado no ajuste de ρ por MLE).
    """
    x, y = np.asarray(x), np.asarray(y)
    lam, mu, rho = np.asarray(lam), np.asarray(mu), np.asarray(rho)
    resultado = np.ones(np.broadcast(x, y, lam, mu, rho).shape, dtype=float)
    resultado = np.where((x == 0) & (y == 0), 1.0 - lam * mu * rho, resultado)
    resultado = np.where((x == 0) & (y == 1), 1.0 + lam * rho, resultado)
    resultado = np.where((x == 1) & (y == 0), 1.0 + mu * rho, resultado)
    resultado = np.where((x == 1) & (y == 1), 1.0 - rho, resultado)
    return resultado


def matriz_placares(lam: float, mu: float, rho: float = 0.0, max_gols: int = MAX_GOLS) -> np.ndarray:
    """Matriz (max_gols+1)² com P(placar = i x j), já renormalizada.

    `lam` = gols esperados do mandante, `mu` = do visitante -- é AQUI que o
    λ estimado por ML entra no modelo. `rho` = 0 devolve a Poisson dupla
    independente exata (usado como caso-limite de teste).
    """
    gols = np.arange(max_gols + 1)
    matriz = np.outer(poisson.pmf(gols, lam), poisson.pmf(gols, mu))

    # τ só mexe nas 4 células de placar baixo; o resto fica intacto.
    for x in (0, 1):
        for y in (0, 1):
            matriz[x, y] *= float(tau(x, y, lam, mu, rho))

    # Renormaliza: τ quebra a soma 1, e a matriz é truncada em max_gols.
    # Clip antes de normalizar porque um ρ fora dos limites de validade
    # pode gerar célula negativa -- probabilidade negativa não existe.
    matriz = np.clip(matriz, 0.0, None)
    return matriz / matriz.sum()


def _totais(max_gols: int) -> np.ndarray:
    """Matriz auxiliar com o total de gols (i+j) de cada célula."""
    gols = np.arange(max_gols + 1)
    return np.add.outer(gols, gols)


def mercados_de_gols(
    matriz: np.ndarray,
    linhas_over_under: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5, 4.5),
    linhas_handicap: tuple[float, ...] = (-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5),
    max_placares_exatos: int = 20,
) -> dict[tuple[str, str], float]:
    """Deriva TODOS os mercados de gols de uma única matriz de placares.

    Devolve `{(mercado, selecao): probabilidade}`, no mesmo vocabulário de
    `market`/`selection` já usado em `model_predictions` e em `odds_market`
    (`1X2` -> home/draw/away, `over_under_2.5` -> over/under), pra casar
    direto com as odds sem tradução.

    Como tudo sai da mesma matriz, as identidades valem por construção:
    P(home)+P(draw)+P(away) = 1, P(over X)+P(under X) = 1, e a soma dos
    placares exatos é 1.
    """
    n = matriz.shape[0] - 1
    total = _totais(n)
    saida: dict[tuple[str, str], float] = {}

    # --- 1X2 -------------------------------------------------------------
    # tril(-1) = abaixo da diagonal = mandante fez mais gols.
    p_home = float(np.tril(matriz, -1).sum())
    p_draw = float(np.trace(matriz))
    p_away = float(np.triu(matriz, 1).sum())
    saida[("1X2", "home")] = p_home
    saida[("1X2", "draw")] = p_draw
    saida[("1X2", "away")] = p_away

    # --- Dupla chance (derivada, coerente com o 1X2 acima) ---------------
    saida[("dupla_chance", "1X")] = p_home + p_draw
    saida[("dupla_chance", "12")] = p_home + p_away
    saida[("dupla_chance", "X2")] = p_draw + p_away

    # --- Over/Under em qualquer linha ------------------------------------
    for linha in linhas_over_under:
        p_over = float(matriz[total > linha].sum())
        saida[(f"over_under_{linha}", "over")] = p_over
        saida[(f"over_under_{linha}", "under")] = 1.0 - p_over

    # --- BTTS -------------------------------------------------------------
    p_btts = float(matriz[1:, 1:].sum())
    saida[("btts", "yes")] = p_btts
    saida[("btts", "no")] = 1.0 - p_btts

    # --- Faixas de gols (mesmos buckets de `codigo_faixa_gols`) ----------
    for rotulo, minimo, maximo in (("0-1", 0, 1), ("2-3", 2, 3), ("4-6", 4, 6), ("7+", 7, 10**9)):
        saida[("faixa_gols", rotulo)] = float(matriz[(total >= minimo) & (total <= maximo)].sum())

    # --- Handicap asiático ------------------------------------------------
    # Convenção: a linha é aplicada ao MANDANTE. `linha = -1.0` significa
    # "mandante começa 1 gol atrás". Linhas inteiras podem empatar
    # (push/devolução), então o mercado tem 3 saídas; linhas de meio gol
    # nunca empatam. Devolver `push` explicitamente (em vez de diluir na
    # probabilidade de vitória) é o que permite precificar a devolução.
    diferenca = np.subtract.outer(np.arange(n + 1), np.arange(n + 1))
    for linha in linhas_handicap:
        margem = diferenca + linha
        p_ganha = float(matriz[margem > 0].sum())
        p_push = float(matriz[margem == 0].sum())
        rotulo = f"handicap_{linha}"
        saida[(rotulo, "home")] = p_ganha
        saida[(rotulo, "away")] = 1.0 - p_ganha - p_push
        if p_push > 0:
            saida[(rotulo, "push")] = p_push

    # --- Placar exato -----------------------------------------------------
    # Só os N mais prováveis: a cauda tem centenas de células com
    # probabilidade irrisória que só poluiriam `model_predictions`.
    celulas = [((i, j), float(matriz[i, j])) for i in range(n + 1) for j in range(n + 1)]
    celulas.sort(key=lambda item: item[1], reverse=True)
    for (i, j), prob in celulas[:max_placares_exatos]:
        saida[("placar_exato", f"{i}-{j}")] = prob

    return saida


# ---------------------------------------------------------------------------
# Escanteios: Binomial Negativa no total + split Beta-Binomial
# ---------------------------------------------------------------------------
def _nb_pmf_vetor(media: float, disp_r: float, max_valor: int = MAX_ESCANTEIOS) -> np.ndarray:
    """PMF da Binomial Negativa parametrizada por (média, dispersão r).

    Mesma parametrização de `src/utils/distributions.js` e
    `api/_lib/negbin.js` (NB2): variância = média + média²/r, então r -> ∞
    converge pra Poisson. `scipy.stats.nbinom` usa (n, p), daí a conversão.
    """
    if disp_r <= 0 or not np.isfinite(disp_r):
        return poisson.pmf(np.arange(max_valor + 1), media)
    p = disp_r / (disp_r + media)
    return nbinom.pmf(np.arange(max_valor + 1), disp_r, p)


def _beta_binomial_pmf(n: int, alpha: float, beta: float) -> np.ndarray:
    """P(k sucessos em n) da Beta-Binomial, k = 0..n.

    É a Binomial com a probabilidade de sucesso ela própria sorteada de uma
    Beta(α, β) -- o que modela "a fatia do mandante varia de jogo pra jogo"
    em vez de ser fixa. α=β=1 devolve uniforme em 0..n (caso-limite testado).
    Calculada em log pra não estourar em n grande.
    """
    k = np.arange(n + 1)
    log_binom = gammaln(n + 1) - gammaln(k + 1) - gammaln(n - k + 1)
    return np.exp(log_binom + betaln(k + alpha, n - k + beta) - betaln(alpha, beta))


def distribuicao_conjunta_escanteios(
    media_total: float, disp_r: float, alpha: float, beta: float, max_total: int = MAX_ESCANTEIOS
) -> np.ndarray:
    """Matriz P(escanteios_casa = i, escanteios_fora = j).

    Construída como P(total) x P(casa | total), que é exatamente a
    decomposição justificada no cabeçalho: a Binomial Negativa carrega a
    superdispersão do total (var/média 1,15-1,24 medida) e a Beta-Binomial
    distribui entre as equipes gerando a correlação negativa observada
    (-0,24 a -0,31).
    """
    matriz = np.zeros((max_total + 1, max_total + 1))
    p_total = _nb_pmf_vetor(media_total, disp_r, max_total)
    for total in range(max_total + 1):
        if p_total[total] <= 0:
            continue
        p_casa = _beta_binomial_pmf(total, alpha, beta)
        for casa in range(total + 1):
            matriz[casa, total - casa] += p_total[total] * p_casa[casa]
    return matriz / matriz.sum()


def mercados_de_escanteios(
    media_total: float,
    disp_r: float,
    alpha: float,
    beta: float,
    linhas_totais: tuple[float, ...] = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5),
    linhas_por_time: tuple[float, ...] = (3.5, 4.5, 5.5, 6.5),
) -> dict[tuple[str, str], float]:
    """Mercados de escanteios derivados da conjunta.

    O total usa a Binomial Negativa direto (exato); os mercados por equipe
    e o handicap saem da conjunta com o split Beta-Binomial.
    """
    saida: dict[tuple[str, str], float] = {}

    p_total = _nb_pmf_vetor(media_total, disp_r)
    acumulado = np.cumsum(p_total)
    for linha in linhas_totais:
        # P(total > linha) = 1 - P(total <= floor(linha))
        p_over = float(1.0 - acumulado[int(np.floor(linha))])
        saida[(f"corners_over_under_{linha}", "over")] = p_over
        saida[(f"corners_over_under_{linha}", "under")] = 1.0 - p_over

    # Faixas de escanteios (mesmos buckets de `codigo_faixa_corners`)
    for rotulo, minimo, maximo in (("≤8", 0, 8), ("9-10", 9, 10), ("11-12", 11, 12), ("13+", 13, MAX_ESCANTEIOS)):
        saida[("faixa_corners", rotulo)] = float(p_total[minimo : maximo + 1].sum())

    conjunta = distribuicao_conjunta_escanteios(media_total, disp_r, alpha, beta)
    p_casa = conjunta.sum(axis=1)
    p_fora = conjunta.sum(axis=0)
    acumulado_casa, acumulado_fora = np.cumsum(p_casa), np.cumsum(p_fora)
    for linha in linhas_por_time:
        corte = int(np.floor(linha))
        saida[(f"corners_home_over_under_{linha}", "over")] = float(1.0 - acumulado_casa[corte])
        saida[(f"corners_home_over_under_{linha}", "under")] = float(acumulado_casa[corte])
        saida[(f"corners_away_over_under_{linha}", "over")] = float(1.0 - acumulado_fora[corte])
        saida[(f"corners_away_over_under_{linha}", "under")] = float(acumulado_fora[corte])

    # Quem faz mais escanteios (3 saídas -- empate é comum aqui)
    saida[("corners_1X2", "home")] = float(np.tril(conjunta, -1).sum())
    saida[("corners_1X2", "draw")] = float(np.trace(conjunta))
    saida[("corners_1X2", "away")] = float(np.triu(conjunta, 1).sum())

    return saida


# ---------------------------------------------------------------------------
# Estimação dos parâmetros que o ML NÃO estima
# ---------------------------------------------------------------------------
# λ vem do ML (um GBM com perda de Poisson por equipe). ρ, a dispersão e o
# split são poucos parâmetros globais/por liga, ajustados por máxima
# verossimilhança CONDICIONADA nesses λ -- é o que mantém o ajuste estável
# (1-2 parâmetros sobre milhares de partidas) em vez de tentar aprender
# tudo de uma vez.
def ajustar_rho_mle(lam: np.ndarray, mu: np.ndarray, gols_casa: np.ndarray, gols_fora: np.ndarray) -> float:
    """ρ do Dixon-Coles por MLE, dados os λ/μ já estimados pelo ML.

    Só as 4 células baixas dependem de ρ, então a log-verossimilhança se
    reduz a Σ log τ nessas partidas -- o resto do termo Poisson é constante
    em ρ e não precisa entrar na otimização.
    """
    lam, mu = np.asarray(lam, dtype=float), np.asarray(mu, dtype=float)
    gols_casa, gols_fora = np.asarray(gols_casa), np.asarray(gols_fora)

    def neg_log_lik(rho: float) -> float:
        t = tau(gols_casa, gols_fora, lam, mu, rho)
        # τ <= 0 é região inválida do parâmetro; empurra a otimização pra fora.
        if np.any(t <= 0):
            return 1e12
        return float(-np.sum(np.log(t)))

    # Limites clássicos de validade de ρ no Dixon-Coles.
    resultado = minimize_scalar(neg_log_lik, bounds=(-0.4, 0.4), method="bounded")
    return float(resultado.x)


def ajustar_dispersao_nb(lam: np.ndarray, real: np.ndarray) -> float:
    """Dispersão r da NB pelo resíduo de Pearson condicionado no λ da partida.

    `alpha = Σ((real-λ)² - λ) / Σλ²`, `r = 1/alpha` -- estimador padrão de
    dispersão NB2. Condicionar no λ de CADA partida é o ponto: o método dos
    momentos aplicado à distribuição agregada da liga mistura a variação
    ENTRE jogos (times diferentes têm λ diferente, o que o modelo já
    captura) com a variação DENTRO de um jogo, inflando a dispersão. Esse
    exato erro já foi corrigido uma vez neste projeto, em
    `api/corners-model.js` (achado #7 do CONTEXTO_PROJETO.md) -- não repetir.

    Devolve `inf` quando os dados não mostram superdispersão nenhuma
    (alpha <= 0), que é a NB degenerando em Poisson.
    """
    lam, real = np.asarray(lam, dtype=float), np.asarray(real, dtype=float)
    alpha = float(np.sum((real - lam) ** 2 - lam) / np.sum(lam**2))
    return float("inf") if alpha <= 0 else 1.0 / alpha


# Concentração máxima da Beta. Acima disso o split é indistinguível de uma
# Binomial de p fixo, e valores enormes só criam instabilidade numérica.
CONCENTRACAO_MAXIMA = 1000.0


def ajustar_beta_binomial(total: np.ndarray, casa: np.ndarray) -> tuple[float, float]:
    """α/β do split Beta-Binomial pelo método dos momentos.

    ATENÇÃO ao ponto que torna isso não-óbvio: a variância da fatia
    OBSERVADA (`casa/total`) NÃO é a variância da Beta. Ela carrega, por
    cima, o ruído binomial de amostragem -- num jogo de 6 escanteios a
    fatia só pode assumir 7 valores, e essa granularidade entra na
    variância amostral. Estimar α/β direto dessa variância superestima a
    dispersão e devolve uma Beta larga demais (num teste com α=6, β=5
    sintéticos, a versão ingênua recuperava α≈2,5/β≈2,1 -- concentração
    4,5 em vez de 11).

    Decompondo, com p ~ Beta(μ, κ) e casa|total ~ Binomial(total, p):

        Var(fatia) = E[1/total]·E[p(1-p)] + Var(p)
                   = μ(1-μ)·[E[1/total]·κ/(κ+1) + 1/(κ+1)]

    Chamando V = Var(fatia)/(μ(1-μ)) e m1 = E[1/total], isso inverte em
    forma fechada:

        κ = (1 - V) / (V - m1)

    O termo `m1` é exatamente o desconto do ruído binomial. Método dos
    momentos (e não MLE) porque é fechado, estável e sobra pra 2
    parâmetros.
    """
    total, casa = np.asarray(total, dtype=float), np.asarray(casa, dtype=float)
    valido = total > 0
    total, casa = total[valido], casa[valido]
    fatia = casa / total

    media = float(np.mean(fatia))
    if not 0.0 < media < 1.0:  # split degenerado (uma equipe leva sempre tudo)
        media = min(max(media, 1e-6), 1.0 - 1e-6)
        return media * CONCENTRACAO_MAXIMA, (1.0 - media) * CONCENTRACAO_MAXIMA

    variancia_normalizada = float(np.var(fatia, ddof=1)) / (media * (1.0 - media))
    media_inverso_total = float(np.mean(1.0 / total))

    # V <= m1: dispersão observada no nível do ruído binomial puro ou
    # abaixo -- não há superdispersão de Beta pra estimar.
    # V >= 1: dispersão além do máximo que uma Beta comporta.
    if not media_inverso_total < variancia_normalizada < 1.0:
        concentracao = CONCENTRACAO_MAXIMA
    else:
        concentracao = (1.0 - variancia_normalizada) / (variancia_normalizada - media_inverso_total)
        concentracao = min(max(concentracao, 1e-3), CONCENTRACAO_MAXIMA)

    return media * concentracao, (1.0 - media) * concentracao


# ---------------------------------------------------------------------------
# Verossimilhança do resultado observado -- a métrica primária de avaliação
# ---------------------------------------------------------------------------
def log_verossimilhanca_placar(matriz: np.ndarray, gols_casa: int, gols_fora: int) -> float:
    """log P(placar observado) sob a matriz do modelo.

    É a métrica primária da avaliação porque mede o modelo INTEIRO de uma
    vez: um modelo pode acertar bem o 1X2 e ainda distribuir mal os
    placares dentro de cada resultado. Log-loss por mercado, medido depois,
    só enxerga uma projeção dessa distribuição.
    """
    n = matriz.shape[0] - 1
    i, j = min(int(gols_casa), n), min(int(gols_fora), n)
    return float(np.log(max(matriz[i, j], 1e-15)))
