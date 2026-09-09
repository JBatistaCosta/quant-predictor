create table public.model_betting_strategy (
  id uuid primary key default gen_random_uuid(),
  mercado text not null,               -- ex.: 'cartoes_total', 'cartoes_mandante', 'cartoes_visitante', 'escanteios_total'
  linha numeric not null,
  sub_faixa text,                      -- ex.: 'under', 'over' (quando a estratégia depende do lado que o modelo prevê, como cartões 5.5)
  estrategia text not null check (estrategia in ('seguir_modelo', 'anti_modelo', 'nenhuma', 'nunca')),
  casa_referencia text,                -- casa(s) usada(s) no teste de referência, ex.: 'pinnacle', 'pinnacle+bet365'
  confianca text not null check (confianca in ('alta', 'media', 'baixa', 'em_revisao', 'insuficiente')),
  validado_carteira boolean not null default false,  -- passou por simulação de carteira cronológica (não só log-loss/ROI agregado)?
  evidencia_anti_perde boolean not null default false, -- IC95% confirma que apostar CONTRA o modelo perde dinheiro (mesmo sem edge positivo em nenhum lado)
  n_amostra integer,
  roi_ic95_inf numeric,
  roi_ic95_sup numeric,
  notas text,
  atualizado_em timestamptz not null default now()
);

comment on table public.model_betting_strategy is
  'Config versionada (lookup, não modelo de ML) da estratégia de aposta por mercado/linha — seguir o modelo, apostar contra ele, ou não apostar. Reavaliar conforme o cron acumula mais partidas de 2026. Ver CONTEXTO_PROJETO.md para o histórico completo dos testes por trás de cada linha.';

alter table public.model_betting_strategy enable row level security;

create policy "leitura publica model_betting_strategy"
  on public.model_betting_strategy for select
  to public
  using (true);

-- Cartões (bookings) — total da partida
insert into public.model_betting_strategy
  (mercado, linha, sub_faixa, estrategia, casa_referencia, confianca, validado_carteira, evidencia_anti_perde, n_amostra, roi_ic95_inf, roi_ic95_sup, notas)
values
  ('cartoes_total', 1.5, null, 'nenhuma', 'pinnacle', 'baixa', false, false, null, null, null,
   'Sem sinal em nenhuma direção (log-loss e ROI). Não retestado com carteira simulada nesta sessão.'),
  ('cartoes_total', 2.5, null, 'nenhuma', 'pinnacle', 'baixa', false, false, null, null, null,
   'IC95% cruza zero tanto no log-loss quanto na aposta contrária. Não retestado com carteira simulada nesta sessão.'),
  ('cartoes_total', 3.5, null, 'nenhuma', 'pinnacle+bet365', 'em_revisao', true, false, 268, -0.175, 0.055,
   'Classificado antes como "anti-modelo, alta confiança" (log-loss/ROI agregado, +21,71% IC95%[+8%,+35%], n=244) — NÃO reproduzido em carteira cronológica real: stake fixo deu -6,0% IC95%[-17,5%,+5,5%]; com gestão de risco (edge min/máx por faixa + 1/4 Kelly) deu +13,5% (n=24) a +29,5% (n=31, +bet365), sempre com IC95% larguíssimo. Rebaixado para "em revisão" até mais dados ou teste out-of-sample.'),
  ('cartoes_total', 4.5, null, 'anti_modelo', 'pinnacle', 'media', false, false, 135, 0.24, 0.56,
   'Classificação herdada do posicionamento consolidado por log-loss/ROI agregado (alta confiança, IC95%[+24%,+56%]) — PENDENTE de reteste com carteira cronológica + gestão de risco (só 3.5 e 5.5 foram retestadas nesta sessão). Tratar com cautela até confirmar.'),
  ('cartoes_total', 5.5, 'under', 'nenhuma', 'pinnacle+bet365', 'em_revisao', true, false, 63, -0.437, -0.026,
   'Regra herdada era "seguir o modelo quando ele diz under" — carteira cronológica real (sempre under, já que "seguir"+"anti" convergem nessa linha) deu -23,2% IC95%[-43,7%,-2,6%] (stake fixo, INTEIRAMENTE NEGATIVO) e -21,7% a -29,7% com gestão de risco (IC95% mais largo, cruza zero). Não seguir esta linha cegamente.'),
  ('cartoes_total', 5.5, 'over', 'nenhuma', 'pinnacle', 'em_revisao', false, false, 26,  null, null,
   'Regra herdada era "apostar contra quando o modelo diz over" — mesma carteira acima já cobre o efeito líquido da linha (ambos os ramos convergem pra "sempre under"); não há teste isolado do ramo "over" em carteira própria.'),
  ('cartoes_total', 6.5, null, 'nenhuma', null, 'insuficiente', false, false, null, null, null,
   'Amostra insuficiente (n<=1) mesmo no teste agregado original.')
