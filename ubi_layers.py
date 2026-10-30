"""Ubisoft SB 'layer' streams (SM0 tipo 0x06/0x0D -> STREAM.SS0): N capas entrelazadas en rondas.
Formato (verificado en Splinter Cell PC):
  0x00 u32 2 (versión), u32 nº de capas, u32 tamaño total del stream, u32 tamaño de cabecera de ronda (8+4*capas),
       u32 tamaño de ronda, u32 tamaño base de trozo   -> 0x18 bytes
  rondas: [u32 índice (desde 1)] [u32 posición_de_la_cabecera - 4] [u32 tamaño de trozo por capa] + datos de cada capa en orden."""
import struct

HDR = 0x18

def parse(data):
    """-> dict(layers=[bytes por capa], rounds=[(idx, [sizes])], head=(ver, nl, total, rh, rs, base))"""
    ver, nl, total, rh, rs, base = struct.unpack_from('<6I', data, 0)
    assert ver == 2 and rh == 8 + 4 * nl, (ver, nl, rh)
    pos, rounds, layers = HDR, [], [bytearray() for _ in range(nl)]
    while pos + rh <= total:
        idx, mark = struct.unpack_from('<2I', data, pos)
        sizes = struct.unpack_from(f'<{nl}I', data, pos + 8)
        assert idx == len(rounds) + 1 and mark == pos - 4, (idx, mark, pos)
        p = pos + rh
        for k, s in enumerate(sizes):
            layers[k] += data[p:p + s]; p += s
        rounds.append((idx, list(sizes)))
        pos = p
    return dict(layers=[bytes(x) for x in layers], rounds=rounds, head=(ver, nl, total, rh, rs, base), end=pos)

def build(layers, base=None, round_size=1104):
    """reconstruye el stream a partir de los datos de cada capa (misma regla de reparto que el original)"""
    nl = len(layers); rh = 8 + 4 * nl
    per_round = round_size - rh                              # bytes de datos por ronda (todas las capas)
    q, r = divmod(per_round, nl)                             # q por capa y r bytes extra que rotan entre capas
    out = bytearray(struct.pack('<6I', 2, nl, 0, rh, round_size, q)); done = [0] * nl
    n = 0
    while any(done[k] < len(layers[k]) for k in range(nl)):
        n += 1
        sizes = []
        for k in range(nl):
            want = q + (1 if ((k - (n - 1)) % nl) < r else 0)  # rotación del byte extra (capa n-1 mod nl en la ronda n)
            sizes.append(min(want, len(layers[k]) - done[k]))
        out += struct.pack('<2I', n, len(out) - 4) + struct.pack(f'<{nl}I', *sizes)
        for k in range(nl):
            out += layers[k][done[k]:done[k] + sizes[k]]; done[k] += sizes[k]
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)
