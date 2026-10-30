#!/usr/bin/env python3
"""Interfaz de sc1_tool (Splinter Cell 2002 - LS0/LM0).
Con Pillow (y sc1_bg_src.jpg junto al script) la ventana es redimensionable/maximizable: el fondo, el desenfoque y los
paneles de cristal se renderizan al tamaño actual. Sin Pillow queda a tamaño fijo con el fondo horneado sc1_bg.png.
Cambiar el fondo:  python sc1_gui.py --bake mi_imagen.jpg   (requiere Pillow; copia la imagen y regenera sc1_bg.png)"""
import os, sys, json, shutil, threading, traceback

W, H = 1100, 740                                  # tamaño base y mínimo
M = 28                                            # margen lateral de los paneles
HERE = os.path.dirname(os.path.abspath(__file__))
BG_PNG = os.path.join(HERE, 'sc1_bg.png')
SRC_NAMES = ('sc1_bg_src.jpg', 'sc1_bg_src.jpeg', 'sc1_bg_src.png')
CFG = os.path.join(HERE, 'sc1_gui.json')

# ---- paleta
ACC, ACC_HI, ACC_DIM = '#8dff5c', '#b4ff8f', '#4b9a3a'
TXT, MUTED, WARN, ERR = '#e6f1e8', '#93aa9a', '#ffcf5a', '#ff7b72'
FIELD, FIELD_BORDER, BTN_BG, BTN_HOVER = '#0a100d', '#23382b', '#0d1612', '#16301f'

# ---- filas y posiciones que no dependen del tamaño
ROWS = [('Mapa LM0 / SM0', 'lm0', 'file'), ('Carpeta .LS0 / .SS0', 'ls0', 'dir'), ('Carpeta WAV', 'wav', 'dir'), ('Salida (importar)', 'out', 'dir')]
ROW_Y0, ROW_DY = 214, 44
LABEL_X, ENTRY_X, ENTRY_H, BROWSE_W = 52, 214, 30, 104
CHECK_X, CHECK_Y = 52, (426, 456, 486)
MAP_PATTERNS = ' '.join(f'*.{p}{n}' for n in range(8) for p in ('LM', 'lm', 'SM', 'sm'))
C3_Y1 = 528                                       # el panel del registro crece hacia abajo


def layout(w, h):
    """geometría de todo en función del tamaño de la ventana (compartida por la GUI, el render y la vista previa)"""
    x2 = w - M
    c3 = (M, C3_Y1, x2, max(h - 24, C3_Y1 + 120))
    right = x2 - 24
    imp = (right - 212, 426, right, 492)
    ext = (imp[0] - 16 - 220, 426, imp[0] - 16, 492)
    return dict(cards=[(M, 150, x2, 392), (M, 404, x2, 516), c3],
                browse_x=right - BROWSE_W, entry_w=(right - BROWSE_W) - 14 - ENTRY_X,
                btn_extract=ext, btn_import=imp,
                bar=(52, C3_Y1 + 38, right, C3_Y1 + 42),
                log=(52, C3_Y1 + 52, right - 52, c3[3] - 14 - (C3_Y1 + 52)),
                dot=(w - 150, 46), status_x=w - 136)


