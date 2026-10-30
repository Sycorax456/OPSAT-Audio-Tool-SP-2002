"""Ubisoft IMA (stream_type 4) - decoder + encoder, mono. Port de vgmstream ima_decoder.c (decode_ubi_ima).
Header: version(u8) ... [big endian si version<5], 10 muestras PCM crudas y luego nibbles IMA (high nibble primero)."""
import struct

STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
        130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060,
        1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484,
        7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
IDX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]

def clamp16(x): return -32768 if x < -32768 else 32767 if x > 32767 else x

def expand(nib, hist, idx):
    d = (((nib & 7) * 2 + 1) * STEP[idx]) >> 3
    if nib & 8: d = -d
    hist = clamp16(hist + d)
    idx = max(0, min(88, idx + IDX[nib]))
    return hist, idx

class Info:
    pass

def parse_header(data, channels=1):
    """devuelve Info(version, big, header_samples, hist, step, hist_off, step_off, pcm_off, nib_off)"""
    i = Info()
    v = data[0]
    if v == 0: raise ValueError('Ubi IMA versión 0 (BAO) sin soporte')
    if v > 7: raise ValueError(f'Ubi IMA versión {v} desconocida')
    i.version, i.big = v, v < 5
    e = '>' if i.big else '<'
    o = 2
    i.header_samples = struct.unpack_from(e + 'h', data, o + 0x0c)[0]
    i.hist_off, i.step_off = o + 0x0e, o + 0x10
    i.hist = struct.unpack_from(e + 'h', data, i.hist_off)[0]
    i.step = struct.unpack_from('b', data, i.step_off)[0]
    i.hists = [struct.unpack_from(e + 'h', data, i.hist_off + 4 * c)[0] for c in range(channels)]   # estado inicial por canal
    i.steps = [struct.unpack_from('b', data, i.step_off + 4 * c)[0] for c in range(channels)]
    o += 0x0e + 0x08
    if v >= 3: o += 4
    if v == 6:
        o += 8 + struct.unpack_from(e + 'i', data, o + 4)[0]
    elif v >= 7:
        o += 6
        n = struct.unpack_from(e + 'i', data, o)[0]
        if n: o += 4 + (n + 1) * 8
    i.pcm_off = o
    i.channels = channels
    i.nib_off = o + i.header_samples * 2 * channels
    i.e = e
    return i

def decode(data, num_samples, channels=1):
    """num_samples = muestras por canal. Devuelve (samples, header); con 2 canales las muestras van entrelazadas L,R,L,R"""
    h = parse_header(data, channels)
    n = h.header_samples
    out = list(struct.unpack_from(h.e + f'{n * channels}h', data, h.pcm_off))
    if channels == 1:
        hist, idx = h.hist, max(0, min(88, h.step))
        for k in range(num_samples - n):
            b = data[h.nib_off + k // 2]
            nib = (b >> 4) if k % 2 == 0 else (b & 15)
            hist, idx = expand(nib, hist, idx)
            out.append(hist)
        return out, h
    assert channels == 2, 'solo mono o estéreo'
    hl, il = h.hists[0], max(0, min(88, h.steps[0])); hr, ir = h.hists[1], max(0, min(88, h.steps[1]))
    base = h.nib_off
    for k in range(num_samples - n):
        b = data[base + k]
        hl, il = expand(b >> 4, hl, il); hr, ir = expand(b & 15, hr, ir)
        out.append(hl); out.append(hr)
    return out, h

def _enc_channel(vals, hist, idx):
    nibs = []
    for t in vals:
        diff = t - hist
        sign = 8 if diff < 0 else 0
        k = (abs(diff) * 8 // STEP[idx] - 1) // 2
        best = None
        for c in (k - 1, k, k + 1):
            nib = max(0, min(7, c)) | sign
            hh, ii = expand(nib, hist, idx)
            e = abs(hh - t)
            if best is None or e < best[0]: best = (e, nib, hh, ii)
        _, nib, hist, idx = best
        nibs.append(nib)
    return nibs


def encode(samples, template, channels=1):
    """samples: int16 (entrelazadas si channels=2; mínimo header_samples por canal). template: bytes de un stream original."""
    h = parse_header(template, channels)
    n = h.header_samples
    assert len(samples) >= n * channels
    head = bytearray(template[:h.nib_off])
    old_head = struct.unpack_from(h.e + f'{n * channels}h', template, h.pcm_off)
    if tuple(samples[:n * channels]) == old_head:
        hists, steps = list(h.hists), [max(0, min(88, s)) for s in h.steps]            # inicio sin cambios: estado original
    else:
        hists = [samples[(n - 1) * channels + c] for c in range(channels)]
        steps = [max(0, min(88, s)) for s in h.steps]
        for c in range(channels):
            struct.pack_into(h.e + 'h', head, h.hist_off + 4 * c, hists[c]); struct.pack_into('b', head, h.step_off + 4 * c, steps[c])
        struct.pack_into(h.e + f'{n * channels}h', head, h.pcm_off, *samples[:n * channels])
    rest = samples[n * channels:]
    if channels == 1:
        nibs = _enc_channel(rest, hists[0], steps[0])
        if len(nibs) % 2: nibs.append(0)
        body = bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))
    else:
        l = _enc_channel(rest[0::2], hists[0], steps[0]); r = _enc_channel(rest[1::2], hists[1], steps[1])
        body = bytes((a << 4) | b for a, b in zip(l, r))
    return bytes(head) + body