on conflict do nothing;

-- Cartões por time (mandante/visitante)
insert into public.model_betting_strategy
  (mercado, linha, sub_faixa, estrategia, casa_referencia, confianca, validado_carteira, evidencia_anti_perde, n_amostra, roi_ic95_inf, roi_ic95_sup, notas)
values
  ('cartoes_mandante', 0.5, null, 'nunca', null, 'alta', false, false, null, null, null,
   'ROI aparente de +150% a +630% é artefato de odd longa (time zerar cartões é raro) — NUNCA apostar aqui independente do EV mostrado.'),
  ('cartoes_visitante', 0.5, null, 'nunca', null, 'alta', false, false, null, null, null,
   'Mesmo artefato de odd longa do mandante 0.5 (odd_under média 9,46x). NUNCA apostar.'),
  ('cartoes_mandante', 1.5, null, 'anti_modelo', 'bet365', 'media', false, false, 93, null, null,
   'Herdado do posicionamento por log-loss/ROI agregado (3 de 4 combinações casa/lado significativas) — PENDENTE de reteste com carteira cronológica.'),
  ('cartoes_visitante', 1.5, null, 'anti_modelo', 'bet365', 'media', false, false, 76, null, null,
   'Idem mandante 1.5 — pendente de reteste com carteira cronológica.'),
  ('cartoes_mandante', 2.5, null, 'nenhuma', null, 'insuficiente', false, false, null, null, null,
   'Amostra insuficiente nas casas com cobertura de linha por time (n=3-17).'),
  ('cartoes_visitante', 2.5, null, 'nenhuma', null, 'insuficiente', false, false, null, null, null,
   'Amostra insuficiente nas casas com cobertura de linha por time (n=3-17).')
on conflict do nothing;

-- Escanteios (corners) — total da partida, modelo dedicado "Escanteios — FBref + FotMob [xgboost]"
insert into public.model_betting_strategy
  (mercado, linha, sub_faixa, estrategia, casa_referencia, confianca, validado_carteira, evidencia_anti_perde, n_amostra, roi_ic95_inf, roi_ic95_sup, notas)
values
  ('escanteios_total', 7.5, null, 'nenhuma', 'pinnacle+bet365+betano+william_hill', 'em_revisao', true, false, 28, -0.368, 0.169,
   'Amostra pequena (n=28 segue / n=10 anti) — ambos os lados cruzam zero. Inconclusivo, não confirma nem descarta.'),
  ('escanteios_total', 8.5, null, 'nenhuma', 'pinnacle+bet365+betano+william_hill', 'em_revisao', true, false, 163, -0.161, 0.104,
   'n=163 segue / n=85 anti — ambos cruzam zero, ponto central negativo nos dois. Inconclusivo.'),
  ('escanteios_total', 9.5, null, 'nenhuma', 'pinnacle+bet365+betano+william_hill', 'alta', true, true, 88, -0.463, -0.006,
   'Log-loss: mercado supera o modelo com significância (IC95%[+0,79%,+2,74%]). Carteira anti-modelo fecha com IC95% INTEIRAMENTE NEGATIVO (n=88 de 964 partidas) — evidência real de que apostar contra o modelo também perde. "Segue o modelo" também não gera lucro (n=301, ROI -4,4%, IC95%[-15,5%,+6,7%]). Não apostar em nenhuma direção nesta linha.'),
  ('escanteios_total', 10.5, null, 'nenhuma', 'pinnacle+bet365+betano+william_hill', 'alta', true, true, 151, -0.375, -0.008,
   'Replica o padrão da 9.5: anti-modelo fecha com IC95% inteiramente negativo, amostra ainda maior (n=151 de 734). "Segue o modelo" fica praticamente neutro (n=90, ROI +0,7%, IC95% largo). Duas linhas confirmando o mesmo sinal = replicação real, não é sorte de amostra pequena.'),
  ('escanteios_total', 11.5, null, 'nenhuma', 'pinnacle+bet365+betano+william_hill', 'em_revisao', true, false, 31, -0.523, 0.677,
   'n=31 segue / n=16 anti — amostra pequena, IC95% larguíssimos dos dois lados. Inconclusivo.'),
  ('escanteios_total', 12.5, null, 'nenhuma', null, 'insuficiente', false, false, 0, null, null,
   'Pinnacle não cotava fechamento nessa linha no período coberto (08/09/2026) — sem dado suficiente pra testar.')
on conflict do nothing;
