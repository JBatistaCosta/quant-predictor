# Força dinâmica dos times — desenho da arquitetura

> **STATUS (14/07/2026): PAUSADO — não passou na validação ainda.**
> Guardado aqui como candidato a comparação futura contra o
> `modelo_dixon_coles_walkforward.py` (retreino em lote periódico), que
> foi implementado em paralelo e É validado. Quando algum dos dois tiver
> tempo/interesse dedicado, comparar log loss dos dois em holdout real e
> seguir com o que ganhar.
>
> **Diagnóstico já feito** (não repetir do zero):
> - Teste com força real 100% estática (sem deriva) e Q=0 deveria
>   convergir pro valor real. Não convergiu — ficou um erro residual de
>   ~0.12 mesmo depois de 60 rodadas (600 partidas).
> - Causa parcial identificada: **deriva de gauge** — o update online não
>   fixa a média do ataque em zero (o lote faz isso via
>   `atk - atk.mean()`; o filtro sequencial não tinha nada equivalente).
>   Corrigir isso (recentralizar após cada rodada) reduziu o erro de
>   0.124 para 0.097 — melhora real, mas não resolve tudo.
> - **Causa provável do restante**: a linearização de Newton de um passo
>   só (EKF "puro") é grosseira demais pra verossimilhança de Poisson tão
>   ruidosa (variância = lambda, tipicamente só ~1.5 — pouquíssima
>   informação por partida). Próximo passo de investigação, se retomado:
>   iterar a linearização algumas vezes por atualização (IEKF) antes de
>   aceitar o novo estado, em vez de um passo de Newton só.

## O problema com o Kalman "puro"

Um filtro de Kalman clássico assume que a relação entre estado e observação
é linear-gaussiana. No nosso caso o estado (ataque/defesa) é gaussiano, mas
a **observação** (gols) é Poisson, não gaussiana. Isso é resolvido do jeito
padrão pra GLMs online: um **Filtro de Kalman Estendido (EKF)** — a cada
partida, linearizamos a verossimilhança de Poisson ao redor da estimativa
atual (um passo de Newton/Fisher scoring) e tratamos esse resultado
linearizado como uma "pseudo-observação" gaussiana, que aí sim entra num
update de Kalman de verdade.

## Estado

Cada time carrega, por competição, uma distribuição (não mais um número
fixo):

    ataque_i  ~ N(m_ai, v_ai)
    defesa_i  ~ N(m_di, v_di)

(tratados independentes entre si — sem covariância cruzada, pra manter o
update barato: O(1) por time por partida, em vez de manipular uma matriz
de covariância conjunta que cresceria com o número de times da liga).

## Passo 1 — Inicialização (priori)

Vem do ajuste 2023-2024 que já fizemos (`team_strengths`). O ponto médio
já temos; a variância da priori vem da matriz de covariância assintótica
do MLE — aproximada pelo inverso da Hessiana no ótimo, que o próprio
`scipy.optimize.minimize` já calcula (`res.hess_inv`) e que hoje
descartamos sem usar.

## Passo 2 — Predição (entre partidas)

Antes de cada partida nova de um time, a incerteza cresce um pouco —
times podem mudar de forma no intervalo (lesões, transferências, pausas):

    v_ai(t) = v_ai(t-1) + Q * dias_desde_o_ultimo_jogo_do_time

`Q` é um hiperparâmetro (taxa de "deriva" por dia) — assim como o `xi` do
decaimento temporal, não dá pra estimar junto com o resto sem risco de
degenerar; calibra por validação cruzada temporal depois que o protótipo
estiver funcionando.

## Passo 3 — Atualização (depois de cada partida)

Aqui está a parte não-trivial. Para o mandante i contra o visitante j,
lambda = exp(a_i + d_j + mando). A observação é `hg` (gols do mandante). O
**score** (gradiente do log da verossimilhança de Poisson em relação ao
preditor linear eta = a_i + d_j + mando) e a **informação de Fisher**
(achatamento da curva de verossimilhança) valem:

    score = hg - lambda
    fisher = lambda        (= Var(hg) sob Poisson)

Isso vira uma pseudo-observação gaussiana do preditor linear:

    z = (a_i + d_j + mando) + score / fisher
    R = 1 / fisher          (variância da pseudo-observação)

Como eta é a **soma** de dois estados independentes (a_i e d_j), isso é
exatamente o problema clássico de "atualizar duas variáveis observadas só
através da soma delas" — resolvido por Kalman padrão:

    inovação = z - (m_ai + m_dj + mando)
    ganho_ai = v_ai / (v_ai + v_dj + R)
    ganho_dj = v_dj / (v_ai + v_dj + R)

    m_ai_novo = m_ai + ganho_ai * inovação
    m_dj_novo = m_dj + ganho_dj * inovação
    v_ai_novo = v_ai * (1 - ganho_ai)
    v_dj_novo = v_dj * (1 - ganho_dj)

O mesmo update se repete para mu = exp(a_j + d_i) usando `ag` (gols do
visitante), atualizando `a_j` e `d_i`. Ou seja: **cada partida atualiza os
4 números** (ataque e defesa dos dois times envolvidos), nunca os times
que não jogaram naquela rodada — que só passam pelo passo de predição
(incerteza cresce, média não muda).

## O que fica de fora dessa primeira versão (compromissos conscientes)

- **Correção de Dixon-Coles (rho) não entra no update online** — o rho
  corrige a dependência entre os dois placares de uma partida, e incluir
  isso no EKF exigiria linearizar uma correção que já não é linear nos
  parâmetros. Fica pro placar final (mercados 1X2/O-U), calculado com o
  rho global já ajustado, não durante a atualização de força.
- **Sem covariância cruzada entre ataque e defesa do mesmo time** —
  simplificação deliberada. Times com ataque e defesa genuinamente
  correlacionados (ex: time que "joga junto" nos dois lados) perderiam um
  pouco de precisão, mas o ganho de simplicidade computacional compensa
  nessa fase.
- **mando e Q continuam fixos** (vindos do ajuste histórico / escolhidos
  a priori) — só ataque/defesa por time são dinâmicos por enquanto.

## Validação planejada antes de ir pra produção

1. Gerar dados sintéticos onde a força **real** de cada time muda ao
   longo do tempo (um "passeio aleatório" conhecido).
2. Rodar o filtro e conferir se ele consegue *rastrear* essa mudança —
   comparando com um modelo estático (que não deveria conseguir).
3. Só depois disso, conectar no banco de verdade.
