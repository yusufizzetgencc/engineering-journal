#!/usr/bin/env python3
"""Demo icin ornek urun cekimleri ve muzik uretir.

Gercek bir reklamda bunlarin yerine kendi telefon/kamera cekimlerini ve
lisansli bir muzigi koyarsin. Bu script sadece pipeline'i uctan uca
gosterebilmek icin "LUMA Vitamin C Serum" diye hayali bir urunun
yatay (1920x1080) ve dikey (1080x1920) kliplerini render eder.

Kullanim:  python3 demo_malzeme.py <cikti_klasoru>
"""
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS = 30
FONT_DIR = Path("/usr/share/fonts/opentype/inter")


def font(name, size):
    return ImageFont.truetype(str(FONT_DIR / name), size)


# ---------------------------------------------------------------- sprites

def bottle_sprite(height=900):
    """Amber cam damlalikli serum sisesi, seffaf arka planli RGBA."""
    s = 4  # supersampling
    w, h = int(height * 0.42) * s, height * s
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    body_top = int(h * 0.36)
    neck_w = int(w * 0.42)
    # Damlalik lastigi
    d.rounded_rectangle(((w - neck_w * 0.8) / 2, 0, (w + neck_w * 0.8) / 2, h * 0.24),
                        radius=int(neck_w * 0.4), fill=(22, 20, 22, 255))
    # Halka / kapak
    d.rounded_rectangle(((w - neck_w) / 2, h * 0.2, (w + neck_w) / 2, body_top + 10 * s),
                        radius=6 * s, fill=(196, 160, 98, 255))
    for i in range(6):
        y = h * 0.22 + i * (body_top - h * 0.22) / 6
        d.line(((w - neck_w) / 2, y, (w + neck_w) / 2, y), fill=(160, 124, 70, 255), width=2 * s)

    # Govde: yatay gradyanli amber cam
    body = Image.new("RGBA", (w, h - body_top), (0, 0, 0, 0))
    grad = np.zeros((h - body_top, w, 4), dtype=np.uint8)
    xs = np.linspace(0, 1, w)
    shade = 0.55 + 0.45 * np.sin(xs * math.pi) ** 0.6
    grad[..., 0] = (190 * shade).astype(np.uint8)
    grad[..., 1] = (92 * shade).astype(np.uint8)
    grad[..., 2] = (28 * shade).astype(np.uint8)
    grad[..., 3] = 255
    body = Image.fromarray(grad, "RGBA")
    mask = Image.new("L", body.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - body_top - 1), radius=int(w * 0.16), fill=255)
    img.paste(body, (0, body_top), mask)

    # Cam yansimasi
    hl = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hl).rounded_rectangle((w * 0.12, body_top + h * 0.04, w * 0.2, h * 0.93), radius=w * 0.04, fill=120)
    hl = hl.filter(ImageFilter.GaussianBlur(6 * s))
    img = Image.composite(Image.new("RGBA", (w, h), (255, 240, 220, 255)), img,
                          Image.fromarray((np.array(hl) * (np.array(img.split()[3]) > 0)).astype(np.uint8)))

    # Etiket
    d = ImageDraw.Draw(img)
    lt, lb = body_top + (h - body_top) * 0.28, body_top + (h - body_top) * 0.78
    d.rectangle((w * 0.06, lt, w * 0.94, lb), fill=(248, 244, 236, 255))
    d.rectangle((w * 0.06, lt + 10 * s, w * 0.94, lt + 14 * s), fill=(214, 168, 76, 255))
    f1 = font("InterDisplay-Black.otf", int(w * 0.2))
    f2 = font("InterDisplay-SemiBold.otf", int(w * 0.075))
    for text, f, y in (("LUMA", f1, lt + (lb - lt) * 0.18), ("VITAMIN C", f2, lt + (lb - lt) * 0.58),
                       ("SERUM · 30 ml", f2, lt + (lb - lt) * 0.74)):
        tw = d.textlength(text, font=f)
        d.text(((w - tw) / 2, y), text, font=f, fill=(34, 28, 24, 255))
    return img.resize((w // s, h // s), Image.LANCZOS)


def citrus_sprite(r=120):
    s = 4
    R = r * s
    img = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, 2 * R, 2 * R), fill=(255, 150, 30, 255))
    d.ellipse((R * 0.1, R * 0.1, R * 1.9, R * 1.9), fill=(255, 236, 190, 255))
    d.ellipse((R * 0.18, R * 0.18, R * 1.82, R * 1.82), fill=(255, 170, 40, 255))
    for k in range(10):
        a = k * 2 * math.pi / 10
        d.line((R, R, R + math.cos(a) * R * 0.8, R + math.sin(a) * R * 0.8), fill=(255, 225, 160, 255), width=3 * s)
    d.ellipse((R * 0.85, R * 0.85, R * 1.15, R * 1.15), fill=(255, 225, 160, 255))
    return img.resize((2 * r, 2 * r), Image.LANCZOS)


