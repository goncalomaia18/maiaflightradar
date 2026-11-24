import json
import os
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
HEADERS = {"User-Agent": "STAER-Radar-App/1.0"}


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
    conn.commit()
    conn.close()


def pedir_json(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def enriquecer_novos_avioes(conn):
    """Completa matrícula, modelo e rotas de aviões novos em segundo plano."""
    cursor = conn.cursor()

    hex_pendentes = [
        r[0] for r in cursor.execute(
            "SELECT DISTINCT hex FROM estado_atual WHERE hex IS NOT NULL AND hex NOT IN (SELECT hex FROM aeronaves) LIMIT 2"
        ).fetchall()
    ]
    for h in hex_pendentes:
        matricula, modelo, companhia = "Desconhecida", "Desconhecido", "Desconhecida"
        dados_os = pedir_json(f"https://opensky-network.org/api/metadata/aircraft/icao/{h.lower()}")
        if dados_os and (dados_os.get("registration") or dados_os.get("model")):
            matricula = dados_os.get("registration") or "N/A"
            modelo = dados_os.get("model") or dados_os.get("typecode") or "N/A"
            companhia = dados_os.get("operator") or dados_os.get("owner") or "N/A"
        else:
            dados_hd = pedir_json(f"https://hexdb.io/api/v1/aircraft/{h.lower()}")
            if dados_hd and (dados_hd.get("Registration") or dados_hd.get("Type")):
                matricula = dados_hd.get("Registration") or "N/A"
                modelo = dados_hd.get("Type") or dados_hd.get("ICAOTypeCode") or "N/A"
                companhia = dados_hd.get("RegisteredOwners") or "N/A"

        cursor.execute(
            "INSERT OR REPLACE INTO aeronaves (hex, matricula, modelo, companhia) VALUES (?, ?, ?, ?)",
            (h, matricula, modelo, companhia),
        )

    voos_pendentes = [
        r[0] for r in cursor.execute(
            "SELECT DISTINCT voo FROM estado_atual WHERE voo IS NOT NULL AND voo NOT IN (SELECT voo FROM rotas) LIMIT 2"
        ).fetchall()
    ]
    for voo in voos_pendentes:
        origem, destino = "N/A", "N/A"
        dados_rota = pedir_json(f"https://hexdb.io/api/v1/route/icao/{voo.upper()}")
        if dados_rota and "route" in dados_rota and "-" in dados_rota["route"]:
            partes = dados_rota["route"].split("-")
            origem, destino = partes[0].strip(), partes[-1].strip()

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

            # Limpa o estado anterior para mostrar no mapa apenas o que está agora no ficheiro
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

                # 1. Guarda no histórico completo (base de dados)
                cursor.execute("""
                    INSERT INTO telemetria (data_hora, hex, voo, lat, lon, altitude, velocidade, rssi, rumo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agora, hex_code, voo, lat, lon, altitude, velocidade, rssi, rumo))

                # 2. Guarda no mapa em tempo real apenas se estiver presente agora com coordenadas
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