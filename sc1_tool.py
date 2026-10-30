#!/usr/bin/env python3
"""Splinter Cell (2002, PC) - herramienta de audio: mapas LM0/SM0 + streams LS0/SS0
  Extraer : decodifica a WAV los streams externos (Ubi ADPCM 4/6-bit mono/estéreo, Ubi IMA) y los sonidos internos del mapa
            (PCM16 mono/estéreo, Ubi IMA, Ubi ADPCM). Con --layers también la música en capas de STREAM.SS0 (3 capas estéreo por tema)
  Importar: recodifica los WAV, reconstruye los streams y el mapa (sonidos de cualquier tamaño, duración, tasa y canales)
Uso CLI:
  python sc1_tool.py extract <MAPS.LM0|MAPS.SM0> <carpeta con .LS0/.SS0> <salida WAV> [--layers]
  python sc1_tool.py import  <MAPS.LM0|MAPS.SM0> <carpeta con .LS0/.SS0> <carpeta WAV> <salida> [--all] [--original-rate]
  Por defecto cada sonido toma la tasa (hasta 48000 Hz) y la duración del WAV; --original-rate remuestrea a la tasa original.
  Los WAV pueden ser mono o estéreo: se convierten a los canales del sonido original. Los bucles se conservan (chunk 'smpl').
Sin argumentos abre la GUI. Requiere ubi_adpcm.py, ubi_ima.py y ubi_layers.py junto a este archivo."""
import os, sys, csv, json, zlib, struct, shutil, subprocess, threading
import ubi_adpcm as ua
import ubi_ima as im
import ubi_layers as ul

RATE = 36000
u32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
u16 = lambda d, o: struct.unpack_from('<H', d, o)[0]
cstr = lambda d, o, n: d[o:o + n].split(b'\0')[0].decode('latin1')
# offsets dentro de la entrada de sección 2 (0x80 bytes, SC1 PC)
E_SIZE, E_SOFF, E_STREAMED, E_NS, E_SIZE2, E_RATE, E_CH, E_STYPE, E_NAME = 0x0c, 0x14, 0x28, 0x30, 0x34, 0x44, 0x4a, 0x4c, 0x50
E_LOOP, E_NS2 = 0x2c, 0x38                         # bucle: +0x30 = inicio, +0x38 = longitud desde el inicio (total = ns + ns2)
L_NAME, L_COUNT, L_SOFF, L_SIZE, L_EXTRA, SUB = 0x34, 0x24, 0x5c, 0x64, 0x10, 0x18   # entrada de capas (tipo 0x0D)


def internal_offset(d, s3o, s3n, idx, stype):
    """offset absoluto en el LM0 de un sonido interno (misma lógica que ubi_sb.c parse_offsets, rama is_map)"""
    sub = 0 if stype == 1 else 1
    for i in range(s3n):
        o = s3o + 0x14 * i
        t1, n1, t2, n2 = u32(d, o + 4) + s3o, u32(d, o + 8), u32(d, o + 12) + s3o, u32(d, o + 16)
        for j in range(n1):
            if (u32(d, t1 + 8 * j) & 0x3FFFFFFF) == idx:
                so = u32(d, t1 + 8 * j + 4)
                for k in range(n2):
                    if u32(d, t2 + 0x10 * k) == sub: return so + u32(d, t2 + 0x10 * k + 0x0c)
    return None


def parse_lm0(path):
    d = bytearray(open(path, 'rb').read())
    ver, mstart, mnum = u32(d, 0), u32(d, 4), u32(d, 8)
    if ver != 7: raise ValueError(f'{os.path.basename(path)}: versión de mapa {ver} no soportada (se espera 7, Splinter Cell PC; LM0/SM0)')
    ents = []
    for i in range(mnum):
        o = mstart + i * 0x30
        mo, mname = u32(d, o + 8), cstr(d, o + 0x10, 0x20)
        s2o, s2n = u32(d, mo + 12) + mo, u32(d, mo + 16)
        s3o, s3n = u32(d, mo + 0x14) + mo, u32(d, mo + 0x18)
        for j in range(s2n):
            e = s2o + j * 0x80
            if u32(d, e + 4) != 1: continue
            streamed = bool(u32(d, e + E_STREAMED) & 1)
            r = dict(map=mname, idx=j, off=e, id=u32(d, e), size=u32(d, e + E_SIZE), soff=u32(d, e + E_SOFF),
                     ns=u32(d, e + E_NS), size2=u32(d, e + E_SIZE2), ch=u16(d, e + E_CH), stype=u32(d, e + E_STYPE),
                     rate=u32(d, e + E_RATE), rname=cstr(d, e + E_NAME, 0x28), streamed=streamed, ioff=None,
                     loop=bool(u32(d, e + E_LOOP) & 1), ns2=u32(d, e + E_NS2))
            r['total'] = r['ns'] + r['ns2'] if r['loop'] else r['ns']
            if not streamed: r['ioff'] = internal_offset(d, s3o, s3n, j, r['stype'])
            ents.append(r)
    return d, ents


