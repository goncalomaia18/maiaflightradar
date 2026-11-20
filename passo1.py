import json
import urllib.request

URL = "https://ads-b.jcboliveira.xyz/dump1090/data/aircraft.json"

# 1. Ir buscar os dados ao servidor dump1090
req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=10) as resposta:
    dados = json.loads(resposta.read().decode("utf-8"))

avioes = dados.get("aircraft", [])
print(f"Total de registos recebidos: {len(avioes)}\n")

# 2. Mostrar apenas os aviões que têm coordenadas GPS (lat e lon)
for aviao in avioes:
    if "lat" in aviao and "lon" in aviao:
        hex_icao = aviao.get("hex")
        voo = (aviao.get("flight") or "Sem indicativo").strip()
        altitude = aviao.get("altitude", "N/A")
        sinal = aviao.get("rssi", "N/A")

        print(f"ICAO: {hex_icao} | Voo: {voo:10} | Alt: {altitude} ft | Sinal: {sinal} dBFS")