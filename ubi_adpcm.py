"""Ubisoft ADPCM (4-bit, mono) - decoder + encoder. Port de vgmstream ubi_adpcm_decoder.c
(RE base: Zench/DecUbiSnd, sigsegv, bnnm). Solo modo 4-bit mono por ahora."""
import struct

def s16(x): x &= 0xFFFF; return x - 0x10000 if x & 0x8000 else x
def s32(x): x &= 0xFFFFFFFF; return x - 0x100000000 if x & 0x80000000 else x
def clamp16(x): return -32768 if x < -32768 else 32767 if x > 32767 else x
def clamp(x, lo, hi): return lo if x < lo else hi if x > hi else x
def sg(x): return -1 if x < 0 else 1
def absmax16(v, m):
    if v < 0: return -m if v < -m else v
    return m if v > m else v

T1 = [-100000000, 8, 269, 425, 545, 645, 745, 850]
T2 = [-1536, 2314, 5243, 8192, 14336, 25354, 45445, 143626]
DT = [1024,1031,1053,1076,1099,1123,1148,1172,1198,1224,1251,1278,1306,1334,1363,1393,
      1423,1454,1485,1518,1551,1584,1619,1654,1690,1726,1764,1802,1841,1881,1922,1964,2007]
DT = DT + [-v for v in DT]          # 66 entradas: [0..32] positivas, [33..65] negativas

T1_6 = [-100000000, -369, -245, -133, -33, 56, 135, 207, 275, 338, 395, 448, 499, 548, 593, 635,
        676, 717, 755, 791, 825, 858, 889, 919, 948, 975, 1003, 1029, 1054, 1078, 1103, 1132,
        1800, 1800, 1800, 2048, 3072, 4096, 5000, 5056, 5184, 5240, 6144, 6880, 9624, 12880, 14952, 18040,
        20480, 22920, 25600, 28040, 32560, 35840, 40960, 45832, 51200, 56320, 63488, 67704, 75776, 89088, 102400, 0]
T2_6 = [1800, 1800, 1800, 2048, 3072, 4096, 5000, 5056, 5184, 5240, 6144, 6880, 9624, 12880, 14952, 18040,
        20480, 22920, 25600, 28040, 32560, 35840, 40960, 45832, 51200, 56320, 63488, 67704, 75776, 89088, 102400, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5, 6, 6, 6, 7]

SIZE_HDR = 0x30
SIZE_CH = 0x34
FIELDS = ('step1','next1','next2','coef1','coef2','unused1','unused2','mod1','mod2','mod3','mod4',
          'hist1','hist2','unused3','unused4','delta1','delta2','delta3','delta4','delta5','unused5')

class State:
    __slots__ = FIELDS
    def __init__(self):
        for f in FIELDS: setattr(self, f, 0)
    def copy(self):
        n = State()
        for f in FIELDS: setattr(n, f, getattr(self, f))
        return n
    def pack(self):
        return struct.pack('<I3i18h', 2,
            self.step1, self.next1, self.next2,
            self.coef1, self.coef2, self.unused1, self.unused2, self.mod1, self.mod2, self.mod3, self.mod4,
            self.hist1, self.hist2, self.unused3, self.unused4, self.delta1, self.delta2, self.delta3, self.delta4,
            self.delta5, self.unused5)
    @staticmethod
    def unpack(b):
        v = struct.unpack('<I3i18h', b[:SIZE_CH])
        s = State()
        for f, x in zip(FIELDS, v[1:]): setattr(s, f, x)
        return s
    def key(self):  # campos significativos (sin unused)
        return tuple(getattr(self, f) for f in FIELDS if not f.startswith('unused'))

def _delta0(code_signed, step1):
    idx = abs(code_signed)
    step0_next = s32(T1[idx] + step1)
    d = 0
    x = (step0_next & 0xFFFFFFFF) & 0xFFFFFF00
    if not (((x - 1) & 0xFFFFFFFF) & 0x80000000):
        di = ((step0_next >> 3) & 0x1F) + (33 if code_signed < 0 else 0)
        sh = clamp((step0_next >> 8) & 0xFF, 0, 31)
        d = s32(DT[di] << sh) >> 10
    return d