def parse_layers(d):
    """entradas de capas (tipo 0x0D/0x06): música multicapa en un stream externo (STREAM.SS0)"""
    out = []
    mstart, mnum = u32(d, 4), u32(d, 8)
    for i in range(mnum):
        o = mstart + i * 0x30
        mo, mname = u32(d, o + 8), cstr(d, o + 0x10, 0x20)
        s2o, s2n, sxo = u32(d, mo + 12) + mo, u32(d, mo + 16), u32(d, mo + 0x1c) + mo
        for j in range(s2n):
            e = s2o + j * 0x80
            if u32(d, e + 4) not in (0x06, 0x0d): continue
            cnt, extra = u32(d, e + L_COUNT), u32(d, e + L_EXTRA) + sxo
            subs = [dict(off=extra + SUB * k, rate=u32(d, extra + SUB * k), ch=u16(d, extra + SUB * k + 6),
                         stype=u32(d, extra + SUB * k + 8), ns=u32(d, extra + SUB * k + 0x14)) for k in range(cnt)]
            out.append(dict(map=mname, idx=j, off=e, id=u32(d, e), rname=cstr(d, e + L_NAME, 0x28), count=cnt,
                            soff=u32(d, e + L_SOFF), size=u32(d, e + L_SIZE), subs=subs))
    return out


def rebuild_lm0(d, repl):
    """Reconstruye el LM0 con sonidos internos de tamaño libre.
    repl: {(mapa, idx_sec2): (blob_bytes, ns[, ns2])}. Cada subbloque se recompone (sonidos contiguos, alineados a 4) y se
    desplazan los datos posteriores; se actualizan offsets de tabla1, tamaños/offset absoluto de tabla2, entrada de audio
    (size, size2, samples) y la tabla de mapas del header. Los mapas sin cambios se copian tal cual (solo se reubican)."""
    d = bytearray(d)
    al = lambda x: (x + 3) // 4 * 4
    mstart, mnum = u32(d, 4), u32(d, 8)
    maps = []
    for i in range(mnum):
        o = mstart + i * 0x30
        mo, ms = u32(d, o + 8), u32(d, o + 12)
        maps.append(dict(tab=o, name=cstr(d, o + 0x10, 0x20), mo=mo, ms=ms, s2o=u32(d, mo + 12) + mo,
                         s3o=u32(d, mo + 0x14) + mo, s3n=u32(d, mo + 0x18)))
    ends = [m['mo'] for m in maps[1:]] + [len(d)]
    pending = {k for k in repl}
    parts, pos = [], maps[0]['mo']
    for m, old_end in zip(maps, ends):
        mo, ms, new_mo = m['mo'], m['ms'], pos
        struct.pack_into('<I', d, m['tab'] + 8, new_mo)
        mine = {k: v for k, v in repl.items() if k[0] == m['name']}
        subs = []
        if not mine:
            for i in range(m['s3n']):                       # solo reubicar: sumar el desplazamiento al offset absoluto
                o = m['s3o'] + 0x14 * i
                t2, n2 = u32(d, o + 12) + m['s3o'], u32(d, o + 16)
                for k in range(n2): struct.pack_into('<I', d, t2 + 16 * k + 12, u32(d, t2 + 16 * k + 12) + new_mo - mo)
            subs.append(bytes(d[mo + ms:old_end]))
        else:
            if m['s3n'] != 1: raise ValueError(f"{m['name']}: {m['s3n']} tablas de sección 3 (solo se soporta 1)")
            o = m['s3o']
            t1, n1, t2, n2 = u32(d, o + 4) + o, u32(d, o + 8), u32(d, o + 12) + o, u32(d, o + 16)
            groups = {}
            for j in range(n1):
                idx, so = u32(d, t1 + 8 * j) & 0x3FFFFFFF, u32(d, t1 + 8 * j + 4)
                e = m['s2o'] + idx * 0x80
                sid = 0 if u32(d, e + E_STYPE) == 1 else 1
                groups.setdefault(sid, []).append((so, idx, j, e, u32(d, e + E_SIZE)))
            cursor = new_mo + ms
            for k in sorted(range(n2), key=lambda k: u32(d, t2 + 16 * k + 12)):
                sid, old_abs, old_size = u32(d, t2 + 16 * k), u32(d, t2 + 16 * k + 12), u32(d, t2 + 16 * k + 4)
                lst = sorted(groups.get(sid, []))
                if not lst or lst[0][0] != 0 or any(a[0] + al(a[4]) != b[0] for a, b in zip(lst, lst[1:])) or lst[-1][0] + al(lst[-1][4]) != old_size:
                    raise ValueError(f"{m['name']}: subbloque {sid} con layout irregular")
                data = bytearray()
                for so, idx, j, e, size in lst:
                    region = bytes(d[old_abs + so:old_abs + so + al(size)])
                    if (m['name'], idx) in repl:
                        blob, ns, ns2 = (tuple(repl[(m['name'], idx)]) + (None,))[:3]
                        region = blob + (region[len(blob):] if len(blob) == size else b'\0' * (-len(blob) % 4))
                        old_size2 = u32(d, e + E_SIZE2)
                        struct.pack_into('<I', d, e + E_SIZE, len(blob)); struct.pack_into('<I', d, e + E_NS, ns)
                        if ns2 is not None: struct.pack_into('<I', d, e + E_NS2, ns2)
                        if old_size2 == size: struct.pack_into('<I', d, e + E_SIZE2, len(blob))
                        pending.discard((m['name'], idx))
                    struct.pack_into('<I', d, t1 + 8 * j + 4, len(data))
                    data += region
                struct.pack_into('<I', d, t2 + 16 * k + 4, len(data)); struct.pack_into('<I', d, t2 + 16 * k + 8, len(data))
                struct.pack_into('<I', d, t2 + 16 * k + 12, cursor)
                cursor += len(data); subs.append(bytes(data))
        parts.append((mo, ms, subs))
        pos = new_mo + ms + sum(len(x) for x in subs)
    if pending: raise ValueError(f'sonidos internos no encontrados en el LM0: {sorted(pending)[:5]}')
    out = bytearray(d[:maps[0]['mo']])
    for mo, ms, subs in parts:
        out += d[mo:mo + ms]
        for x in subs: out += x
    return bytes(out)