def render(src, w, h, cards, blur=1.8, glass_blur=7, dim=0.78):
    """fondo a tamaño w×h: recorte 'cover' alineado arriba, desenfoque sutil, viñeta y paneles de cristal esmerilado"""
    from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
    R = getattr(Image, 'Resampling', Image)
    def soft(im, radius, down=2):                 # desenfoque a media resolución (más rápido, mismo aspecto)
        if min(im.size) > 200:
            sm = im.resize((max(1, im.width // down), max(1, im.height // down)), R.BILINEAR)
            return sm.filter(ImageFilter.GaussianBlur(radius / down)).resize(im.size, R.BILINEAR)
        return im.filter(ImageFilter.GaussianBlur(radius))
    s = max(w / src.width, h / src.height)
    im = src.resize((max(w, round(src.width * s)), max(h, round(src.height * s))), R.LANCZOS)
    x0 = (im.width - w) // 2
    im = im.crop((x0, 0, x0 + w, h)).filter(ImageFilter.GaussianBlur(blur))
    im = ImageEnhance.Brightness(im).enhance(dim)
    qw, qh = max(1, w // 4), max(1, h // 4)       # viñeta
    vig = Image.new('L', (qw, qh), 0)
    ImageDraw.Draw(vig).ellipse((-qw * .25, -qh * .35, qw * 1.25, qh * 1.30), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(30)).resize((w, h), R.BILINEAR).point(lambda v: 90 + v * 165 // 255)
    im = Image.composite(im, Image.new('RGB', (w, h), (3, 6, 5)), vig)
    hdr = Image.new('L', (620, 150), 0)           # degradado oscuro bajo el título
    for x in range(620): hdr.paste(150 - x * 150 // 620, (x, 0, x + 1, 150))
    im.paste(Image.new('RGB', (620, 150), (2, 5, 4)), (0, 0), hdr)
    frost = soft(im, glass_blur)                  # paneles
    mask, shadow = Image.new('L', (w, h), 0), Image.new('L', (w, h), 0)
    veil = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    dm, ds, dv = ImageDraw.Draw(mask), ImageDraw.Draw(shadow), ImageDraw.Draw(veil)
    for (x1, y1, x2, y2) in cards:
        ds.rounded_rectangle((x1, y1 + 6, x2, y2 + 6), 16, fill=150)
        dm.rounded_rectangle((x1, y1, x2, y2), 14, fill=255)
        dv.rounded_rectangle((x1, y1, x2, y2), 14, fill=(4, 10, 8, 172), outline=(125, 255, 110, 70), width=1)
        dv.line((x1 + 16, y1 + 1, x2 - 16, y1 + 1), fill=(190, 255, 170, 55))
    im.paste(Image.new('RGB', (w, h), (0, 0, 0)), (0, 0), soft(shadow, 10))
    im = Image.composite(frost, im, mask)
    return Image.alpha_composite(im.convert('RGBA'), veil).convert('RGB')


def bake(src, out=BG_PNG):
    """genera el PNG fijo (modo sin Pillow) y deja la imagen como fuente del modo redimensionable"""
    from PIL import Image
    ext = os.path.splitext(src)[1].lower() or '.jpg'
    dst = os.path.join(HERE, 'sc1_bg_src' + ext)
    if os.path.abspath(src) != os.path.abspath(dst):
        for n in SRC_NAMES:
            if os.path.isfile(os.path.join(HERE, n)): os.remove(os.path.join(HERE, n))
        shutil.copyfile(src, dst)
    render(Image.open(src).convert('RGB'), W, H, layout(W, H)['cards']).save(out, optimize=True)
    return out


def _find_src():
    return next((p for p in (os.path.join(HERE, n) for n in SRC_NAMES) if os.path.isfile(p)), None)


def _pick(tkfont, names, default):
    have = set(tkfont.families())
    return next((n for n in names if n in have), default)


def main():
    import tkinter as tk
    from tkinter import filedialog, font as tkfont
    sys.path.insert(0, HERE)
    import sc1_tool as T

    # ---- ¿modo dinámico? (Pillow + ImageTk + imagen fuente)
    src, ImageTk = None, None
    try:
        from PIL import Image, ImageTk as _it
        p = _find_src()
        if p: src, ImageTk = Image.open(p).convert('RGB'), _it
    except Exception: src = None

    cfg = {}
    try: cfg = json.load(open(CFG, encoding='utf-8'))
    except Exception: pass

    root = tk.Tk()
    root.title('Splinter Cell · Audio Tool')
    root.configure(bg='#070b09')
    sans = _pick(tkfont, ('Segoe UI', 'SF Pro Text', 'Helvetica Neue', 'Noto Sans', 'DejaVu Sans'), 'TkDefaultFont')
    mono = _pick(tkfont, ('Consolas', 'Cascadia Mono', 'Menlo', 'DejaVu Sans Mono'), 'TkFixedFont')
    F = dict(title=(sans, 27, 'bold'), sub=(sans, 10, 'bold'), head=(sans, 9, 'bold'), label=(sans, 10),
             btn=(sans, 11, 'bold'), small=(sans, 9), entry=(mono, 10), log=(mono, 9))
    c = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, bg='#070b09')
    c.pack(fill='both', expand=True)
    L = layout(W, H)
    size = [W, H]

    # ---- fondo
    bg_item = c.create_image(0, 0, anchor='nw')
    def paint_bg(w, h):
        ph = ImageTk.PhotoImage(render(src, w, h, layout(w, h)['cards']))
        c.itemconfigure(bg_item, image=ph); c._bg = ph
    dynamic = src is not None
    if dynamic:
        try: paint_bg(W, H)
        except Exception: dynamic = False; traceback.print_exc()
    if not dynamic:
        bg = None
        if os.path.isfile(BG_PNG):
            try: bg = tk.PhotoImage(file=BG_PNG)
            except tk.TclError: bg = None
        if bg: c.itemconfigure(bg_item, image=bg); c._bg = bg
        else:                                         # sin imagen: degradado + paneles sólidos
            for i in range(H):
                k = i / H; c.create_line(0, i, W, i, fill='#%02x%02x%02x' % (int(8 + 6 * k), int(16 + 8 * k), int(12 + 6 * k)))
            for (x1, y1, x2, y2) in L['cards']: _rr(c, x1, y1, x2, y2, 14, fill='#0a120e', outline=ACC_DIM)
        root.resizable(False, False)
    else:
        root.minsize(W, H)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        try: cw, ch = cfg.get('size', (W, H)); cw, ch = max(W, min(int(cw), sw)), max(H, min(int(ch), sh))
        except Exception: cw, ch = W, H
        root.geometry(f'{cw}x{ch}')
    c.tag_lower(bg_item)

    # ---- cabecera
    for dx, dy, col in ((2, 2, '#000000'), (0, 0, TXT)):
        c.create_text(38 + dx, 52 + dy, text='SPLINTER CELL', anchor='w', fill=col, font=F['title'])
    c.create_text(40, 92, text='AUDIO TOOL   ·   LM0 / LS0 · SM0 / SS0   ·   ADPCM · IMA · PCM16 · CAPAS', anchor='w', fill=ACC, font=F['sub'])
    dot = c.create_oval(0, 0, 10, 10, fill=ACC, outline='')
    status = c.create_text(0, 46, text='LISTO', anchor='w', fill=TXT, font=F['head'])
    c.create_text(52, 172, text='ARCHIVOS', anchor='w', fill=ACC, font=F['head'])
    c.create_text(52, 550, text='REGISTRO', anchor='w', fill=ACC, font=F['head'])

    vars_ = {k: tk.StringVar(value=cfg.get(k, '')) for _, k, _ in ROWS}

    def save_cfg(*_):
        try:
            data = {**{k: v.get() for k, v in vars_.items()}, 'keep_rate': chk_rate.value, 'layers': chk_layers.value, 'force': chk_force.value}
            if dynamic: data['size'] = list(size)
            json.dump(data, open(CFG, 'w', encoding='utf-8'), indent=1)
        except Exception: pass

    # ---- filas de rutas
    def browse(key, kind):
        cur = vars_[key].get()
        init = cur if os.path.isdir(cur) else (os.path.dirname(cur) if cur else '')
        p = (filedialog.askopenfilename(initialdir=init or None, filetypes=[('Mapas Ubisoft (LM/SM)', MAP_PATTERNS), ('Todos', '*.*')])
             if kind == 'file' else filedialog.askdirectory(initialdir=init or None))
        if p: vars_[key].set(p); save_cfg()

    entry_wins, browse_btns = [], []
    for i, (label, key, kind) in enumerate(ROWS):
        y = ROW_Y0 + i * ROW_DY
        c.create_text(LABEL_X, y, text=label, anchor='w', fill=MUTED, font=F['label'])
        e = tk.Entry(root, textvariable=vars_[key], bg=FIELD, fg=TXT, insertbackground=ACC, relief='flat', bd=0,
                     highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=ACC, font=F['entry'],
                     selectbackground=ACC_DIM, selectforeground='#ffffff')
        entry_wins.append(c.create_window(ENTRY_X, y - ENTRY_H // 2, anchor='nw', width=L['entry_w'], height=ENTRY_H, window=e))
        bx = L['browse_x']
        browse_btns.append(_Btn(c, bx, y - ENTRY_H // 2, bx + BROWSE_W, y + ENTRY_H // 2, 'Examinar',
                                lambda k=key, kd=kind: browse(k, kd), F['small']))

    chk_rate = _Check(c, CHECK_X, CHECK_Y[0], 'Usar el sample rate del WAV (hasta 48000 Hz)', cfg.get('keep_rate', True), F['label'], save_cfg)
    chk_layers = _Check(c, CHECK_X, CHECK_Y[1], 'Extraer también la música en capas (STREAM.SS0, ~2 GB de WAV)', cfg.get('layers', False), F['label'], save_cfg)
    chk_force = _Check(c, CHECK_X, CHECK_Y[2], 'Recodificar todo (modo prueba)', cfg.get('force', False), F['label'], save_cfg)

    # ---- registro
    logbox = tk.Text(root, bg=FIELD, fg='#b9d4c0', insertbackground=ACC, relief='flat', bd=0, highlightthickness=1,
                     highlightbackground=FIELD_BORDER, highlightcolor=FIELD_BORDER, font=F['log'], wrap='word',
                     state='disabled', padx=10, pady=8, cursor='arrow')
    for tag, col in (('warn', WARN), ('err', ERR), ('ok', ACC), ('dim', MUTED)): logbox.tag_configure(tag, foreground=col)
    lx, ly, lw, lh = L['log']
    log_win = c.create_window(lx, ly, anchor='nw', width=lw, height=lh, window=logbox)
    track = c.create_rectangle(*L['bar'], fill='#16231b', outline='')
    bar = c.create_rectangle(0, 0, 0, 0, fill=ACC, outline='', state='hidden')
    geom = {'bar': L['bar']}

    def log(msg):
        low = msg.lower()
        tag = ('err' if low.startswith('error') else 'warn' if ('aviso' in low or 'omitido' in low)
               else 'ok' if low.startswith('listo') else 'dim' if msg.startswith('  ') else None)
        logbox.configure(state='normal'); logbox.insert('end', msg + '\n', tag or ''); logbox.see('end'); logbox.configure(state='disabled')

    # ---- ejecución en segundo plano
    busy = [False]; t0 = [0]

    def tick():
        if not busy[0]: return
        t0[0] += 1; bx1, by1, bx2, by2 = geom['bar']; span = bx2 - bx1; seg = 150
        pos = (t0[0] * 14) % (span + seg)
        c.coords(bar, bx1 + max(0, pos - seg), by1, bx1 + min(span, pos), by2)
        root.after(30, tick)

    def set_busy(on):
        busy[0] = on
        c.itemconfigure(bar, state='normal' if on else 'hidden')
        c.itemconfigure(status, text='PROCESANDO…' if on else 'LISTO'); c.itemconfigure(dot, fill=WARN if on else ACC)
        for b in (btn_extract, btn_import): b.set_enabled(not on)
        if on: tick()

    def run(fn, need, *args, **kw):
        if busy[0]: return
        miss = [name for name, key in need if not vars_[key].get().strip()]
        if miss: log('ERROR: falta ' + ', '.join(miss)); return
        save_cfg(); set_busy(True)
        def work():
            try: fn(*args, log=lambda m: root.after(0, log, m), **kw)
            except Exception as ex:
                root.after(0, log, f'ERROR: {ex}'); traceback.print_exc()
            finally: root.after(0, set_busy, False)
        threading.Thread(target=work, daemon=True).start()

    g = lambda k: vars_[k].get().strip()
    btn_extract = _Btn(c, *L['btn_extract'], 'EXTRAER A WAV', lambda: run(T.extract, [('el mapa (LM0/SM0)', 'lm0'), ('carpeta de streams', 'ls0'), ('carpeta WAV', 'wav')],
                       g('lm0'), g('ls0'), g('wav'), layers=chk_layers.value), F['btn'])
    btn_import = _Btn(c, *L['btn_import'], 'IMPORTAR WAV', lambda: run(T.import_wavs, [('el mapa (LM0/SM0)', 'lm0'), ('carpeta de streams', 'ls0'), ('carpeta WAV', 'wav'), ('carpeta de salida', 'out')],
                       g('lm0'), g('ls0'), g('wav'), g('out'), force_all=chk_force.value, keep_rate=chk_rate.value), F['btn'], primary=True)

    # ---- redimensionado: los widgets se recolocan al instante; el fondo se renderiza al soltar (debounce)
    def relayout(w, h):
        Ln = layout(w, h)
        for i, item in enumerate(entry_wins):
            c.itemconfigure(item, width=Ln['entry_w'])
            y = ROW_Y0 + i * ROW_DY; bx = Ln['browse_x']
            browse_btns[i].place(bx, y - ENTRY_H // 2, bx + BROWSE_W, y + ENTRY_H // 2)
        btn_extract.place(*Ln['btn_extract']); btn_import.place(*Ln['btn_import'])
        x, y, bw, bh = Ln['log']
        c.coords(log_win, x, y); c.itemconfigure(log_win, width=bw, height=bh)
        c.coords(track, *Ln['bar']); geom['bar'] = Ln['bar']
        dx, dy = Ln['dot']; c.coords(dot, dx, dy - 5, dx + 10, dy + 5); c.coords(status, Ln['status_x'], 46)

    relayout(W, H)
    pending = [None]

    def on_configure(e):
        w, h = max(W, e.width), max(H, e.height)
        if [w, h] == size: return
        size[:] = [w, h]; relayout(w, h)
        if pending[0]: root.after_cancel(pending[0])
        pending[0] = root.after(110, lambda: paint_bg(*size))
    if dynamic: c.bind('<Configure>', on_configure)

    root.protocol('WM_DELETE_WINDOW', lambda: (save_cfg(), root.destroy()))
    log('  Elige el mapa (LM0 o SM0) y las carpetas. "Extraer" genera los WAV; "Importar" reconstruye los streams y el mapa en la carpeta de salida.')
    root.mainloop()


def _rr_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def _rr(c, x1, y1, x2, y2, r, **kw):
    return c.create_polygon(_rr_pts(x1, y1, x2, y2, r), smooth=True, **kw)


class _Btn:
    """botón dibujado en el canvas (transparente sobre el fondo) con hover/pulsado/deshabilitado"""
    def __init__(self, c, x1, y1, x2, y2, text, cmd, font, primary=False):
        self.c, self.cmd, self.primary, self.enabled, self.hover = c, cmd, primary, True, False
        tag = f'btn{id(self)}'
        self.shape = _rr(c, x1, y1, x2, y2, 9, width=1, tags=(tag,))
        self.label = c.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=text, font=font, tags=(tag,))
        c.tag_bind(tag, '<Enter>', lambda e: self._set(hover=True)); c.tag_bind(tag, '<Leave>', lambda e: self._set(hover=False))
        c.tag_bind(tag, '<ButtonRelease-1>', lambda e: self.enabled and self.cmd())
        self._paint()

    def place(self, x1, y1, x2, y2):
        self.c.coords(self.shape, *_rr_pts(x1, y1, x2, y2, 9)); self.c.coords(self.label, (x1 + x2) // 2, (y1 + y2) // 2)

    def _set(self, hover):
        self.hover = hover
        self.c.configure(cursor='hand2' if hover and self.enabled else '')
        self._paint()

    def _paint(self):
        if not self.enabled: fill, line, txt = BTN_BG, '#26332b', '#566a5d'
        elif self.primary: fill, line, txt = (ACC_HI if self.hover else ACC), ACC, '#07110a'
        else: fill, line, txt = (BTN_HOVER if self.hover else BTN_BG), (ACC if self.hover else ACC_DIM), ACC
        self.c.itemconfigure(self.shape, fill=fill, outline=line); self.c.itemconfigure(self.label, fill=txt)

    def set_enabled(self, on):
        self.enabled = on
        if not on: self.c.configure(cursor='')
        self._paint()


class _Check:
    def __init__(self, c, x, y, text, value, font, on_change):
        self.c, self.value, self.on_change = c, bool(value), on_change
        tag = f'chk{id(self)}'
        self.box = _rr(c, x, y - 10, x + 20, y + 10, 5, fill=FIELD, outline=ACC_DIM, width=1, tags=(tag,))
        self.mark = c.create_line(x + 5, y, x + 9, y + 5, x + 16, y - 5, fill=ACC, width=2.5, capstyle='round', joinstyle='round', tags=(tag,))
        c.create_text(x + 32, y, text=text, anchor='w', fill=TXT, font=font, tags=(tag,))
        c.tag_bind(tag, '<ButtonRelease-1>', lambda e: self.toggle())
        c.tag_bind(tag, '<Enter>', lambda e: c.configure(cursor='hand2')); c.tag_bind(tag, '<Leave>', lambda e: c.configure(cursor=''))
        self._paint()

    def toggle(self): self.value = not self.value; self._paint(); self.on_change()

    def _paint(self):
        self.c.itemconfigure(self.mark, state='normal' if self.value else 'hidden')
        self.c.itemconfigure(self.box, outline=ACC if self.value else ACC_DIM)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--bake': print('fondo generado:', bake(sys.argv[2]))
    else: main()
