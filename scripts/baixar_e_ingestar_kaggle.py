"""
Script Automático: Baixar e Ingestar Odds de Datasets do Kaggle
===============================================================

Este script utiliza a API oficial do Kaggle (autenticada via .env ou kaggle.json)
para baixar datasets de odds do Brasileirão, extrair os arquivos e ingestar
as odds 1X2 diretamente na tabela `odds_market` do Supabase.

Uso:
  python scripts/baixar_e_ingestar_kaggle.py [--dataset REFERENCIA_KAGGLE] [--temporada 2025] [--dry-run]

Exemplo:
  python scripts/baixar_e_ingestar_kaggle.py --dataset lucasyukioimafuko/brasileirao-serie-a-2006-2022 --temporada 2024
"""

import argparse
import glob
import os
import sys
import subprocess

def main():
    parser = argparse.ArgumentParser(description="Baixar e Ingestar Odds do Kaggle")
    parser.add_argument("--dataset", default="lucasyukioimafuko/brasileirao-serie-a-2006-2022", help="Referência do dataset no Kaggle")
    parser.add_argument("--temporada", default="2025", help="Temporada para filtrar/importar (ex: 2025)")
    parser.add_argument("--liga-id", type=int, default=1, help="ID da liga (1 = Brasileirão Série A)")
    parser.add_argument("--dry-run", action="store_true", help="Simular importação sem alterar o banco")
    args = parser.parse_args()

    print("=== INTEGRAÇÃO AUTOMÁTICA KAGGLE -> SUPABASE ===")
    print(f"Dataset Kaggle: {args.dataset}")
    print(f"Temporada: {args.temporada} | Liga ID: {args.liga_id}")

    # Carregar env se existir
    env_path = os.path.join(os.path.dirname(__file__), "..", "functions", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'\"")

    # Autenticar Kaggle
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        print("Autenticação no Kaggle: OK!")
    except Exception as e:
        sys.exit(f"ERRO de Autenticação no Kaggle: {e}")

    # Pasta de destino
    dest_dir = os.path.join(os.path.dirname(__file__), "..", "arquivos_do_claude", "kaggle_download")
    os.makedirs(dest_dir, exist_ok=True)

    print(f"Baixando dataset '{args.dataset}' para '{dest_dir}'...")
    try:
        api.dataset_download_files(args.dataset, path=dest_dir, unzip=True)
        print("Download concluído com sucesso!")
    except Exception as e:
        sys.exit(f"ERRO ao baixar dataset do Kaggle: {e}")

    # Procurar arquivos CSV baixados
    csv_files = glob.glob(os.path.join(dest_dir, "*.csv")) + glob.glob(os.path.join(dest_dir, "**", "*.csv"), recursive=True)
    if not csv_files:
        sys.exit(f"Nenhum arquivo .csv encontrado em '{dest_dir}'.")

    print(f"Arquivos CSV encontrados ({len(csv_files)}):")
    for csv_f in csv_files:
        print(f"  - {os.path.basename(csv_f)}")

    # Executar script de ingestão para cada CSV encontrado
    script_ingestao = os.path.join(os.path.dirname(__file__), "ingestar_odds_csv_customizado.py")
    
    for csv_f in csv_files:
        cmd = [
            sys.executable, script_ingestao,
            "--csv", csv_f,
            "--liga-id", str(args.liga_id),
            "--temporada", str(args.temporada)
        ]
        if args.dry_run:
            cmd.append("--dry-run")
        
        print(f"\n--- Ingestando {os.path.basename(csv_f)} ---")
        subprocess.run(cmd)

if __name__ == "__main__":
    main()