def verify_lm0(d):
    """comprueba que el LM0 sea consistente (mapas y subbloques contiguos, tamaños alineados, fin = tamaño del archivo)"""
    errs, mstart, mnum = [], u32(d, 4), u32(d, 8)
    pos = u32(d, mstart + 8)
    for i in range(mnum):
        o = mstart + i * 0x30
        mo, ms = u32(d, o + 8), u32(d, o + 12)
        if mo != pos: errs.append(f'mapa {i}: offset {mo:#x} != {pos:#x}')
        s3o, s3n = u32(d, mo + 0x14) + mo, u32(d, mo + 0x18)
        cur = mo + ms
        for j in range(s3n):
            q = s3o + 0x14 * j
            t2, n2 = u32(d, q + 12) + s3o, u32(d, q + 16)
            for k in range(n2):
                sid, sz, sz2, ab = struct.unpack_from('<4I', d, t2 + 16 * k)
                if ab != cur or sz != sz2 or sz % 4: errs.append(f'mapa {i} subbloque {sid}: offset/tamaño inconsistente')
                cur = ab + sz
        pos = cur
    if pos != len(d): errs.append(f'fin de datos {pos:#x} != tamaño {len(d):#x}')
    return errs


def missing_streams(ents, folder):
    """[(nombre, nº de sonidos)] de los streams externos que el mapa referencia y no están en la carpeta"""
    have = {n.lower() for n in os.listdir(folder)}
    cnt = {}
    for e in ents:
        if e['streamed'] and e['rname'].lower() not in have: cnt[e['rname']] = cnt.get(e['rname'], 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])


def describe_map(lm0, ents, folder, log, d=None):
    maps = len({e['map'] for e in ents}); ext = [e for e in ents if e['streamed']]
    log(f'{os.path.basename(lm0)}: {maps} mapas con audio, {len(ents) - len(ext)} sonidos internos, {len(ext)} entradas de stream externo '
        f'({len({e["rname"] for e in ext})} archivos)')
    if d is not None:
        lay = parse_layers(d)
        if lay:
            have = {n.lower() for n in os.listdir(folder)}
            log(f'  música en capas: {len(lay)} entradas ({len({(x["rname"], x["soff"], x["size"]) for x in lay})} streams de {lay[0]["count"]} capas) en '
                + ', '.join(sorted({x['rname'] for x in lay})) + ('' if all(x['rname'].lower() in have for x in lay) else '  [AVISO: falta el archivo en la carpeta]'))
    miss = missing_streams(ents, folder)
    if miss:
        log(f'  AVISO faltan {len(miss)} archivo(s) de stream en la carpeta: ' + ', '.join(f'{n} ({c})' for n, c in miss[:8]) + (' …' if len(miss) > 8 else ''))


def streams_by_file(ents, folder):
    """{rname_real: {soff: [entradas]}} solo para LS0 presentes y stream_type 2 (Ubi ADPCM)"""
    files = {n.lower(): n for n in os.listdir(folder)}
    out, skipped = {}, {}
    for e in ents:
        if not e['streamed']: continue
        real = files.get(e['rname'].lower())
        if not real: continue
        if e['stype'] not in (2, 4) or e['ch'] not in (1, 2):
            skipped[(real, e['soff'])] = e; continue
        out.setdefault(real, {}).setdefault(e['soff'], []).append(e)
    return out, skipped


def wav_name(rname, soff, sid): return f"{os.path.splitext(rname)[0]}_{soff:08X}_{sid:08X}.wav"


def internal_name(e): return f"{os.path.splitext(e['rname'])[0]}_{e['idx']:04d}.wav"


def layer_wav_name(rname, soff, sid, k): return f"{os.path.splitext(rname)[0]}_{soff:08X}_{sid:08X}_L{k}.wav"


# ------------------------------------------------------------------ WAV (PCM16 mono/estéreo, con bucle en el chunk 'smpl')
from array import array


def _pack16(pcm):
    a = array('h', pcm)
    if sys.byteorder == 'big': a.byteswap()
    return a.tobytes()