# ---------------------------------------------------------------- helpers

def gradient_bg(w, h, top, bottom, radial=None):
    t = np.linspace(0, 1, h)[:, None, None]
    arr = np.array(top, float) * (1 - t) + np.array(bottom, float) * t
    arr = np.repeat(arr, w, axis=1)
    if radial:
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy, rad, col, k = radial
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad
        a = np.clip(1 - dist, 0, 1)[..., None] ** 1.5 * k
        arr = arr * (1 - a) + np.array(col, float) * a
    return np.clip(arr, 0, 255).astype(np.uint8)


def paste(frame, sprite, cx, cy, scale=1.0, angle=0.0):
    sp = sprite
    if scale != 1.0:
        sp = sp.resize((max(1, int(sp.width * scale)), max(1, int(sp.height * scale))), Image.BICUBIC)
    if angle:
        sp = sp.rotate(angle, resample=Image.BICUBIC, expand=True)
    frame.alpha_composite(sp, (int(cx - sp.width / 2), int(cy - sp.height / 2)))


def shadow(frame, cx, cy, w, h, alpha=110):
    sh = Image.new("L", frame.size, 0)
    ImageDraw.Draw(sh).ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fill=alpha)
    sh = sh.filter(ImageFilter.GaussianBlur(h * 0.5))
    frame.alpha_composite(Image.merge("RGBA", (*[Image.new("L", frame.size, 0)] * 3, sh)))


def light_sweep(frame, sprite_box, sprite_alpha, pos):
    """Sisenin uzerinden gecen parlak isik bandi (urun reklamlarinin klasigi)."""
    x0, y0 = sprite_box
    w, h = sprite_alpha.size
    xx = np.arange(w)[None, :] + np.arange(h)[:, None] * 0.35
    band = np.exp(-((xx - pos * (w + h * 0.35)) / (w * 0.08)) ** 2) * 170
    a = (band * (np.array(sprite_alpha) / 255.0)).astype(np.uint8)
    layer = Image.new("RGBA", (w, h), (255, 248, 235, 0))
    layer.putalpha(Image.fromarray(a))
    frame.alpha_composite(layer, (int(x0), int(y0)))


class Writer:
    def __init__(self, path, w, h):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
             "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)

    def add(self, img):
        self.p.stdin.write(img.convert("RGB").tobytes())

    def close(self):
        self.p.stdin.close()
        self.p.wait()


# ---------------------------------------------------------------- shots

def shot_hero(out, bottle, seconds=4):
    W, H = 1920, 1080
    bg = Image.fromarray(gradient_bg(W, H, (24, 18, 16), (8, 6, 6), (W / 2, H * 0.45, 700, (120, 70, 30), 0.8)))
    sp = bottle.resize((int(bottle.width * 0.85), int(bottle.height * 0.85)), Image.LANCZOS)
    wr = Writer(out, W, H)
    n = seconds * FPS
    for i in range(n):
        t = i / n
        f = bg.copy().convert("RGBA")
        cy = H * 0.52 + math.sin(t * math.pi * 2) * 10
        shadow(f, W / 2, H * 0.93, sp.width * 1.1, 40)
        x0, y0 = W / 2 - sp.width / 2, cy - sp.height / 2
        f.alpha_composite(sp, (int(x0), int(y0)))
        light_sweep(f, (x0, y0), sp.split()[3], -0.2 + t * 1.4)
        wr.add(f)
    wr.close()


