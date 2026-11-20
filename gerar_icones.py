import math
import struct
import zlib


def criar_icone_radar(nome_ficheiro, tamanho):
    centro = tamanho / 2.0
    raw = bytearray()

    for y in range(tamanho):
        raw.append(0)  # Filtro PNG
        for x in range(tamanho):
            dist = math.hypot(x - centro, y - centro) / centro
            # Fundo azul-escuro (#0f172a) com anéis ciano (#38bdf8)
            if dist < 0.08 or abs(dist - 0.35) < 0.025 or abs(dist - 0.65) < 0.025 or abs(dist - 0.90) < 0.03:
                raw.extend((56, 189, 248))
            else:
                raw.extend((15, 23, 42))

    def chunk(tipo, dados):
        return (
            struct.pack(">I", len(dados))
            + tipo
            + dados
            + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", tamanho, tamanho, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")

    with open(nome_ficheiro, "wb") as f:
        f.write(png)


criar_icone_radar("icon-192.png", 192)
criar_icone_radar("icon-512.png", 512)
print("Ícones icon-192.png e icon-512.png criados com sucesso!")