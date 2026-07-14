"""
Dixon-Coles com validação/produção WALK-FORWARD — retreina o modelo
periodicamente ao longo de 2025, usando em cada retreino só os jogos já
disputados até aquele momento (2023 + 2024 + 2025 até a data), e prevê só
a próxima janela de jogos. Nunca usa informação do futuro.

Reaproveita as funções já validadas do modelo_dixon_coles.py (mesmo
arquivo precisa estar na mesma pasta) — não duplica a matemática.

Grava em model_predictions com model_name diferente do modelo estático
('dixon_coles_walkforward_v1'), pra dar pra comparar os dois depois sem
um sobrescrever o outro.

Uso:
    set SUPABASE_KEY=sua_service_role_key
    python modelo_dixon_coles_walkforward.py
"""

import os
import sys

import numpy as np
import pandas as pd
from supabase import create_client

from modelo_dixon_coles import (
    ajustar_dixon_coles, matriz_placares, mercados,
    carregar_partidas, XI, LIGAS, TEMPORADAS_TREINO_POR_LIGA, TEMPORADA_TESTE,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us")

MODEL_NAME = "dixon_coles_walkforward_v1"
JANELA_DIAS = 7  # retreina a cada ~1 semana de jogos novos


def rodar_liga_walkforward(supabase, liga_ext_id):
    liga_id, df = carregar_partidas(supabase, liga_ext_id)
    if df.empty:
        print(f"\n[{liga_ext_id}] sem dados — pulando.")
        return None

    base_treino = df[df["season"].isin(TEMPORADAS_TREINO_POR_LIGA[liga_ext_id])].copy()
    teste = df[df["season"] == TEMPORADA_TESTE].copy().sort_values("match_date")
    if teste.empty:
        print(f"\n[{liga_ext_id}] sem jogos de teste — pulando.")
        return None

    # Agrupa os jogos de teste em janelas cronológicas (~1 rodada por vez)
    teste["janela"] = (
        (teste["match_date"] - teste["match_date"].min()).dt.days // JANELA_DIAS
    )

    print(f"\n[{liga_ext_id}] {teste['janela'].nunique()} janelas de retreino "
          f"({len(teste)} jogos de teste no total)")

    previsoes, log_loss_soma, n_previstos, pulados_total = [], 0.0, 0, 0
    historico_2025 = base_treino.iloc[0:0].copy()  # fatia vazia preserva dtypes (datetime etc)

    for janela_num, jogos_janela in teste.groupby("janela"):
        # Treino = histórico fixo (2023-24) + tudo de 2025 já jogado até aqui
        treino_atual = pd.concat([base_treino, historico_2025], ignore_index=True)

        ref = treino_atual["match_date"].max()
        dias = (ref - treino_atual["match_date"]).dt.days
        treino_atual = treino_atual.copy()
        treino_atual["weight"] = np.exp(-XI * dias)

        modelo = ajustar_dixon_coles(treino_atual)
        conhecidos = set(modelo["times"])

        previsiveis = jogos_janela[
            jogos_janela["home"].isin(conhecidos) & jogos_janela["away"].isin(conhecidos)
        ]
        pulados_total += len(jogos_janela) - len(previsiveis)

        for _, jogo in previsiveis.iterrows():
            m = matriz_placares(modelo, jogo["home"], jogo["away"])
            probs = mercados(m)

            resultado = ("draw" if jogo["hg"] == jogo["ag"]
                        else ("home" if jogo["hg"] > jogo["ag"] else "away"))
            log_loss_soma += -np.log(max(probs[("1X2", resultado)], 1e-10))
            n_previstos += 1

            for (mercado, selecao), p in probs.items():
                previsoes.append({
                    "match_id": int(jogo["id"]), "model_name": MODEL_NAME,
                    "market": mercado, "selection": selecao,
                    "probability": round(float(p), 5),
                })

        # Esses jogos da janela (já resolvidos) entram no histórico pra
        # próxima janela — é isso que torna o processo "walk-forward"
        historico_2025 = pd.concat([historico_2025, jogos_janela.drop(columns="janela")],
                                    ignore_index=True)

    log_loss = log_loss_soma / n_previstos if n_previstos else float("nan")
    print(f"  {n_previstos} jogos previstos, {pulados_total} pulados | "
          f"log loss walk-forward: {log_loss:.4f}")

    for i in range(0, len(previsoes), 500):
        supabase.table("model_predictions").upsert(
            previsoes[i : i + 500],
            on_conflict="match_id,model_name,market,selection",
        ).execute()

    return {"liga": liga_ext_id, "log_loss": log_loss, "jogos": n_previstos}


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    resultados = [r for liga in LIGAS if (r := rodar_liga_walkforward(supabase, liga))]

    print("\n===== RESUMO WALK-FORWARD =====")
    for r in resultados:
        print(f"  {r['liga']:>4}: log loss {r['log_loss']:.4f} ({r['jogos']} jogos)")
    print("\nCompare com o log loss do modelo estático (dixon_coles_v1) pra "
          "ver se o retreino periódico ajudou.")


if __name__ == "__main__":
    main()