def shot_label(out, bottle, seconds=4):
    W, H = 1920, 1080
    bg = Image.fromarray(gradient_bg(W, H, (246, 232, 214), (226, 200, 170)))
    big = bottle.resize((int(bottle.width * 2.3), int(bottle.height * 2.3)), Image.LANCZOS)
    wr = Writer(out, W, H)
    n = seconds * FPS
    for i in range(n):
        t = i / n
        e = t * t * (3 - 2 * t)
        f = bg.copy().convert("RGBA")
        cy = H * 0.15 + e * 120
        x0, y0 = W / 2 - big.width / 2 + (1 - e) * 60, cy - big.height / 2 + 300
        f.alpha_composite(big, (int(x0), int(y0)))
        light_sweep(f, (x0, y0), big.split()[3], t * 1.2)
        wr.add(f)
    wr.close()


def shot_drop(out, seconds=4):
    """Dikey cekim: damla dusuyor, yuzeyde halka halka yayiliyor."""
    W, H = 1080, 1920
    bg = Image.fromarray(gradient_bg(W, H, (250, 226, 196), (236, 186, 140), (W / 2, H * 0.7, 900, (255, 244, 225), 0.7)))
    wr = Writer(out, W, H)
    n = seconds * FPS
    surface = H * 0.72
    for i in range(n):
        t = i / FPS
        f = bg.copy().convert("RGBA")
        d = ImageDraw.Draw(f)
        # Damlalik ucu
        d.rounded_rectangle((W / 2 - 22, -40, W / 2 + 22, H * 0.2), radius=20, fill=(30, 26, 26, 255))
        d.polygon(((W / 2 - 22, H * 0.2), (W / 2 + 22, H * 0.2), (W / 2 + 6, H * 0.24), (W / 2 - 6, H * 0.24)),
                  fill=(210, 190, 160, 230))
        for k, t0 in enumerate((0.2, 1.6, 3.0)):
            dt = t - t0
            if 0 <= dt:
                y = H * 0.25 + 0.5 * 2600 * dt * dt
                if y < surface:
                    r = 20
                    d.ellipse((W / 2 - r, y - r * 1.4, W / 2 + r, y + r), fill=(214, 128, 40, 235))
                    d.ellipse((W / 2 - 7, y - 10, W / 2 + 2, y - 1), fill=(255, 230, 190, 255))
                else:
                    t_hit = math.sqrt((surface - H * 0.25) / 1300)
                    age = dt - t_hit
                    for j in range(3):
                        a = age - j * 0.12
                        if 0 < a < 1.2:
                            rx, ry = 40 + a * 520, 10 + a * 110
                            alpha = int(200 * (1 - a / 1.2))
                            d.ellipse((W / 2 - rx, surface - ry, W / 2 + rx, surface + ry),
                                      outline=(200, 120, 50, alpha), width=5)
        wr.add(f)
    wr.close()


def shot_citrus(out, bottle, seconds=5):
    W, H = 1920, 1080
    bg = Image.fromarray(gradient_bg(W, H, (255, 214, 120), (250, 150, 60), (W / 2, H / 2, 900, (255, 240, 200), 0.9)))
    sp = bottle.resize((int(bottle.width * 0.8), int(bottle.height * 0.8)), Image.LANCZOS)
    citrus = citrus_sprite(110)
    wr = Writer(out, W, H)
    n = seconds * FPS
    for i in range(n):
        t = i / FPS
        f = bg.copy().convert("RGBA")
        orbit = []
        for k in range(5):
            a = t * 0.9 + k * 2 * math.pi / 5
            depth = math.sin(a)
            orbit.append((depth, W / 2 + math.cos(a) * 480, H * 0.5 + depth * 90 - 40, 0.55 + 0.35 * (depth + 1) / 2,
                          t * 40 + k * 50))
        for o in sorted(orbit):
            if o[0] < 0:
                paste(f, citrus, o[1], o[2], o[3], o[4])
        shadow(f, W / 2, H * 0.94, sp.width, 36)
        paste(f, sp, W / 2, H * 0.53)
        for o in sorted(orbit):
            if o[0] >= 0:
                paste(f, citrus, o[1], o[2], o[3], o[4])
        wr.add(f)
    wr.close()


