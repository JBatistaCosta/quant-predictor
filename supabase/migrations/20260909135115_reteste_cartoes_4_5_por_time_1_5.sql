-- Reteste de cartões 4.5 e por-time 1.5 com carteira cronológica + gestão de risco (09/09).
-- Resultado: 4.5 (anti-modelo) confirma perda real (IC95% inteiramente negativo) — a
-- primeira linha de cartões a virar de "positiva" pra "confirmadamente perdedora", não
-- apenas "inconclusiva". Mandante/visitante 1.5 seguem inconclusivas (amostra pequena).
-- Ver CONTEXTO_PROJETO.md para o detalhamento completo.

update public.model_betting_strategy
set estrategia = 'nenhuma',
    confianca = 'alta',
    casa_referencia = 'pinnacle+bet365',
    validado_carteira = true,
    evidencia_anti_perde = true,
    n_amostra = 90,
    roi_ic95_inf = -0.699,
    roi_ic95_sup = -0.265,
    notas = 'RETESTADO 09/09 com carteira cronológica: era "anti-modelo, média confiança, IC95%[+24%,+56%]" pelo método antigo (log-loss/ROI agregado) — reprovou de forma dramática no reteste. Edge min/máx por faixa + 1/4 Kelly (Pin+bet365): n=90, ROI -48,2%, IC95%[-69,9%,-26,5%] — INTEIRAMENTE NEGATIVO. Com teto de EV 4%-16%: n=31, ROI -40,1%, IC95%[-75,9%,-4,3%] — também inteiramente negativo. Causa provável: modelo prevê "under" em 65% dos jogos mas a taxa real de "over" é 41% — o filtro de EV, que confia na própria probabilidade do modelo, está sistematicamente selecionando os jogos onde o modelo está mais equivocado. Não seguir NUNCA a direção anti-modelo nesta linha.',
    atualizado_em = now()
where mercado = 'cartoes_total' and linha = 4.5;

update public.model_betting_strategy
set estrategia = 'nenhuma',
    confianca = 'em_revisao',
    casa_referencia = 'bet365+betano',
    validado_carteira = true,
    evidencia_anti_perde = false,
    n_amostra = 20,
    roi_ic95_inf = -0.770,
    roi_ic95_sup = 0.165,
    notas = 'RETESTADO 09/09 com carteira cronológica: era "anti-modelo, média confiança" pelo método antigo — amostra pequena no reteste (n=20 com edge min/faixa, n=13 com EV 4%-16%) não permite confirmar nem descartar. Ponto central negativo nos dois filtros (-30,2% e -47,5%), mas IC95% cruza zero. Rebaixado de "média" pra "em revisão" até acumular mais dados de 2026.',
    atualizado_em = now()
where mercado = 'cartoes_mandante' and linha = 1.5;

update public.model_betting_strategy
set estrategia = 'nenhuma',
    confianca = 'em_revisao',
    casa_referencia = 'bet365+betano',
    validado_carteira = true,
    evidencia_anti_perde = false,
    n_amostra = 14,
    roi_ic95_inf = -0.452,
    roi_ic95_sup = 0.763,
    notas = 'RETESTADO 09/09 com carteira cronológica: era "anti-modelo, média confiança" pelo método antigo — amostra pequena no reteste (n=14 com edge min/faixa, n=10 com EV 4%-16%), IC95% larguíssimo dos dois lados. Ponto central positivo (+15,5% e +14,3%), mas n insuficiente pra confirmar. Rebaixado de "média" pra "em revisão" até acumular mais dados de 2026.',
    atualizado_em = now()
where mercado = 'cartoes_visitante' and linha = 1.5;
