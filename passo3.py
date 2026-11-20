import json
import sqlite3
import time
import urllib.error
import urllib.request

DB_FILE = "radar.db"
HEADERS = {"User-Agent": "STAER-Student-Project/1.0"}


def pedir_json(url):
    """Faz um pedido HTTP GET e devolve o JSON, ou None se falhar."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def obter_dados_aviao(hex_icao):
    h = hex_icao.lower()

    # 1.ª Tentativa: OpenSky Network
    dados_os = pedir_json(f"https://opensky-network.org/api/metadata/aircraft/icao/{h}")
    if dados_os and (dados_os.get("registration") or dados_os.get("model")):
        return (
            dados_os.get("registration") or "N/A",
            dados_os.get("model") or dados_os.get("typecode") or "N/A",
            dados_os.get("operator") or dados_os.get("owner") or "N/A",
        )

    # 2.ª Tentativa (Fallback): hexdb.io
    dados_hd = pedir_json(f"https://hexdb.io/api/v1/aircraft/{h}")
    if dados_hd and (dados_hd.get("Registration") or dados_hd.get("Type")):
        return (
            dados_hd.get("Registration") or "N/A",
            dados_hd.get("Type") or dados_hd.get("ICAOTypeCode") or "N/A",
            dados_hd.get("RegisteredOwners") or "N/A",
        )

    return "Desconhecida", "Desconhecido", "Desconhecida"


conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Limpar entradas anteriores que ficaram como 'Desconhecido' para voltar a tentar
cursor.execute("DELETE FROM aeronaves WHERE modelo = 'Desconhecido'")
conn.commit()

# Consultar apenas aeronaves que ainda não estão na tabela 'aeronaves'
hex_novos = [
    row[0] for row in cursor.execute(
        "SELECT DISTINCT hex FROM telemetria WHERE hex IS NOT NULL AND hex NOT IN (SELECT hex FROM aeronaves)"
    ).fetchall()
]

print(f"A consultar metadados (OpenSky + fallback) para {len(hex_novos)} aeronaves...")
for h in hex_novos:
    matricula, modelo, companhia = obter_dados_aviao(h)
    cursor.execute(
        "INSERT OR REPLACE INTO aeronaves (hex, matricula, modelo, companhia) VALUES (?, ?, ?, ?)",
        (h, matricula, modelo, companhia),
    )
    print(f"  -> ICAO {h}: {matricula} | {modelo} | {companhia}")
    time.sleep(0.3)

# Consultar rotas novas
voos_novos = [
    row[0] for row in cursor.execute(
        "SELECT DISTINCT voo FROM telemetria WHERE voo IS NOT NULL AND voo NOT IN (SELECT voo FROM rotas)"
    ).fetchall()
]

print(f"\nA consultar rotas para {len(voos_novos)} voos...")
for voo in voos_novos:
    dados_rota = pedir_json(f"https://hexdb.io/api/v1/route/icao/{voo.upper()}")
    if dados_rota and "route" in dados_rota and "-" in dados_rota["route"]:
        partes = dados_rota["route"].split("-")
        origem, destino = partes[0].strip(), partes[-1].strip()
    else:
        origem, destino = "N/A", "N/A"

    cursor.execute(
        "INSERT OR REPLACE INTO rotas (voo, origem, destino) VALUES (?, ?, ?)",
        (voo, origem, destino),
    )
    print(f"  -> Voo {voo}: Origem {origem} -> Destino {destino}")

conn.commit()
conn.close()
print("\nBase de dados atualizada com sucesso!")