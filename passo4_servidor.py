import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

URL_RADAR = "https://ads-b.jcboliveira.xyz/dump1090/data/aircraft.json"
DB_FILE = "radar.db"
PORTA = int(os.environ.get("PORT", 8000))
HEADERS = {"User-Agent": "MaiaFlightRadar/1.0"}


def preparar_bd():
    """Cria as tabelas caso o servidor arranque num ambiente limpo (ex: Render)."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telemetria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            hex TEXT,
            voo TEXT,
            lat REAL,
            lon REAL,
            altitude TEXT,
            velocidade REAL,
            rssi REAL,
            rumo REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estado_atual (
            hex TEXT PRIMARY KEY,
            voo TEXT,
            lat REAL,
            lon REAL,
            altitude TEXT,
            velocidade REAL,
            rssi REAL,
            rumo REAL,
            data_hora TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aeronaves (
            hex TEXT PRIMARY KEY,
            matricula TEXT,
            modelo TEXT,
            companhia TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rotas (
            voo TEXT PRIMARY KEY,
            origem TEXT,
            destino TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE telemetria ADD COLUMN rumo REAL")
    except sqlite3.OperationalError:
        pass

    # Limpa entradas antigas marcadas como 'Desconhecida' para voltar a tentar com as 3 APIs
    conn.execute("DELETE FROM aeronaves WHERE matricula = 'Desconhecida'")
    conn.commit()
    conn.close()


def pedir_json(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def extrair_matricula_do_voo(voo):
    """Deteta se o callsign é na verdade uma matrícula privada (ex: TCAKB -> TC-AKB, CSDXA -> CS-DXA)."""
    if not voo:
        return None
    v = voo.strip().upper()
    # Padrão de matrícula americana (ex: N128AM)
    if re.match(r"^N[1-9][0-9A-Z]{1,4}$", v):
        return v
    # Padrão de matrícula com prefixo de 2 letras + 3 letras (ex: CS-..., EC-..., TC-..., PH-..., OO-...)
    if re.match(r"^(CS|CR|EC|TC|PH|OO|HB|OE|EI|SE|OY|LN|OH|SP|OK|HA|YR|LZ|SX|9H)[A-Z]{3}$", v):
        return f"{v[:2]}-{v[2:]}"
    # Padrão de 1 letra + 4 letras (ex: D-ABCD, F-ABCD, G-ABCD, I-ABCD)
    if re.match(r"^[DFGI][A-Z]{4}$", v):
        return f"{v[0]}-{v[1:]}"
    return None


def consultar_3_apis(hex_code):
    h = hex_code.lower()

    # 1.ª Fonte: OpenSky Network
    dados_os = pedir_json(f"https://opensky-network.org/api/metadata/aircraft/icao/{h}")
    if dados_os and (dados_os.get("registration") or dados_os.get("model")):
        return (
            dados_os.get("registration") or None,
            dados_os.get("model") or dados_os.get("typecode") or "Desconhecido",
            dados_os.get("operator") or dados_os.get("owner") or "Desconhecida",
        )

    # 2.ª Fonte: adsbdb.com (excelente cobertura para aviação comercial e executiva)
    dados_adsb = pedir_json(f"https://api.adsbdb.com/v0/aircraft/{h}")
    if dados_adsb and isinstance(dados_adsb.get("response"), dict):
        ac = dados_adsb["response"].get("aircraft")
        if isinstance(ac, dict) and (ac.get("registration") or ac.get("type")):
            return (
                ac.get("registration") or None,
                ac.get("type") or ac.get("icao_type") or "Desconhecido",
                ac.get("registered_owner") or "Desconhecida",
            )

    # 3.ª Fonte: hexdb.io
    dados_hd = pedir_json(f"https://hexdb.io/api/v1/aircraft/{h}")
    if dados_hd and (dados_hd.get("Registration") or dados_hd.get("Type")):
        return (
            dados_hd.get("Registration") or None,
            dados_hd.get("Type") or dados_hd.get("ICAOTypeCode") or "Desconhecido",
            dados_hd.get("RegisteredOwners") or "Desconhecida",
        )

    return None, "Desconhecido", "Desconhecida"


def enriquecer_novos_avioes(conn):
    """Completa matrícula, modelo e rotas dos aviões ativos em segundo plano."""
    cursor = conn.cursor()

    pendentes = cursor.execute("""
        SELECT DISTINCT e.hex, e.voo, e.altitude
        FROM estado_atual e
        WHERE e.hex IS NOT NULL AND e.hex NOT IN (SELECT hex FROM aeronaves)
        LIMIT 6
    """).fetchall()

    for h, voo, altitude in pendentes:
        matricula, modelo, companhia = consultar_3_apis(h)

        # Fallback 1: Tentar deduzir a matrícula a partir do callsign (voos privados)
        if not matricula:
            matricula_voo = extrair_matricula_do_voo(voo)
            if matricula_voo:
                matricula = matricula_voo
                if companhia == "Desconhecida":
                    companhia = "Aviação Privada / Executiva"

        # Fallback 2: Identificar veículos terrestres de aeroporto
        if not matricula and altitude == "ground" and not voo:
            matricula = "Viatura Solo"
            modelo = "Equipamento Aeroportuário"
            companhia = "Aeroporto"

        cursor.execute(
            "INSERT OR REPLACE INTO aeronaves (hex, matricula, modelo, companhia) VALUES (?, ?, ?, ?)",
            (h, matricula or "Desconhecida", modelo, companhia),
        )

    # Enriquecer rotas (Origem -> Destino)
    voos_pendentes = [
        r[0] for r in cursor.execute(
            "SELECT DISTINCT voo FROM estado_atual WHERE voo IS NOT NULL AND voo NOT IN (SELECT voo FROM rotas) LIMIT 6"
        ).fetchall()
    ]
    for voo in voos_pendentes:
        origem, destino = "N/A", "N/A"
        dados_rota = pedir_json(f"https://hexdb.io/api/v1/route/icao/{voo.upper()}")
        if dados_rota and "route" in dados_rota and "-" in dados_rota["route"]:
            partes = dados_rota["route"].split("-")
            origem, destino = partes[0].strip(), partes[-1].strip()
        else:
            # Fallback de rota pela adsbdb
            dados_adsb_rota = pedir_json(f"https://api.adsbdb.com/v0/callsign/{voo.upper()}")
            if dados_adsb_rota and isinstance(dados_adsb_rota.get("response"), dict):
                fr = dados_adsb_rota["response"].get("flightroute")
                if isinstance(fr, dict):
                    orig = fr.get("origin") or {}
                    dest = fr.get("destination") or {}
                    if orig.get("icao_code") and dest.get("icao_code"):
                        origem = orig["icao_code"]
                        destino = dest["icao_code"]

        cursor.execute(
            "INSERT OR REPLACE INTO rotas (voo, origem, destino) VALUES (?, ?, ?)",
            (voo, origem, destino),
        )
    conn.commit()


def recolha_background():
    """Recolhe dados do radar a cada 3 segundos, guarda histórico e atualiza o mapa ao vivo."""
    while True:
        try:
            req = urllib.request.Request(URL_RADAR, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                dados = json.loads(resp.read().decode("utf-8"))

            agora = time.strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM estado_atual")

            for aviao in dados.get("aircraft", []):
                hex_code = aviao.get("hex")
                voo = (aviao.get("flight") or "").strip() or None
                lat = aviao.get("lat")
                lon = aviao.get("lon")
                altitude = str(aviao.get("altitude", ""))
                velocidade = aviao.get("speed")
                rssi = aviao.get("rssi")
                rumo = aviao.get("track")

                cursor.execute("""
                    INSERT INTO telemetria (data_hora, hex, voo, lat, lon, altitude, velocidade, rssi, rumo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agora, hex_code, voo, lat, lon, altitude, velocidade, rssi, rumo))

                if hex_code and lat is not None and lon is not None:
                    cursor.execute("""
                        INSERT OR REPLACE INTO estado_atual (hex, voo, lat, lon, altitude, velocidade, rssi, rumo, data_hora)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (hex_code, voo, lat, lon, altitude, velocidade, rssi, rumo, agora))

            conn.commit()
            enriquecer_novos_avioes(conn)
            conn.close()
        except Exception as e:
            print(f"[Aviso recolha] {e}")
        time.sleep(3)


class RadarHandler(SimpleHTTPRequestHandler):
    def responder_json(self, dados):
        corpo = json.dumps(dados).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/avioes":
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT e.hex, e.voo, e.lat, e.lon, e.altitude, e.velocidade, e.rssi, e.rumo, e.data_hora,
                       a.matricula, a.modelo, a.companhia,
                       r.origem, r.destino
                FROM estado_atual e
                LEFT JOIN aeronaves a ON e.hex = a.hex
                LEFT JOIN rotas r ON e.voo = r.voo
            """).fetchall()
            conn.close()
            self.responder_json([dict(r) for r in rows])

        elif parsed.path == "/api/rasto":
            params = urllib.parse.parse_qs(parsed.query)
            hex_code = params.get("hex", [""])[0]
            conn = sqlite3.connect(DB_FILE)
            rows = conn.execute("""
                SELECT lat, lon, altitude
                FROM telemetria
                WHERE hex = ? AND lat IS NOT NULL AND lon IS NOT NULL
                ORDER BY id DESC
                LIMIT 150
            """, (hex_code,)).fetchall()
            conn.close()
            pontos = [[r[0], r[1]] for r in reversed(rows)]
            self.responder_json({"hex": hex_code, "pontos": pontos})

        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    preparar_bd()
    t = threading.Thread(target=recolha_background, daemon=True)
    t.start()

    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), RadarHandler)
    print(f"Servidor ativo na porta {PORTA}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")