def _unpack16(raw):
    a = array('h'); a.frombytes(bytes(raw[:len(raw) // 2 * 2]))
    if sys.byteorder == 'big': a.byteswap()
    return a.tolist()


def _chunk(cid, body): return cid + struct.pack('<I', len(body)) + body + (b'\0' if len(body) & 1 else b'')


def write_wav(path, pcm, rate=RATE, channels=1, loop_start=None):
    body = b'WAVE' + _chunk(b'fmt ', struct.pack('<HHIIHH', 1, channels, rate, rate * 2 * channels, 2 * channels, 16))
    body += _chunk(b'data', _pack16(pcm))
    if loop_start is not None:
        total = len(pcm) // channels
        body += _chunk(b'smpl', struct.pack('<9I', 0, 0, round(1e9 / rate), 60, 0, 0, 0, 1, 0) +
                       struct.pack('<6I', 0, 0, loop_start, max(loop_start, total - 1), 0, 0))
    with open(path, 'wb') as f: f.write(b'RIFF' + struct.pack('<I', len(body)) + body)


def wav_info(path):
    """cabecera de un WAV -> dict(rate, ch, bits, tag, data=(offset, size), loop, raw)"""
    raw = open(path, 'rb').read()
    if raw[:4] != b'RIFF' or raw[8:12] != b'WAVE': raise ValueError(f'{os.path.basename(path)}: no es un WAV')
    pos, info = 12, dict(rate=0, ch=0, bits=0, tag=0, data=None, loop=None, raw=raw)
    while pos + 8 <= len(raw):
        cid, sz = raw[pos:pos + 4], struct.unpack_from('<I', raw, pos + 4)[0]
        b = pos + 8
        if cid == b'fmt ' and sz >= 16:
            info['tag'], info['ch'], info['rate'], _, _, info['bits'] = struct.unpack_from('<HHIIHH', raw, b)
            if info['tag'] == 0xFFFE and sz >= 26: info['tag'] = struct.unpack_from('<H', raw, b + 24)[0]
        elif cid == b'data': info['data'] = (b, min(sz, len(raw) - b))
        elif cid == b'smpl' and sz >= 60 and struct.unpack_from('<I', raw, b + 28)[0] >= 1: info['loop'] = struct.unpack_from('<I', raw, b + 44)[0]
        pos = b + sz + (sz & 1)
    if not info['data'] or not info['ch']: raise ValueError(f'{os.path.basename(path)}: WAV incompleto')
    return info


def wav_rate(path): return wav_info(path)['rate']


def _remix(pcm, ch, want):
    if ch == want: return pcm
    if want == 1: return [sum(v) // ch for v in zip(*[pcm[c::ch] for c in range(ch)])]
    if ch == 1: return [x for v in pcm for x in (v, v)]
    out = [0] * (2 * (len(pcm) // ch)); out[0::2] = pcm[0::ch]; out[1::2] = pcm[1::ch]
    return out


def resample(pcm, src, dst):
    """remuestreo sinc con ventana de Hann y corte antialias (numpy si está disponible)"""
    if src == dst or not pcm: return list(pcm)
    ratio, half = dst / src, 16
    fc = min(1.0, ratio)                                   # corte relativo a la tasa de entrada
    wlen = half / fc                                       # semiancho del kernel en muestras de entrada
    n_out = int(len(pcm) * ratio)
    try:
        import numpy as np
        x = np.asarray(pcm, dtype=np.float64); out = np.empty(n_out); W = int(2 * wlen) + 3
        for s0 in range(0, n_out, 8192):
            j = np.arange(s0, min(n_out, s0 + 8192)); tpos = j / ratio
            base = np.floor(tpos).astype(np.int64) - int(wlen) - 1
            idx = base[:, None] + np.arange(W)[None, :]
            dx = idx - tpos[:, None]
            h = fc * np.sinc(fc * dx) * (0.5 + 0.5 * np.cos(np.pi * np.clip(dx / wlen, -1, 1)))
            h[np.abs(dx) >= wlen] = 0
            valid = (idx >= 0) & (idx < len(x))
            h = h * valid                                  # fuera de rango no cuenta (igual que la ruta sin numpy)
            out[j] = (h * x[np.clip(idx, 0, len(x) - 1)]).sum(1) / np.where(np.abs(h.sum(1)) < 1e-9, 1e-9, h.sum(1))
        return np.clip(np.rint(out), -32768, 32767).astype(np.int64).tolist()
    except ImportError:
        import math
        out = []
        for j in range(n_out):
            tpos = j / ratio; lo, hi = int(tpos - wlen) + 1, int(tpos + wlen)
            acc = norm = 0.0
            for i in range(max(lo, 0), min(hi, len(pcm) - 1) + 1):
                dx = i - tpos; xx = fc * dx
                w = fc * (math.sin(math.pi * xx) / (math.pi * xx) if xx else 1.0) * (0.5 + 0.5 * math.cos(math.pi * dx / wlen))
                acc += w * pcm[i]; norm += w
            out.append(max(-32768, min(32767, int(round(acc / (norm or 1e-9))))))
        return out


def resample_ch(pcm, ch, src, dst):
    if ch == 1: return resample(pcm, src, dst)
    chans = [resample(pcm[c::ch], src, dst) for c in range(ch)]
    out = [0] * (len(chans[0]) * ch)
    for c in range(ch): out[c::ch] = chans[c]
    return out


def read_wav(path, log=print, target=RATE, channels=1):
    """WAV -> (muestras int16 entrelazadas en `channels` canales, inicio de bucle en frames o None).
    target=None conserva la tasa del WAV; si no, remuestrea. Mono/estéreo se convierten a `channels`."""
    info = wav_info(path)
    rate, ch, loop = info['rate'], info['ch'], info['loop']
    to = rate if target is None else target
    ff = shutil.which('ffmpeg')
    native = info['bits'] == 16 and info['tag'] == 1
    if (not native or rate != to) and ff:
        r = subprocess.run([ff, '-v', 'error', '-i', path, '-ac', str(channels), '-ar', str(to), '-f', 's16le', '-'], capture_output=True)
        if r.returncode == 0:
            return _unpack16(r.stdout), (None if loop is None else round(loop * to / rate))
    if not native: raise ValueError(f'{os.path.basename(path)}: se requiere WAV PCM de 16 bits (o ffmpeg instalado para convertirlo)')
    off, size = info['data']
    pcm = _remix(_unpack16(info['raw'][off:off + size]), ch, channels)
    if rate != to:
        log(f'  aviso: {os.path.basename(path)} está a {rate} Hz; remuestreo propio a {to} Hz (con ffmpeg instalado sale más rápido)')
        pcm = resample_ch(pcm, channels, rate, to)
        if loop is not None: loop = round(loop * to / rate)
    return pcm, loop


MIN_RATE, MAX_RATE = 1000, 48000


def set_rate(d, off, stype, rate, ch=1):
    """tasa de una entrada: +0x44 = rate; +0x40 (bytes/s): rate*2*ch en PCM16, rate*ch en Ubi IMA, 19800*rate/36000*ch en ADPCM"""
    if stype == 1: struct.pack_into('<I', d, off + 0x40, rate * 2 * ch)
    elif stype == 4: struct.pack_into('<I', d, off + 0x40, rate * ch)
    elif stype == 2: struct.pack_into('<I', d, off + 0x40, round(19800 * rate / 36000) * ch)
    else: raise ValueError(f'stream_type {stype} sin soporte')
    struct.pack_into('<I', d, off + E_RATE, rate)


def pick_rate(e, path, keep_rate, log):
    """tasa destino para un WAV: la del WAV si keep_rate (hasta MAX_RATE), si no la original de la entrada"""
    if not keep_rate or e['stype'] not in (1, 2, 4): return e['rate']
    r = wav_rate(path)
    if not MIN_RATE <= r <= MAX_RATE:
        log(f'  aviso: {os.path.basename(path)}: {r} Hz fuera de {MIN_RATE}-{MAX_RATE}; se remuestrea a {e["rate"]} Hz'); return e['rate']
    return r


def calc_samples(e, total, loop_start=None):
    """campos (ns, ns2) de la entrada para `total` frames; con bucle se conserva (o se mueve, si el WAV trae 'smpl') el inicio"""
    if e['loop']:
        ls = e['ns'] if loop_start is None else loop_start
        ls = max(0, min(ls, max(total - 1, 0)))
        return ls, total - ls
    return total, e['ns2']


# ------------------------------------------------------------------ códecs (mono/estéreo)
def _enc_adpcm(chunk, pcm, rate):
    h = ua.Header(chunk); h.u18 = rate                         # el header ADPCM repite la tasa
    if h.channels == 2:
        st = [ua.State.unpack(chunk[0x30:0x30 + ua.SIZE_CH]), ua.State.unpack(chunk[0x30 + ua.SIZE_CH:0x30 + 2 * ua.SIZE_CH])]
        return ua.encode(pcm, template_header=h, init_states=st)
    return ua.encode(pcm, template_header=h, init_state=ua.State.unpack(chunk[0x30:0x30 + ua.SIZE_CH]))


def dec_ext(e, chunk):
    """stream externo -> (pcm entrelazado, codec, avisos, canales)"""
    if e['stype'] == 2:
        pcm, h, mm = ua.decode(chunk, check_states=True)
        return pcm, f'adpcm{h.bps}', mm, h.channels
    pcm, h = im.decode(chunk, e['total'], e['ch'])
    return pcm, f'ima_v{h.version}', 0, e['ch']


def enc_ext(e, pcm, chunk, rate=None, channels=1):
    if e['stype'] == 2: return _enc_adpcm(chunk, pcm, rate or e['rate'])
    return im.encode(pcm, chunk, channels)


def int_raw(d, e): return bytes(d[e['ioff']:e['ioff'] + e['size']])


def dec_int(d, e):
    """sonido interno -> (pcm entrelazado, codec, canales) o None si no soportado/inconsistente"""
    if e['streamed'] or e['ioff'] is None or e['ch'] not in (1, 2): return None
    raw = int_raw(d, e)
    if e['stype'] == 1:
        if e['size'] != e['total'] * 2 * e['ch']: return None
        return _unpack16(raw), 'pcm16', e['ch']
    if e['stype'] == 4:
        pcm, h = im.decode(raw, e['total'], e['ch'])
        return pcm, f'ima_v{h.version}', e['ch']
    if e['stype'] == 2:
        pcm, h, _ = ua.decode(raw, check_states=True)
        return pcm, f'adpcm{h.bps}', h.channels
    return None


def enc_int(d, e, pcm, ch):
    if e['stype'] == 1: return _pack16(pcm)
    if e['stype'] == 2: return _enc_adpcm(int_raw(d, e), pcm, e['rate'])
    return im.encode(pcm, int_raw(d, e), ch)


# ------------------------------------------------------------------ música en capas (STREAM.SS0)
def layer_streams(d, folder):
    """{(archivo_real, soff, size): [entradas de capas]} para los streams en capas cuyo archivo está en la carpeta"""
    files = {n.lower(): n for n in os.listdir(folder)}
    out = {}
    for L in parse_layers(d):
        real = files.get(L['rname'].lower())
        if real: out.setdefault((real, L['soff'], L['size']), []).append(L)
    for v in out.values(): v.sort(key=lambda x: x['id'])
    return out


def extract_layers(d, ls0_dir, out_dir, log):
    uniq = layer_streams(d, ls0_dir)
    if not uniq: log('  capas: no hay streams en capas con su archivo en la carpeta'); return []
    ldir = os.path.join(out_dir, 'layers'); os.makedirs(ldir, exist_ok=True)
    index, rows = {}, []
    for n, (key, ents) in enumerate(sorted(uniq.items(), key=lambda kv: (kv[0][0], kv[0][1])), 1):
        real, soff, size = key
        with open(os.path.join(ls0_dir, real), 'rb') as f: f.seek(soff); data = f.read(size)
        p = ul.parse(data)
        for k, sub in enumerate(ents[0]['subs']):
            pcm, h = im.decode(p['layers'][k], sub['ns'], sub['ch'])
            name = layer_wav_name(real, soff, ents[0]['id'], k)
            write_wav(os.path.join(ldir, name), pcm, sub['rate'], sub['ch'])
            index[name] = dict(crc=zlib.crc32(_pack16(pcm)), frames=len(pcm) // sub['ch'])
            rows.append([f'layers/{name}', real, soff, len(p['layers'][k]), len(pcm) // sub['ch'], sub['rate'], sub['ch'], f'ima_v{h.version}',
                         '', '|'.join(sorted({x['map'] for x in ents}))])
        if n % 10 == 0 or n == len(uniq): log(f'  capas: {n}/{len(uniq)} streams')
    json.dump(index, open(os.path.join(ldir, 'layers_index.json'), 'w'))
    return rows


def import_layers(d, ls0_dir, wav_dir, out_dir, force_all, log):
    """recodifica las capas cuyo WAV cambió (las demás se reutilizan), reconstruye el stream y parchea las entradas de d"""
    ldir = os.path.join(wav_dir, 'layers')
    if not os.path.isdir(ldir): return 0
    uniq = layer_streams(d, ls0_dir)
    try: index = json.load(open(os.path.join(ldir, 'layers_index.json')))
    except Exception: index = {}
    by_file, total = {}, 0
    for key in uniq: by_file.setdefault(key[0], []).append(key)
    for real, keys in sorted(by_file.items()):
        keys.sort(key=lambda k: k[1])
        path = os.path.join(ls0_dir, real)
        src = open(path, 'rb'); newblobs = {}
        for key in keys:
            _, soff, size = key; ents = uniq[key]; subs = ents[0]['subs']
            wavs = [os.path.join(ldir, layer_wav_name(real, soff, ents[0]['id'], k)) for k in range(len(subs))]
            if not any(os.path.isfile(p) for p in wavs): continue
            src.seek(soff); parsed = ul.parse(src.read(size)); orig = parsed['layers']
            new = {}
            for k, p in enumerate(wavs):
                if not os.path.isfile(p): continue
                sub = subs[k]
                pcm, _ = read_wav(p, log, sub['rate'], sub['ch'])            # las capas mantienen su tasa (36000 Hz)
                rec = index.get(os.path.basename(p))
                if force_all: changed = True
                elif rec and rec['crc'] == zlib.crc32(_pack16(pcm)) and rec['frames'] == len(pcm) // sub['ch']: changed = False
                else: changed = pcm != im.decode(orig[k], sub['ns'], sub['ch'])[0]
                if changed: new[k] = pcm
            if not new: continue
            frames = [len(new[k]) // subs[k]['ch'] if k in new else subs[k]['ns'] for k in range(len(subs))]
            N = max(frames)
            if N < 10: log(f'  OMITIDO {real}@{soff:#x}: mínimo 10 samples'); continue
            layers = []
            for k, sub in enumerate(subs):                                  # todas las capas deben durar lo mismo: se rellena con silencio
                if k in new: pcm = new[k]
                elif frames[k] < N: pcm = im.decode(orig[k], sub['ns'], sub['ch'])[0]
                else: layers.append(orig[k]); continue
                pcm = pcm + [0] * ((N - len(pcm) // sub['ch']) * sub['ch'])
                layers.append(im.encode(pcm, orig[k], sub['ch']))
            newblobs[key] = (ul.build(layers, round_size=parsed['head'][4]), N)
            log(f'  capas {real}@{soff:#x}: {len(new)} capa(s) cambiada(s), {size} -> {len(newblobs[key][0])} bytes, {N} frames ({N / subs[0]["rate"]:.2f} s)')
        if not newblobs: src.close(); continue
        pos, newpos = 0, {}                                                  # nuevo archivo: streams contiguos alineados a 4
        if keys[0][1] != 0 or any(b[1] - (a[1] + a[2]) > 3 for a, b in zip(keys, keys[1:])):
            raise ValueError(f'{real}: hay datos fuera de los streams en capas conocidos; no se puede reconstruir con seguridad')
        with open(os.path.join(out_dir, real), 'wb') as out:
            for key in keys:
                if key in newblobs: blob = newblobs[key][0]
                else: src.seek(key[1]); blob = src.read(key[2])
                if pos % 4: out.write(b'\0' * (-pos % 4)); pos += -pos % 4
                newpos[key] = (pos, len(blob)); out.write(blob); pos += len(blob)
            out.write(b'\0' * (-pos % 4))
        src.close()
        for key, (npos, nsize) in newpos.items():
            for L in uniq[key]:
                o = L['off']
                struct.pack_into('<I', d, o + L_SOFF, npos); struct.pack_into('<I', d, o + L_SIZE, nsize)
                struct.pack_into('<I', d, o + E_SIZE, nsize); struct.pack_into('<I', d, o + E_SOFF, npos)   # copias del tamaño y del offset
                if key in newblobs:
                    for sub in L['subs']: struct.pack_into('<I', d, sub['off'] + 0x14, newblobs[key][1])
        total += len(newblobs)
        log(f'{real}: {len(newblobs)} stream(s) en capas reemplazado(s)')
    return total


# ------------------------------------------------------------------ extracción / importación
def extract(lm0, ls0_dir, out_dir, log=print, layers=False):
    d, ents = parse_lm0(lm0)
    files, skipped = streams_by_file(ents, ls0_dir)
    describe_map(lm0, ents, ls0_dir, log, d)
    os.makedirs(out_dir, exist_ok=True)
    rows, bad = [], 0
    for rname, sts in sorted(files.items()):
        data = open(os.path.join(ls0_dir, rname), 'rb').read()
        kinds = {}
        for soff, es in sorted(sts.items()):
            e = es[0]
            pcm, codec, mm, ch = dec_ext(e, data[soff:soff + e['size']])
            kinds[codec] = kinds.get(codec, 0) + 1
            frames = len(pcm) // ch
            if mm or (e['total'] and frames != e['total']): bad += 1; log(f'  AVISO {rname}@{soff:#x}: {mm} frames con estado inconsistente, {frames}/{e["total"]} samples')
            name = wav_name(rname, soff, e['id'])
            write_wav(os.path.join(out_dir, name), pcm, e['rate'], ch, e['ns'] if e['loop'] else None)
            rows.append([name, rname, soff, e['size'], frames, e['rate'], ch, codec, 'si' if e['loop'] else '', '|'.join(sorted({x['map'] for x in es}))])
        log(f'{rname}: {len(sts)} streams {kinds}')
    kinds = {}
    for e in ents:
        if e['streamed']: continue
        r = dec_int(d, e)
        if r is None:
            log(f'  AVISO interno {e["map"]}/{e["rname"]}: stream_type {e["stype"]} / {e["ch"]}ch no soportado o tamaño inconsistente'); continue
        pcm, codec, ch = r
        kinds[codec] = kinds.get(codec, 0) + 1
        sub = os.path.join(out_dir, 'internal', e['map']); os.makedirs(sub, exist_ok=True)
        write_wav(os.path.join(sub, internal_name(e)), pcm, e['rate'], ch, e['ns'] if e['loop'] else None)
        rows.append([f"internal/{e['map']}/{internal_name(e)}", os.path.basename(lm0), e['ioff'], e['size'], len(pcm) // ch, e['rate'], ch, codec,
                     'si' if e['loop'] else '', e['map']])
    log(f'internos: {kinds}')
    if layers: rows += extract_layers(d, ls0_dir, out_dir, log)
    with open(os.path.join(out_dir, 'index.csv'), 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['wav', 'origen', 'offset', 'size', 'samples', 'rate', 'canales', 'codec', 'bucle', 'mapas']); w.writerows(rows)
    for (rn, so), e in skipped.items(): log(f'omitido {rn}@{so:#x}: stream_type {e["stype"]} / {e["ch"]}ch')
    log(f'listo: {len(rows)} WAV en {out_dir}' + (f' ({bad} con avisos)' if bad else ''))


def import_wavs(lm0, ls0_dir, wav_dir, out_dir, force_all=False, keep_rate=True, log=print):
    d, ents = parse_lm0(lm0)
    files, _ = streams_by_file(ents, ls0_dir)
    describe_map(lm0, ents, ls0_dir, log, d)
    os.makedirs(out_dir, exist_ok=True)
    changed_total = 0
    for rname, sts in sorted(files.items()):
        data = open(os.path.join(ls0_dir, rname), 'rb').read()
        order = sorted(sts)
        replaced, meta = {}, {}
        for soff in order:
            e = sts[soff][0]
            p = os.path.join(wav_dir, wav_name(rname, soff, e['id']))
            if not os.path.isfile(p): continue
            old = data[soff:soff + e['size']]
            old_pcm, codec, _, ch = dec_ext(e, old)
            new_rate = pick_rate(e, p, keep_rate, log)
            pcm, loop = read_wav(p, log, new_rate, ch)
            frames = len(pcm) // ch
            loop_moved = e['loop'] and loop is not None and loop != e['ns']
            if not force_all and pcm == old_pcm and new_rate == e['rate'] and not loop_moved: continue    # WAV sin cambios: se conserva el original
            if e['stype'] == 4 and frames < 10: log(f'  OMITIDO {os.path.basename(p)}: mínimo 10 samples'); continue
            replaced[soff] = enc_ext(e, pcm, old, new_rate, ch); meta[soff] = (new_rate, frames, loop, ch)
            log(f'  {os.path.basename(p)}: {frames} samples x {ch}ch -> {len(replaced[soff])} bytes ({codec})')
        if not replaced:
            log(f'{rname}: sin cambios'); continue
        new, newpos = bytearray(), {}
        for soff in order:
            e = sts[soff][0]
            blob = replaced.get(soff, data[soff:soff + e['size']])
            new += b'\0' * (-len(new) % 4)
            newpos[soff] = (len(new), len(blob))
            new += blob
        new += b'\0' * (-len(new) % 4)
        for soff, (npos, nsize) in newpos.items():
            for e in sts[soff]:
                o = e['off']
                struct.pack_into('<I', d, o + E_SOFF, npos); struct.pack_into('<I', d, o + E_SIZE, nsize); struct.pack_into('<I', d, o + E_SIZE2, nsize)
                if soff in replaced:
                    rate, frames, loop, ch = meta[soff]
                    ns, ns2 = calc_samples(e, frames, loop)
                    struct.pack_into('<I', d, o + E_NS, ns); struct.pack_into('<I', d, o + E_NS2, ns2)
                    if rate != e['rate']: set_rate(d, o, e['stype'], rate, ch)
        open(os.path.join(out_dir, rname), 'wb').write(new)
        changed_total += len(replaced)
        log(f'{rname}: {len(replaced)} stream(s) reemplazado(s), {len(data)} -> {len(new)} bytes')
    changed_total += import_layers(d, ls0_dir, wav_dir, out_dir, force_all, log)
    # sonidos internos (PCM16 / Ubi IMA / Ubi ADPCM): tamaño libre; el mapa se reconstruye reubicando los datos posteriores
    repl = {}
    for e in ents:
        p = os.path.join(wav_dir, 'internal', e['map'], internal_name(e))
        if e['streamed'] or not os.path.isfile(p): continue
        r = dec_int(d, e)
        if r is None: continue
        old_pcm, codec, ch = r
        new_rate = pick_rate(e, p, keep_rate, log)
        pcm, loop = read_wav(p, log, new_rate, ch)
        frames = len(pcm) // ch
        loop_moved = e['loop'] and loop is not None and loop != e['ns']
        if not force_all and pcm == old_pcm and new_rate == e['rate'] and not loop_moved: continue
        if e['stype'] == 4 and frames < 10: log(f'  OMITIDO {e["map"]}/{internal_name(e)}: mínimo 10 samples'); continue
        blob = enc_int(d, e, pcm, ch)
        ns, ns2 = calc_samples(e, frames, loop)
        repl[(e['map'], e['idx'])] = (blob, ns, ns2)
        if new_rate != e['rate']: set_rate(d, e['off'], e['stype'], new_rate, ch); log(f'  {e["map"]}/{internal_name(e)}: {e["rate"]} -> {new_rate} Hz')
        if len(blob) != e['size']: log(f'  {e["map"]}/{internal_name(e)}: {e["size"]} -> {len(blob)} bytes')
    out = bytes(rebuild_lm0(d, repl)) if repl else bytes(d)
    if repl:
        errs = verify_lm0(out)
        if errs: raise ValueError('mapa reconstruido inconsistente, no se escribe: ' + '; '.join(errs[:3]))
        log(f'internos: {len(repl)} sonido(s) reemplazado(s), mapa {len(d)} -> {len(out)} bytes')
    open(os.path.join(out_dir, os.path.basename(lm0)), 'wb').write(out)
    log(f'listo: {changed_total} stream(s) + {len(repl)} interno(s) en {out_dir} (copia los streams y el mapa juntos al juego)')


def gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext
    root = tk.Tk(); root.title('Splinter Cell (2002) - Audio LS0/LM0')
    v = {k: tk.StringVar() for k in ('lm0', 'ls0', 'wav', 'out')}
    force = tk.BooleanVar(); keep = tk.BooleanVar(value=True)
    def row(r, label, key, folder):
        ttk.Label(root, text=label).grid(row=r, column=0, sticky='w', padx=6, pady=3)
        ttk.Entry(root, textvariable=v[key], width=60).grid(row=r, column=1, padx=4)
        def pick():
            p = filedialog.askdirectory() if folder else filedialog.askopenfilename(filetypes=[('LM0', '*.LM0 *.lm0'), ('Todos', '*.*')])
            if p: v[key].set(p)
        ttk.Button(root, text='...', width=3, command=pick).grid(row=r, column=2, padx=4)
    row(0, 'MAPS.LM0', 'lm0', False); row(1, 'Carpeta con .LS0', 'ls0', True)
    row(2, 'Carpeta WAV', 'wav', True); row(3, 'Salida (import)', 'out', True)
    ttk.Checkbutton(root, text='Recodificar todo (prueba)', variable=force).grid(row=4, column=1, sticky='w')
    ttk.Checkbutton(root, text='Usar sample rate del WAV (hasta 48000 Hz)', variable=keep).grid(row=4, column=0, sticky='w', padx=6)
    box = scrolledtext.ScrolledText(root, width=90, height=18, state='disabled'); box.grid(row=6, column=0, columnspan=3, padx=6, pady=6)
    def log(m):
        box.configure(state='normal'); box.insert('end', m + '\n'); box.see('end'); box.configure(state='disabled')
    def run(fn, *a):
        def w():
            try: fn(*a, log=lambda m: root.after(0, log, m))
            except Exception as ex: root.after(0, log, f'ERROR: {ex}')
        threading.Thread(target=w, daemon=True).start()
    ttk.Button(root, text='Extraer a WAV', command=lambda: run(extract, v['lm0'].get(), v['ls0'].get(), v['wav'].get())).grid(row=5, column=0, pady=4)
    ttk.Button(root, text='Importar WAV', command=lambda: run(lambda *a, log: import_wavs(*a, force_all=force.get(), keep_rate=keep.get(), log=log),
               v['lm0'].get(), v['ls0'].get(), v['wav'].get(), v['out'].get())).grid(row=5, column=1, sticky='w')
    root.mainloop()


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        try: import sc1_gui
        except ImportError: gui()                      # sin sc1_gui.py: interfaz básica
        else: sc1_gui.main()
    elif a[0] == 'extract' and len(a) >= 4: extract(*a[1:4], layers='--layers' in a)
    elif a[0] == 'import' and len(a) >= 5: import_wavs(*a[1:5], force_all='--all' in a, keep_rate='--original-rate' not in a)
    else: print(__doc__)