def shot_podium(out, bottle, seconds=4):
    W, H = 1920, 1080
    wr = Writer(out, W, H)
    sp = bottle.resize((int(bottle.width * 0.62), int(bottle.height * 0.62)), Image.LANCZOS)
    n = seconds * FPS
    base = gradient_bg(W, H, (238, 220, 232), (214, 190, 210))
    for i in range(n):
        t = i / n
        f = Image.fromarray(base).convert("RGBA")
        d = ImageDraw.Draw(f)
        # Arkadaki yumusak daire ve podyum
        d.ellipse((W / 2 - 380, H * 0.05, W / 2 + 380, H * 0.05 + 760), fill=(248, 236, 244, 255))
        d.rectangle((W / 2 - 300, H * 0.78, W / 2 + 300, H), fill=(246, 232, 240, 255))
        d.ellipse((W / 2 - 300, H * 0.78 - 50, W / 2 + 300, H * 0.78 + 50), fill=(255, 246, 250, 255))
        shadow(f, W / 2, H * 0.78, sp.width * 1.2, 30, 90)
        cy = H * 0.78 - sp.height / 2 + 8 - (1 - t) * 30
        x0, y0 = W / 2 - sp.width / 2, cy - sp.height / 2
        f.alpha_composite(sp, (int(x0), int(y0)))
        light_sweep(f, (x0, y0), sp.split()[3], 1.1 - t * 1.3)
        wr.add(f)
    wr.close()


# ---------------------------------------------------------------- music

def music(out, bpm=120, bars=10, sr=44100):
    """Basit 4/4 house beat: kick, clap, hi-hat, sub bas, pad."""
    beat = 60 / bpm
    total = bars * 4 * beat
    n = int(total * sr)
    t = np.arange(n) / sr
    mix = np.zeros(n)

    def add(sig, at):
        s = int(at * sr)
        e = min(n, s + len(sig))
        mix[s:e] += sig[:e - s]

    kt = np.arange(int(0.35 * sr)) / sr
    kick = np.sin(2 * np.pi * (45 * kt + 90 * (1 - np.exp(-kt * 30)) / 30)) * np.exp(-kt * 9) * 0.9
    ct = np.arange(int(0.2 * sr)) / sr
    rng = np.random.default_rng(1)
    clap = rng.standard_normal(len(ct)) * np.exp(-ct * 25) * 0.35
    ht = np.arange(int(0.06 * sr)) / sr
    hat = np.diff(rng.standard_normal(len(ht) + 1)) * np.exp(-ht * 70) * 0.12

    roots = [55.0, 55.0, 43.65, 49.0]  # A, A, F, G
    for b in range(bars * 4):
        at = b * beat
        add(kick, at)
        if b % 2 == 1:
            add(clap, at)
        add(hat, at + beat / 2)
        root = roots[(b // 4) % 4]
        bt = np.arange(int(beat * 0.9 * sr)) / sr
        add(np.sin(2 * np.pi * root * bt) * np.minimum(1, bt * 40) * np.exp(-bt * 2) * 0.35, at + beat / 2)
    # Pad
    for bar in range(bars):
        root = roots[bar % 4] * 4
        seg = np.arange(int(4 * beat * sr)) / sr
        env = np.minimum(1, seg * 4) * np.minimum(1, (4 * beat - seg) * 4)
        chord = sum(np.sin(2 * np.pi * root * r * seg) for r in (1, 1.26, 1.5, 2))
        add(chord * env * 0.03, bar * 4 * beat)
    # Kick altinda sidechain hissi
    duck = 1 - 0.5 * np.exp(-((t % beat) * 12))
    mix = mix * duck
    mix = np.tanh(mix * 1.2) * 0.8
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "malzeme")
    out.mkdir(parents=True, exist_ok=True)
    bottle = bottle_sprite()
    bottle.save(out / "urun.png")
    shot_hero(out / "01_hero_yatay.mp4", bottle)
    shot_label(out / "02_etiket_yakin.mp4", bottle)
    shot_drop(out / "03_damla_dikey.mp4")
    shot_citrus(out / "04_c_vitamini.mp4", bottle)
    shot_podium(out / "05_podyum.mp4", bottle)
    music(out / "muzik_120bpm.wav")
    print(f"Ornek malzeme hazir: {out}")


if __name__ == "__main__":
    main()
