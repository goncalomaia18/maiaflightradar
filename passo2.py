import json
import sqlite3
import time
import urllib.request

URL = "https://ads-b.jcboliveira.xyz/dump1090/data/aircraft.json"
DB_FILE = "radar.db"

# 1. Criar a base de dados e a tabela (se ainda não existirem)
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS telemetria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_hora TEXT,
        hex TEXT,
        voo TEXT,
        lat REAL,
        lon REAL,
        altitude TEXT,
        velocidade REAL,
        rssi REAL
    )
""")
conn.commit()

print("A recolher dados para 'radar.db'... (Prime Ctrl+C para parar)\n")

# 2. Recolher dados continuamente a cada 5 segundos
try:
    while True:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))

        agora = time.strftime("%Y-%m-%d %H:%M:%S")
        avioes = dados.get("aircraft", [])
        guardados = 0

        for aviao in avioes:
            hex_icao = aviao.get("hex")
            voo = (aviao.get("flight") or "").strip() or None
            lat = aviao.get("lat")
            lon = aviao.get("lon")
            altitude = str(aviao.get("altitude", ""))
            velocidade = aviao.get("speed")
            rssi = aviao.get("rssi")

            cursor.execute("""
                INSERT INTO telemetria (data_hora, hex, voo, lat, lon, altitude, velocidade, rssi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (agora, hex_icao, voo, lat, lon, altitude, velocidade, rssi))
            guardados += 1

        conn.commit()
        print(f"[{agora}] Guardados {guardados} registos na base de dados.")
        time.sleep(5)

except KeyboardInterrupt:
    print("\nRecolha terminada pelo utilizador.")
finally:
    conn.close()