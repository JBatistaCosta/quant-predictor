"""
Descoberta de campeonatos disponíveis em cada fonte de dados já usada (ou
cotada) no projeto — pensado pra rodar de tempos em tempos e gerar um
inventário que outra sessão/prompt (Claude ou humano) usa pra decidir o que
vale importar em seguida, sem precisar redescobrir tudo na mão.

Cobre:
  - football-data.org  (precisa de FOOTBALL_DATA_TOKEN)
  - API-Football        (precisa de API_FOOTBALL_KEY — hoje inválida em
                          produção, ver CONTEXTO_PROJETO.md; o script roda
                          mesmo assim e só marca essa seção como indisponível)
  - fbref / understat / football-data.co.uk / ClubElo, via soccerdata
    (sem chave — scraping público; available_leagues() é local/estático
    na maioria dos casos, não bate a fonte de verdade a cada chamada)

NÃO grava nada no Supabase — só LÊ o que já está cadastrado (`leagues` +
`liga_fonte_externa`) pra comparar e destacar só o que é NOVO em cada fonte.
Escrever de fato em `leagues`/`ligas`/`liga_fonte_externa` continua sendo uma
etapa manual/supervisionada depois de olhar o relatório (mesmo motivo já
documentado no projeto pra não confiar em heurística automática de
mapeamento: o custo de um mapeamento errado é alto).

Uso:
    1. pip install requests supabase soccerdata
    2. Defina as variáveis de ambiente:
         FOOTBALL_DATA_TOKEN -> football-data.org/client/home
         API_FOOTBALL_KEY    -> api-football.com (opcional — sem ela, só
                                 pula essa seção)
         SUPABASE_URL        -> https://cgurxgfdmpmsnrshqycx.supabase.co
         SUPABASE_KEY        -> a chave ANON (publishable) é suficiente —
                                 esse script só faz SELECT, não precisa de
                                 service_role. NÃO hardcode nenhuma chave
                                 aqui dentro (é exatamente o problema já
                                 registrado como pendência nos outros
                                 scripts desta pasta).
    3. python descobrir_campeonatos_disponiveis.py

Saída: relatório no terminal + arquivo
`campeonatos_disponiveis.json` (nesta pasta) com os dados brutos, pra outra
sessão ler sem precisar rodar tudo de novo.
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests
from supabase import create_client

# ---------------------------------------------------------------
# Configuração — só via variável de ambiente, sem fallback hardcoded.
# ---------------------------------------------------------------
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (anon já serve, é só leitura) nas variáveis de ambiente.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def ja_cadastrado():
    """O que já está em leagues/liga_fonte_externa — pra não repetir no relatório."""
    leagues = supabase.table("leagues").select("external_id, name").execute().data
    fontes = supabase.table("liga_fonte_externa").select("sistema, identificador").execute().data
    codigos_football_data = {r["external_id"] for r in leagues if r["external_id"]}
    por_sistema = {}
    for r in fontes:
        por_sistema.setdefault(r["sistema"], set()).add(r["identificador"])
    return codigos_football_data, por_sistema


def descobrir_football_data_org():
    if not FOOTBALL_DATA_TOKEN:
        return {"disponivel": False, "motivo": "FOOTBALL_DATA_TOKEN não definida."}
    resp = requests.get(
        "https://api.football-data.org/v4/competitions",
        headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
        timeout=30,
    )
    if not resp.ok:
        return {"disponivel": False, "motivo": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    dados = resp.json()
    competicoes = [
        {
            "codigo": c["code"],
            "nome": c["name"],
            "area": c.get("area", {}).get("name"),
            "tipo": c.get("type"),
            "temporada_atual": (c.get("currentSeason") or {}).get("startDate", "")[:4] or None,
        }
        for c in dados.get("competitions", [])
    ]
    return {"disponivel": True, "competicoes": competicoes}


def descobrir_api_football():
    if not API_FOOTBALL_KEY:
        return {"disponivel": False, "motivo": "API_FOOTBALL_KEY não definida."}
    resp = requests.get(
        "https://v3.football.api-sports.io/leagues",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        timeout=30,
    )
    dados = resp.json()
    if not resp.ok or dados.get("errors"):
        return {"disponivel": False, "motivo": f"HTTP {resp.status_code}: {json.dumps(dados.get('errors') or dados)[:300]}"}
    competicoes = [
        {
            "id": r["league"]["id"],
            "nome": r["league"]["name"],
            "tipo": r["league"]["type"],
            "pais": r.get("country", {}).get("name"),
            "temporada_atual": next((s["year"] for s in r.get("seasons", []) if s.get("current")), None),
        }
        for r in dados.get("response", [])
    ]
    return {"disponivel": True, "competicoes": competicoes}


def descobrir_soccerdata():
    """FBref, Understat, football-data.co.uk (MatchHistory) e ClubElo —
    todas via soccerdata, sem chave. available_leagues() é estático na
    maioria dos casos (lista embutida na lib, não uma chamada de rede por
    liga), então rodar isso não tem custo de cota."""
    try:
        import soccerdata as sd
    except ImportError:
        return {"disponivel": False, "motivo": "pip install soccerdata"}

    resultado = {}
    fontes = {
        "fbref": sd.FBref,
        "understat": sd.Understat,
        "football_data_co_uk": sd.MatchHistory,
        "clubelo": sd.ClubElo,
    }
    for nome, classe in fontes.items():
        try:
            resultado[nome] = sorted(classe.available_leagues())
        except Exception as e:  # biblioteca externa, formato de erro não é nosso
            resultado[nome] = {"erro": str(e)}
    return {"disponivel": True, "fontes": resultado}


def main():
    print("Consultando leagues/liga_fonte_externa já cadastrados no Supabase...")
    ja_football_data, ja_por_sistema = ja_cadastrado()

    relatorio = {"gerado_em": datetime.now(timezone.utc).isoformat(), "fontes": {}}

    print("\n=== football-data.org ===")
    r = descobrir_football_data_org()
    relatorio["fontes"]["football_data_org"] = r
    if r["disponivel"]:
        novos = [c for c in r["competicoes"] if c["codigo"] not in ja_football_data]
        print(f"{len(r['competicoes'])} competições no plano, {len(novos)} ainda não cadastradas em leagues:")
        for c in novos:
            print(f"  {c['codigo']:6s} {c['nome']:35s} ({c['area']}, {c['tipo']}, temporada atual {c['temporada_atual']})")
    else:
        print(f"  pulado: {r['motivo']}")

    print("\n=== API-Football ===")
    r = descobrir_api_football()
    relatorio["fontes"]["api_football"] = r
    if r["disponivel"]:
        ja = ja_por_sistema.get("api_football", set())
        novos = [c for c in r["competicoes"] if str(c["id"]) not in ja]
        print(f"{len(r['competicoes'])} competições no plano, {len(novos)} ainda não em liga_fonte_externa:")
        for c in novos[:30]:
            print(f"  {c['id']:6d} {c['nome']:35s} ({c['pais']}, {c['tipo']}, temporada atual {c['temporada_atual']})")
        if len(novos) > 30:
            print(f"  ... e mais {len(novos) - 30} (ver campeonatos_disponiveis.json)")
    else:
        print(f"  pulado: {r['motivo']}")

    print("\n=== fbref / understat / football-data.co.uk / ClubElo (soccerdata) ===")
    r = descobrir_soccerdata()
    relatorio["fontes"]["soccerdata"] = r
    if r["disponivel"]:
        for nome, ligas in r["fontes"].items():
            if isinstance(ligas, dict) and "erro" in ligas:
                print(f"  {nome}: erro — {ligas['erro']}")
                continue
            ja = ja_por_sistema.get(nome, set())
            novos = [l for l in ligas if l not in ja]
            print(f"  {nome}: {len(ligas)} ligas suportadas pela lib, {len(novos)} ainda não em liga_fonte_externa")
            for l in novos[:15]:
                print(f"    {l}")
            if len(novos) > 15:
                print(f"    ... e mais {len(novos) - 15}")
    else:
        print(f"  pulado: {r['motivo']}")

    caminho_saida = os.path.join(os.path.dirname(__file__), "campeonatos_disponiveis.json")
    with open(caminho_saida, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)
    print(f"\nRelatório completo salvo em {caminho_saida}")


if __name__ == "__main__":
    main()
