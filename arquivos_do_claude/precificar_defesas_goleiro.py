#!/usr/bin/env python3
"""CLI de inspeção manual: converte um lambda de defesas de goleiro em
probabilidades de mercado (over/under 1.5/2.5/3.5/4.5), escolhendo entre
Poisson e Binomial Negativa (NB2) -- ver `analisar_nb2_defesas.py` pra como
o alpha/r usado aqui é calibrado (MLE + LRT) e validado (OOS) antes de virar
default numa liga.

Não é um job de produção -- não grava nada, só imprime a tabela de
probabilidade/odd justa pra um lambda específico. Serve pra (a) inspecionar
o efeito da escolha de distribuição num caso concreto, e (b) buscar o
disp_r já calibrado de uma liga real em `league_model_params` antes de
decidir se vale ligar NB2 pra ela.

Uso:
    python precificar_defesas_goleiro.py --lambda 3.2 --distribuicao-goleiro poisson
    python precificar_defesas_goleiro.py --lambda 3.2 --distribuicao-goleiro neg_binomial --disp-r 8.4
    set SUPABASE_URL=... & set SUPABASE_KEY=...
    python precificar_defesas_goleiro.py --lambda 3.2 --distribuicao-goleiro neg_binomial --league-id 4
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import distribuicoes as dist  # noqa: E402

MODEL_NAME = "jogador_defesas_catboost_poisson_v1"


def buscar_disp_r_liga(league_id: int) -> float | None:
    """Busca `disp_r` calibrado (`league_model_params`, ver
    `analisar_nb2_defesas.persistir`) pra uma liga -- só chamado se
    `--league-id` for passado, pra não exigir credencial de banco no uso
    mais simples (`--disp-r` manual ou Poisson puro)."""
    from supabase import create_client

    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        sys.exit("Configure SUPABASE_URL/SUPABASE_KEY pra buscar disp_r por liga (ou passe --disp-r manualmente).")
    supabase = create_client(url, key)
    linhas = (
        supabase.table("league_model_params")
        .select("param_value")
        .eq("league_id", league_id)
        .eq("model_name", MODEL_NAME)
        .eq("stat", "defesas")
        .eq("param_name", "disp_r")
        .limit(1)
        .execute()
        .data
    )
    return float(linhas[0]["param_value"]) if linhas else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lambda", dest="lam", type=float, required=True, help="lambda_defesas_jogo previsto pro goleiro")
    parser.add_argument("--distribuicao-goleiro", choices=["poisson", "neg_binomial"], default="poisson")
    parser.add_argument("--league-id", type=int, default=None, help="busca disp_r calibrado dessa liga (precisa de SUPABASE_URL/KEY)")
    parser.add_argument("--disp-r", type=float, default=None, help="disp_r manual -- sobrepõe --league-id, não precisa de banco")
    args = parser.parse_args()

    disp_r = float("inf")
    if args.distribuicao_goleiro == "neg_binomial":
        if args.disp_r is not None:
            disp_r = args.disp_r
        elif args.league_id is not None:
            encontrado = buscar_disp_r_liga(args.league_id)
            if encontrado is None:
                print(f"Sem disp_r calibrado pra liga {args.league_id} -- caindo pra Poisson (fallback gracioso).", file=sys.stderr)
            else:
                disp_r = encontrado
        else:
            sys.exit("--distribuicao-goleiro neg_binomial precisa de --disp-r ou --league-id.")

    usando_nb2 = disp_r != float("inf") and disp_r > 0
    alpha_efetivo = (1.0 / disp_r) if usando_nb2 else 0.0
    mercado = dist.mercados_de_defesas(args.lam, disp_r=disp_r)

    print(f"lambda={args.lam:.4f}  distribuicao={'NB2' if usando_nb2 else 'Poisson'}  "
          f"disp_r={disp_r if disp_r != float('inf') else 'inf'}  alpha={alpha_efetivo:.5f}")
    print(f"{'linha':>8}  {'P(over)':>10}  {'P(under)':>10}  {'odd justa over':>16}")
    for linha in (1.5, 2.5, 3.5, 4.5):
        p_over = mercado[(f"defesas_over_under_{dist.rotulo_linha(linha)}", "over")]
        p_under = mercado[(f"defesas_over_under_{dist.rotulo_linha(linha)}", "under")]
        odd = (1.0 / p_over) if p_over > 0 else float("inf")
        print(f"{linha:>8.1f}  {p_over:>10.4f}  {p_under:>10.4f}  {odd:>16.3f}")


if __name__ == "__main__":
    main()