def _step0(code_signed, step1):
    idx = abs(code_signed)
    s = (step1 & 0xFFFF) * 246
    return clamp((s + T2[idx]) >> 8, 271, 2560)

def _pred(st):
    next0 = s16(s32(st.mod1*st.delta1 + st.mod2*st.delta2 + st.mod3*st.delta3 + st.mod4*st.delta4) >> 10)
    base = (st.coef1*st.hist1 + st.coef2*st.hist2) >> 10
    return next0, base

def _delta0_6(cs, step1):
    step0_next = s32(T1_6[abs(cs)] + step1)
    x = (step0_next & 0xFFFFFFFF) & 0xFFFFFF00
    if ((x - 1) & 0xFFFFFFFF) & 0x80000000: return 0
    di = ((step0_next >> 3) & 0x1F) + (33 if cs < 0 else 0)
    return s32(DT[di] << clamp((step0_next >> 8) & 0xFF, 0, 31)) >> 10

def expand_code6(code, st):
    cs = code - 31
    delta0 = _delta0_6(cs, st.step1)
    step0 = clamp(((st.step1 & 0xFFFF) * 246 + T2_6[abs(cs)]) >> 8, 271, 2560)
    sample = s16(delta0 + st.delta1 + st.hist1)
    st.hist1 = sample; st.step1 = step0; st.delta1 = s16(delta0)
    return sample

def expand_code(code, st):
    cs = code - 7
    delta0 = _delta0(cs, st.step1)
    step0 = _step0(cs, st.step1)
    next0, base = _pred(st)
    sample = s16(delta0 + next0 + base)
    c1n = st.coef1 * 255
    c2n = st.coef2 * 254
    delta0 = s16(delta0)
    if delta0 + next0 != 0:
        sign1 = sg(delta0 + next0) * sg(st.delta1 + st.next1)
        sign2 = sg(delta0 + next0) * sg(st.delta2 + st.next2)
        cd = s16(((sign1 * 3072 + c1n) >> 6) & ~0x3)
        cd = clamp16(clamp16(cd + 30719) - 30719)
        cd = clamp16(clamp16(cd - 30720) + 30720)
        cd = (s16(sign2 * 1024) - s16(sign1 * cd)) * 2
        c1n += sign1 * 3072
        c2n += cd
    st.hist2 = st.hist1; st.hist1 = sample
    st.coef2 = absmax16(s16(c2n >> 8), 768)
    st.coef1 = absmax16(s16(c1n >> 8), s16(960 - st.coef2))
    st.next2 = st.next1; st.next1 = next0; st.step1 = step0
    st.delta5 = st.delta4; st.delta4 = st.delta3; st.delta3 = st.delta2; st.delta2 = st.delta1; st.delta1 = delta0
    d1 = sg(st.delta1)
    st.mod4 = clamp16(st.mod4*255 + 2048*d1*sg(st.delta5)) >> 8
    st.mod3 = clamp16(st.mod3*255 + 2048*d1*sg(st.delta4)) >> 8
    st.mod2 = clamp16(st.mod2*255 + 2048*d1*sg(st.delta3)) >> 8
    st.mod1 = clamp16(st.mod1*255 + 2048*d1*sg(st.delta2)) >> 8
    return sample

def unpack_codes(data, n, bps=4):
    out, bits, acc, pos, mask = [], 0, 0, 0, (1 << bps) - 1
    for _ in range(n):
        if bits < bps:
            w = struct.unpack_from('<I', data, pos)[0] if pos + 4 <= len(data) else 0
            pos += 4; acc = (acc << 32) | w; bits += 32
        bits -= bps
        out.append((acc >> bits) & mask); acc &= (1 << bits) - 1
    return out

def pack_codes(codes, bps=4):
    if bps == 4: codes = list(codes) + [7] * (-len(codes) % 8)
    acc = 0
    for c in codes: acc = (acc << bps) | (c & ((1 << bps) - 1))
    nbits = len(codes) * bps
    pad = -nbits % 32
    acc <<= pad; nbits += pad
    return b''.join(struct.pack('<I', (acc >> (nbits - 32 * (i + 1))) & 0xFFFFFFFF) for i in range(nbits // 32))

class Header:
    def __init__(self, raw):
        (self.sig, self.sample_count, self.subframe_count, self.codes_last, self.codes_per_sub,
         self.subs_per_frame, self.u18, self.u1c, self.u20, self.bps, self.u28, self.channels) = struct.unpack('<12I', raw[:0x30])
    def pack(self):
        return struct.pack('<12I', self.sig, self.sample_count, self.subframe_count, self.codes_last, self.codes_per_sub,
                           self.subs_per_frame, self.u18, self.u1c, self.u20, self.bps, self.u28, self.channels)

def sub_size(codes, bps=4):
    return (bps * codes // 8 + 1) if codes else 0

def _key(st, bps):
    return (st.step1, st.hist1, st.delta1) if bps == 6 else st.key()

def frame_layout(h):
    """lista de (codes_a, codes_b) por frame"""
    frames, sf = [], 0
    while sf < h.subframe_count:
        if sf + 1 == h.subframe_count: a, b = h.codes_last, 0
        elif sf + 2 == h.subframe_count: a, b = h.codes_per_sub, h.codes_last
        else: a, b = h.codes_per_sub, h.codes_per_sub
        frames.append((a, b)); sf += 2
    return frames

def decode(data, check_states=False):
    """data = stream completo (header 0x30 + frames). devuelve (samples[int16], Header, mismatches)"""
    h = Header(data)
    assert h.sig == 8 and h.bps in (4, 6) and h.channels in (1, 2), (h.sig, h.bps, h.channels)
    if h.channels == 2: return _decode_stereo(data, h, check_states)
    expand = expand_code if h.bps == 4 else expand_code6
    pos, out, mism = SIZE_HDR, [], 0
    prev_state = None
    for a, b in frame_layout(h):
        st = State.unpack(data[pos:pos+SIZE_CH])
        if check_states and prev_state is not None and _key(prev_state, h.bps) != _key(st, h.bps): mism += 1
        sa, sb = sub_size(a, h.bps), sub_size(b, h.bps)
        p = pos + SIZE_CH
        for codes_n, size in ((a, sa), (b, sb)):
            if not codes_n: continue
            for c in unpack_codes(data[p:p+size], codes_n, h.bps): out.append(expand(c, st))
            p += size
        prev_state = st
        pos += SIZE_CH + sa + sb
    return out[:h.sample_count], h, mism

def _decode_stereo(data, h, check_states):
    """2 canales: dos estados por frame (0x34 cada uno), códigos alternados mid/side en grupos de 8; L=m+s, R=m-s"""
    expand = expand_code if h.bps == 4 else expand_code6
    bps, pos, out, mism, prev = h.bps, SIZE_HDR, [], 0, None
    for a, b in frame_layout(h):
        s0, s1 = State.unpack(data[pos:pos + SIZE_CH]), State.unpack(data[pos + SIZE_CH:pos + 2 * SIZE_CH])
        if check_states and prev is not None and (_key(prev[0], bps) != _key(s0, bps) or _key(prev[1], bps) != _key(s1, bps)): mism += 1
        sa, sb = sub_size(a, bps), sub_size(b, bps)
        p = pos + 2 * SIZE_CH
        for cn, size in ((a, sa), (b, sb)):
            if not cn: continue
            codes = unpack_codes(data[p:p + size], cn + (-cn % 8), bps)
            sub = []
            for i in range(0, len(codes), 8):
                m = [expand(codes[i + j], s0) for j in (0, 2, 4, 6)]
                sd = [expand(codes[i + j], s1) for j in (1, 3, 5, 7)]
                for k in range(4): sub.append(clamp16(m[k] + sd[k])); sub.append(clamp16(m[k] - sd[k]))
            out += sub[:cn]
            p += size
        prev = (s0, s1)
        pos += 2 * SIZE_CH + sa + sb
    return out[:h.sample_count], h, mism

# ---------------------------------------------------------------- encoder
def encode_sample(target, st):
    """elige el código 0..14 cuya salida se acerca más a target; actualiza st; devuelve (code, sample)"""
    next0, base = _pred(st)
    best, bc = None, 7
    for code in range(15):
        cs = code - 7
        s = s16(_delta0(cs, st.step1) + next0 + base)
        e = abs(s - target)
        if best is None or e < best: best, bc = e, code
    return bc, expand_code(bc, st)

def encode_sample6(target, st):
    best, bc = None, 31
    base = st.delta1 + st.hist1
    for code in range(1, 62):                    # |cs| <= 30: con |cs|=31 table2 vale 0 y el paso se atasca en 271
        e = abs(s16(_delta0_6(code - 31, st.step1) + base) - target)
        if best is None or e < best: best, bc = e, code
    return bc, expand_code6(bc, st)

def encode(samples, template_header=None, init_state=None, sample_rate=36000, u1c=1, init_states=None):
    """samples: int16 mono, o entrelazadas L,R si la plantilla es estéreo. Devuelve bytes de stream (header 0x30 + frames)."""
    if template_header is not None and template_header.channels == 2:
        return _encode_stereo(samples, template_header, init_states)
    n = len(samples)
    h = Header(template_header.pack()) if template_header else Header(struct.pack('<12I', 8, 0, 0, 0, 1536, 2, sample_rate, u1c, 0, 4, 1, 1))
    h.sample_count = n
    cps = h.codes_per_sub
    h.subframe_count = (n + cps - 1) // cps
    h.codes_last = n - (h.subframe_count - 1) * cps
    bps = h.bps
    pick = encode_sample if bps == 4 else encode_sample6
    st = init_state.copy() if init_state else State()
    if not init_state:
        st.coef1 = 0; st.coef2 = 0; st.step1 = 271
    out = bytearray(h.pack())
    pos = 0
    for a, b in frame_layout(h):
        out += st.pack()
        for cn in (a, b):
            if not cn: continue
            codes = []
            for i in range(cn):
                c, _ = pick(samples[pos + i], st)
                codes.append(c)
            pos += cn
            out += _pad_sub(codes, cn, bps)
    return bytes(out)

def _encode_stereo(samples, template_header, init_states=None):
    h = Header(template_header.pack())
    n = len(samples)                                   # muestras de todos los canales (L,R,L,R...)
    cps = h.codes_per_sub
    h.sample_count = n
    h.subframe_count = (n + cps - 1) // cps
    h.codes_last = n - (h.subframe_count - 1) * cps
    bps = h.bps
    pick = encode_sample if bps == 4 else encode_sample6
    if init_states: st0, st1 = init_states[0].copy(), init_states[1].copy()
    else:
        st0, st1 = State(), State()
        for st in (st0, st1): st.step1 = 271
    out = bytearray(h.pack()); pos = 0
    for a, b in frame_layout(h):
        out += st0.pack() + st1.pack()
        for cn in (a, b):
            if not cn: continue
            vals = list(samples[pos:pos + cn]); pos += cn
            vals += vals[-2:] * ((-len(vals) % 8) // 2) if len(vals) >= 2 else [0] * (-len(vals) % 8)
            codes = []
            for i in range(0, len(vals), 8):
                for k in range(4):
                    l, r = vals[i + 2 * k], vals[i + 2 * k + 1]
                    cm, _ = pick((l + r) >> 1, st0); cs, _ = pick((l - r) >> 1, st1)
                    codes += [cm, cs]
            out += _pad_sub(codes, cn, bps)
    return bytes(out)

def _pad_sub(codes, cn, bps=4):
    raw = pack_codes(codes, bps)
    need = sub_size(cn, bps)
    return (raw + b'\x00' * need)[:need] if len(raw) >= need else raw + b'\x00' * (need - len(raw))
