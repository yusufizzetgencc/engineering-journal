#!/usr/bin/env python3
"""Dikey (9:16) urun tanitim reklami montajlayici.

Bir JSON senaryo dosyasindaki klipleri alir ve Instagram Reels / TikTok
reklamlarinda kullanilan kurguyu otomatik uygular:

  * 1080x1920, 30 fps, H.264 + AAC, -14 LUFS ses (Instagram'in istedigi format)
  * Yatay klipleri dikeye cevirme: akilli kirpma (focus_x) ya da bulanik arka plan
  * Muzigin BPM'ine gore kesme (her klip N vurus surer, kesmeler vurusa oturur)
  * Her klipte hafif "punch-in" zoom, hiz degisimi (slow-mo / hizli)
  * Kisa gecisler (flash, whip, zoom, slide...) + otomatik whoosh efekti
  * Animasyonlu yazilar (kelime kelime beliren hook, vurgulu kelimeler, rozet)
    hep Reels arayuzunun kapatmadigi guvenli alanda
  * Renk duzeltme (kontrast, doygunluk, vinyet, keskinlik, istege bagli LUT)
  * Kapanis karti: urun adi, fiyat/teklif, CTA butonu, logo

Kullanim:
    python3 reklam.py senaryo.json            # reklami render et
    python3 reklam.py senaryo.json --onizleme # + guvenli alan onizlemesi ve kare ozeti
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

W, H = 1080, 1920

# Reels arayuzu: ustte kullanici adi/ikonlar, altta aciklama + CTA butonu.
# Yazilar bu bantlarin disinda kalmali.
SAFE_TOP = int(H * 0.14)
SAFE_BOTTOM = int(H * 0.35)

TRANSITIONS = {
    # ad: (xfade tipi, sure)
    "flash": ("fadewhite", 0.20),
    "whip": ("slideleft", 0.18),
    "whip_up": ("slideup", 0.18),
    "zoom": ("zoomin", 0.25),
    "fade": ("fade", 0.30),
    "smooth": ("smoothleft", 0.25),
    "circle": ("circleopen", 0.30),
    "dissolve": ("dissolve", 0.30),
}


def run(cmd):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        sys.stderr.write(" ".join(map(str, cmd)) + "\n" + res.stderr[-4000:])
        raise SystemExit(f"ffmpeg hata verdi (kod {res.returncode})")
    return res.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
               "-of", "json", str(path)])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    return {
        "duration": float(data["format"].get("duration", 0) or 0),
        "width": video and video["width"],
        "height": video and video["height"],
    }


def ass_color(hex_color, alpha=0):
    """#RRGGBB -> &HAABBGGRR (ASS formati)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc_filter_path(p):
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# ---------------------------------------------------------------- senaryo

class Ad:
    def __init__(self, cfg_path):
        self.cfg_path = Path(cfg_path).resolve()
        self.base = self.cfg_path.parent
        cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.cfg = cfg
        self.fps = cfg.get("fps", 30)
        self.brand = {"name": "", "primary": "#FFD166", "text": "#FFFFFF", "dark": "#111111",
                      "font": "Inter Display Black", "font_body": "Inter Display SemiBold", **cfg.get("brand", {})}
        self.music = cfg.get("music") or {}
        self.bpm = self.music.get("bpm")
        self.grade = {"contrast": 1.06, "saturation": 1.12, "brightness": 0.0, "gamma": 1.0,
                      "vignette": True, "sharpen": True, "lut": None, **cfg.get("grade", {})}
        self.fill = cfg.get("fill", "crop")
        self.zoom = cfg.get("zoom_amount", 0.08)
        self.clips = cfg["clips"]
        self.end = cfg.get("end_card")
        self.output = self.path(cfg.get("output", "cikti/reklam.mp4"))

    def path(self, p):
        p = Path(p)
        return p if p.is_absolute() else (self.base / p)

    def seconds(self, item, default_beats=4):
        if "duration" in item:
            return float(item["duration"])
        beats = item.get("beats", default_beats)
        if not self.bpm:
            return beats * 0.5
        return beats * 60.0 / self.bpm

    # ------------------------------------------------------------ zaman cizelgesi

    def timeline(self):
        """Her segmentin ekranda basladigi an, nominal suresi ve render suresi.

        Gecis bir sonraki segmentin baslangic vurusunda baslar; gecis suresi
        kadar fazlasi onceki segmente eklenir. Boylece toplam sure = nominal
        surelerin toplami olur ve kesmeler vurusta kalir.
        """
        segs = []
        items = [dict(c, kind="clip") for c in self.clips]
        if self.end:
            items.append(dict(self.end, kind="end"))
        t = 0.0
        for i, item in enumerate(items):
            L = self.seconds(item, 6 if item["kind"] == "end" else 4)
            nxt = items[i + 1] if i + 1 < len(items) else None
            tr = (nxt or {}).get("transition", "cut") if nxt else "cut"
            if tr not in TRANSITIONS and tr != "cut":
                raise SystemExit(f"Bilinmeyen gecis: {tr}. Secenekler: cut, {', '.join(TRANSITIONS)}")
            d_out = TRANSITIONS[tr][1] if tr != "cut" else 0.0
            segs.append({"item": item, "start": t, "len": L, "render": L + d_out, "out": tr})
            t += L
        return segs, t

    # ------------------------------------------------------------ video segmentleri

    def grade_chain(self):
        g = self.grade
        chain = [f"eq=contrast={g['contrast']}:saturation={g['saturation']}:brightness={g['brightness']}"
                 f":gamma={g['gamma']}"]
        if g.get("lut"):
            chain.append(f"lut3d=file='{esc_filter_path(self.path(g['lut']))}'")
        if g.get("sharpen"):
            chain.append("unsharp=5:5:0.55:5:5:0.0")
        if g.get("vignette"):
            chain.append("vignette=angle=PI/5")
        return chain

    def to_vertical(self, info, item):
        """Kaynagi 1080x1920'ye oturt."""
        fill = item.get("fill", self.fill)
        portrait = info["height"] >= info["width"] * 1.5
        if fill == "blur" and not portrait:
            return ("split[a][b];"
                    f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    "gblur=sigma=40,eq=brightness=-0.08:saturation=1.1[bg];"
                    f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2")
        fx = float(item.get("focus_x", 0.5))
        fy = float(item.get("focus_y", 0.5))
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H}:x='(iw-{W})*{fx}':y='(ih-{H})*{fy}'")

    def zoom_chain(self, item, dur):
        mode = item.get("zoom", "in")
        z = float(item.get("zoom_amount", self.zoom))
        if mode == "none" or z <= 0:
            return []
        prog = f"(t/{dur:.4f})"
        # Yumusak (ease-out) zoom
        ease = f"(1-pow(1-min({prog},1),2))"
        factor = f"(1+{z}*{ease})" if mode == "in" else f"(1+{z}*(1-{ease}))"
        # Tek sayili boyutlara izin vermek icin 4:4:4'te olcekle -> daha akici zoom
        return ["format=yuv444p",
                f"scale=w='ceil({W}*{factor})':h='ceil({H}*{factor})':eval=frame:flags=bicubic",
                f"crop={W}:{H}"]

    def render_clip(self, seg, idx, tmp):
        item = seg["item"]
        src = self.path(item["file"])
        if not src.exists():
            raise SystemExit(f"Klip bulunamadi: {src}")
        info = probe(src)
        speed = float(item.get("speed", 1.0))
        start = float(item.get("start", 0.0))
        dur = seg["render"]
        need = dur * speed
        if info["duration"] and start + need > info["duration"] + 0.05:
            print(f"  ! {src.name}: {need:.2f}s gerekli, {info['duration'] - start:.2f}s var; "
                  "son kare uzatilacak (daha uzun bir parca secmen iyi olur)")
        chain = [f"setpts=(PTS-STARTPTS)/{speed}", f"fps={self.fps}", self.to_vertical(info, item)]
        chain += self.zoom_chain(item, dur)
        chain += self.grade_chain()
        chain += [f"tpad=stop_mode=clone:stop_duration={dur}", f"trim=duration={dur:.4f}",
                  "setpts=PTS-STARTPTS", "format=yuv420p", "setsar=1"]
        out = tmp / f"seg{idx:02d}.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start}", "-t", f"{need + 0.5:.3f}", "-i", str(src),
             "-filter_complex", f"[0:v]{','.join(chain)}[v]", "-map", "[v]", "-an",
             "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-r", str(self.fps), str(out)])
        return out

    def render_end(self, seg, idx, tmp):
        item = seg["item"]
        dur = seg["render"]
        bg = item.get("background", "blur_last")
        out = tmp / f"seg{idx:02d}.mp4"
        if bg == "blur_last":
            # Son klibin bulanik + karartilmis hali -> sik bir kapanis zemini
            last = self.clips[-1]
            src = self.path(last["file"])
            info = probe(src)
            chain = [f"fps={self.fps}", self.to_vertical(info, dict(last, fill="crop")),
                     "gblur=sigma=30", "eq=brightness=-0.22:saturation=1.2", "vignette=angle=PI/4"]
            chain += self.zoom_chain({"zoom": "out", "zoom_amount": 0.06}, dur)
            chain += [f"tpad=stop_mode=clone:stop_duration={dur}", f"trim=duration={dur:.4f}",
                      "setpts=PTS-STARTPTS", "format=yuv420p", "setsar=1"]
            run(["ffmpeg", "-y", "-v", "error", "-ss", str(last.get("start", 0)), "-i", str(src),
                 "-filter_complex", f"[0:v]{','.join(chain)}[v]", "-map", "[v]", "-an",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-r", str(self.fps), str(out)])
        else:
            color = bg if bg.startswith("#") else self.brand["dark"]
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", f"color=c={color}:s={W}x{H}:r={self.fps}:d={dur:.4f}",
                 "-vf", "vignette=angle=PI/4,format=yuv420p,setsar=1",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "12", str(out)])
        return out

    # ------------------------------------------------------------ yazilar (ASS)

    def ass_header(self):
        b = self.brand
        white, dark, prim = ass_color(b["text"]), ass_color(b["dark"]), ass_color(b["primary"])
        shadow = "&H64000000"
        fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
               "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
               "Alignment, MarginL, MarginR, MarginV, Encoding")

        def style(name, font, size, prim_c, outline_c, back_c, border_style, outline, shadow_d, align=5, spacing=0):
            return (f"Style: {name},{font},{size},{prim_c},{prim_c},{outline_c},{back_c},0,0,0,0,100,100,"
                    f"{spacing},0,{border_style},{outline},{shadow_d},{align},80,80,0,1")

        styles = [
            style("Hook", b["font"], 104, white, "&H00000000", shadow, 1, 5, 4),
            style("Caption", b["font"], 78, white, "&H00000000", shadow, 1, 4, 3),
            style("Badge", b["font"], 50, dark, prim, prim, 3, 18, 0, spacing=2),
            style("Small", b["font_body"], 46, white, "&H00000000", shadow, 1, 2, 2),
            style("Title", b["font"], 150, white, "&H00000000", shadow, 1, 0, 3, spacing=6),
            style("Sub", b["font_body"], 56, white, "&H00000000", shadow, 1, 0, 2),
            style("Price", b["font"], 96, prim, "&H00000000", shadow, 1, 0, 3),
            style("Old", b["font_body"], 56, "&H40FFFFFF", "&H00000000", shadow, 1, 0, 0),
            style("CTA", b["font"], 60, dark, prim, prim, 3, 30, 0, spacing=1),
        ]
        return ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
                f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n{fmt}\n" + "\n".join(styles) +
                "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def rich(self, text):
        """*kelime* -> marka renginde vurgu. \\n -> yeni satir."""
        prim = ass_color(self.brand["primary"])
        white = ass_color(self.brand["text"])
        text = text.replace("{", "(").replace("}", ")")
        text = re.sub(r"\*(.+?)\*", lambda m: f"{{\\c{prim}}}{m.group(1)}{{\\c{white}}}", text)
        return text.replace("\n", "\\N")

    def word_pop(self, text, span_ms):
        """Kelimeler sirayla 'pop' ederek belirir (CapCut tarzi hook)."""
        prim = ass_color(self.brand["primary"])
        white = ass_color(self.brand["text"])
        lines = text.split("\n")
        words = [w for line in lines for w in line.split()]
        step = span_ms / max(1, len(words))
        out, k = [], 0
        for li, line in enumerate(lines):
            parts = []
            for w in line.split():
                t0 = int(k * step)
                color = white
                if w.startswith("*") or w.endswith("*"):
                    color = prim
                w = w.strip("*").replace("{", "(").replace("}", ")")
                parts.append(f"{{\\c{color}\\alpha&HFF&\\fscx70\\fscy70"
                             f"\\t({t0},{t0 + 1},\\alpha&H00&)"
                             f"\\t({t0},{t0 + 140},\\fscx112\\fscy112)"
                             f"\\t({t0 + 140},{t0 + 230},\\fscx100\\fscy100)}}{w}")
                k += 1
            out.append(" ".join(parts))
        return "\\N".join(out)

    def events(self, segs):
        ev = []

        def add(start, end, style, text, layer=1):
            ev.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

        cx = W // 2
        for i, seg in enumerate(segs):
            item = seg["item"]
            s, e = seg["start"], seg["start"] + seg["len"]
            if item["kind"] == "end":
                self.end_card_events(add, s, e)
                continue
            text = item.get("text")
            if text:
                style = item.get("text_style", "Hook" if i == 0 else "Caption")
                y = item.get("text_y", 0.30 if style == "Hook" else 0.58)
                y = min(max(int(H * y), SAFE_TOP + 120), H - SAFE_BOTTOM - 80)
                pos = f"\\an5\\pos({cx},{y})"
                if item.get("animate", "words" if style == "Hook" else "pop") == "words":
                    body = f"{{{pos}\\fad(0,120)}}" + self.word_pop(text, min(900, seg["len"] * 400))
                else:
                    body = (f"{{{pos}\\fad(60,120)\\fscx80\\fscy80\\t(0,120,\\fscx106\\fscy106)"
                            f"\\t(120,220,\\fscx100\\fscy100)}}" + self.rich(text))
                add(s + 0.03, e - 0.02, style, body)
            badge = item.get("badge")
            if badge:
                by = int(H * item.get("badge_y", 0.45 if text and item.get("text_style") != "Hook" else 0.42))
                if text and i == 0:
                    by = int(H * 0.43)
                by = min(max(by, SAFE_TOP + 80), H - SAFE_BOTTOM - 60)
                add(s + 0.25, e - 0.02, "Badge",
                    f"{{\\an5\\pos({cx},{by})\\fad(80,120)\\frz-4\\fscx0\\fscy0"
                    f"\\t(0,160,\\fscx112\\fscy112)\\t(160,260,\\fscx100\\fscy100)}}{badge.upper()}", layer=2)
            small = item.get("subtext")
            if small:
                sy = int(H * item.get("subtext_y", 0.64))
                sy = min(sy, H - SAFE_BOTTOM - 40)
                add(s + 0.2, e - 0.02, "Small", f"{{\\an5\\pos({cx},{sy})\\fad(150,120)}}{self.rich(small)}")
        return ev

    def end_layout(self):
        """Kapanis karti yerlesimi (oran olarak). Urun gorseli varsa yazilar asagi kayar."""
        if self.end.get("image"):
            lay = {"image": 0.25, "title": 0.41, "subtitle": 0.465, "old_price": 0.515, "price": 0.555, "cta": 0.62}
        else:
            lay = {"title": 0.30, "subtitle": 0.365, "old_price": 0.43, "price": 0.47, "cta": 0.57}
        lay.update(self.end.get("layout", {}))
        return {k: int(H * v) for k, v in lay.items()}

    def end_card_events(self, add, s, e):
        c = self.end
        cx = W // 2
        y = self.end_layout()
        title_size = 130 if c.get("image") else 150
        if c.get("title"):
            add(s + 0.05, e, "Title", f"{{\\an5\\pos({cx},{y['title']})\\fs{title_size}\\fad(150,0)\\blur8"
                                      f"\\t(0,250,\\blur0)\\fscx120\\fscy120\\t(0,300,\\fscx100\\fscy100)}}"
                                      f"{c['title']}")
        if c.get("subtitle"):
            add(s + 0.2, e, "Sub", f"{{\\an5\\pos({cx},{y['subtitle']})\\fad(200,0)}}{self.rich(c['subtitle'])}")
        if c.get("old_price"):
            add(s + 0.4, e, "Old", f"{{\\an5\\pos({cx},{y['old_price']})\\fad(150,0)\\s1}}{c['old_price']}")
        if c.get("price"):
            add(s + 0.45, e, "Price", f"{{\\an5\\pos({cx},{y['price']})\\fad(100,0)\\fscx60\\fscy60"
                                      f"\\t(0,160,\\fscx110\\fscy110)\\t(160,260,\\fscx100\\fscy100)}}{c['price']}")
        if c.get("cta"):
            y_cta = min(y["cta"], H - SAFE_BOTTOM - 60)
            # Nabiz gibi atan CTA butonu
            pulse = "".join(f"\\t({int(t)},{int(t + 250)},\\fscx106\\fscy106)\\t({int(t + 250)},{int(t + 500)},"
                            f"\\fscx100\\fscy100)" for t in range(700, int((e - s) * 1000), 600))
            add(s + 0.6, e, "CTA", f"{{\\an5\\pos({cx},{y_cta})\\fad(120,0){pulse}}}{c['cta']}", layer=2)

    # ------------------------------------------------------------ ses

    def whoosh(self, tmp):
        p = tmp / "whoosh.wav"
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.45:c=pink:a=0.6:r=44100",
             "-af", "highpass=f=500,lowpass=f=6000,afade=t=in:d=0.25:curve=exp,afade=t=out:st=0.25:d=0.2,"
                    "aecho=0.6:0.4:20:0.3,volume=0.55,aformat=channel_layouts=stereo", str(p)])
        return p

    def audio_graph(self, segs, total, n_inputs, tmp, inputs):
        """Muzik + gecis efektleri. Donus: (filtre parcasi, cikis etiketi)."""
        parts, mix = [], []
        m = self.music
        if m.get("file"):
            inputs += ["-i", str(self.path(m["file"]))]
            idx = n_inputs
            n_inputs += 1
            off = float(m.get("offset", 0))
            vol = float(m.get("volume", 1.0))
            parts.append(f"[{idx}:a]atrim=start={off}:duration={total:.3f},asetpts=PTS-STARTPTS,"
                         f"aformat=sample_rates=44100:channel_layouts=stereo,volume={vol},"
                         f"afade=t=in:d=0.15,afade=t=out:st={max(0, total - 0.8):.3f}:d=0.8[mus]")
            mix.append("[mus]")
        if self.cfg.get("sfx", True):
            hits = [seg["start"] + seg["len"] for seg in segs if seg["out"] in TRANSITIONS]
            if hits:
                inputs += ["-i", str(self.whoosh(tmp))]
                widx = n_inputs
                n_inputs += 1
                parts.append(f"[{widx}:a]asplit={len(hits)}" + "".join(f"[w{k}]" for k in range(len(hits))))
                for k, t in enumerate(hits):
                    ms = max(0, int((t - 0.22) * 1000))
                    parts.append(f"[w{k}]adelay={ms}|{ms}[wd{k}]")
                    mix.append(f"[wd{k}]")
        if not mix:
            parts.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={total:.3f}[aout]")
            return parts, "[aout]", n_inputs
        lufs = self.cfg.get("loudness", -14)
        parts.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
                     f"atrim=duration={total:.3f},loudnorm=I={lufs}:TP=-1.5:LRA=11,"
                     "aresample=44100[aout]")
        return parts, "[aout]", n_inputs

    # ------------------------------------------------------------ montaj

    def build(self, preview=False):
        segs, total = self.timeline()
        print(f"Reklam: {len(segs)} segment, {total:.2f} sn"
              + (f" ({self.bpm} BPM, vurus = {60 / self.bpm:.3f} sn)" if self.bpm else ""))
        if total > 60:
            print("  ! 60 saniyeden uzun; Reels reklamlari icin 15-30 sn onerilir")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="reklam_") as td:
            tmp = Path(td)
            files = []
            for i, seg in enumerate(segs):
                label = seg["item"].get("file", "kapanis karti")
                print(f"  [{i + 1}/{len(segs)}] {Path(label).name}  {seg['start']:.2f}s  "
                      f"({seg['len']:.2f}s, gecis: {seg['out']})")
                if seg["item"]["kind"] == "end":
                    files.append(self.render_end(seg, i, tmp))
                else:
                    files.append(self.render_clip(seg, i, tmp))

            ass = tmp / "yazilar.ass"
            ass.write_text(self.ass_header() + "\n".join(self.events(segs)) + "\n", encoding="utf-8")

            inputs = []
            for f in files:
                inputs += ["-i", str(f)]
            graph = []
            acc, acc_len = "[0:v]", segs[0]["render"]
            for i in range(1, len(segs)):
                prev = segs[i - 1]
                nxt = f"[{i}:v]"
                lab = f"[x{i}]"
                if prev["out"] == "cut":
                    graph.append(f"{acc}{nxt}concat=n=2:v=1:a=0{lab}")
                else:
                    kind, d = TRANSITIONS[prev["out"]]
                    graph.append(f"{acc}{nxt}xfade=transition={kind}:duration={d}:offset={segs[i]['start']:.4f}{lab}")
                acc = lab
                acc_len = segs[i]["start"] + segs[i]["render"]
            post = [f"subtitles=filename='{esc_filter_path(ass)}'"]
            graph.append(f"{acc}{','.join(post)},trim=duration={total:.4f},setpts=PTS-STARTPTS[vt]")
            n_in = len(files)
            vlab = "[vt]"
            image = (self.end or {}).get("image")
            if image:
                # Urun gorseli / logo (seffaf PNG) kapanis kartinda hafif buyuyerek gelir
                inputs += ["-loop", "1", "-framerate", str(self.fps), "-i", str(self.path(image))]
                es = segs[-1]["start"]
                ih = int(self.end.get("image_height", 420))
                iy = self.end_layout()["image"]
                graph.append(f"[{n_in}:v]scale=-1:{ih}:flags=lanczos,format=rgba,"
                             f"fade=t=in:st={es + 0.1:.3f}:d=0.35:alpha=1[img]")
                graph.append(f"{vlab}[img]overlay=x=(W-w)/2:y='{iy}-h/2+40*max(0,1-(t-{es + 0.1:.3f})/0.45)'"
                             f":enable='gte(t,{es:.3f})':shortest=1[vl]")
                vlab = "[vl]"
                n_in += 1
            if self.cfg.get("progress_bar"):
                color = self.brand["primary"]
                graph.append(f"color=c={color}:s={W}x8:r={self.fps}:d={total:.4f}[pb]")
                graph.append(f"{vlab}[pb]overlay=x='-W+W*t/{total:.4f}':y={SAFE_TOP - 40}:shortest=1[vp]")
                vlab = "[vp]"
            graph.append(f"{vlab}format=yuv420p[vout]")

            aparts, alab, n_in = self.audio_graph(segs, total, n_in, tmp, inputs)
            graph += aparts

            run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
                 "-map", "[vout]", "-map", alab,
                 "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-level", "4.1",
                 "-pix_fmt", "yuv420p", "-r", str(self.fps), "-g", str(self.fps * 2),
                 "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                 "-movflags", "+faststart", "-t", f"{total:.4f}", str(self.output)])
        print(f"Hazir: {self.output}")
        if preview:
            self.previews(segs, total)
        return self.output

    def previews(self, segs, total):
        """Guvenli alan katmanli onizleme + her segmentten kare ozeti."""
        out = self.output
        safe = out.with_name(out.stem + "_guvenli_alan.mp4")
        top, bot = SAFE_TOP, SAFE_BOTTOM
        run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf",
             f"drawbox=x=0:y=0:w=iw:h={top}:color=red@0.35:t=fill,"
             f"drawbox=x=0:y=ih-{bot}:w=iw:h={bot}:color=red@0.35:t=fill,"
             f"drawbox=x=iw-150:y=ih-{bot}-560:w=150:h=560:color=red@0.25:t=fill",
             "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-c:a", "copy", str(safe)])
        sheet = out.with_name(out.stem + "_kareler.jpg")
        times = [s["start"] + min(s["len"] * 0.6, s["len"] - 0.1) for s in segs]
        sel = "+".join(f"between(t,{t:.3f},{t + 1 / self.fps:.3f})" for t in times)
        run(["ffmpeg", "-y", "-v", "error", "-i", str(safe), "-vf",
             f"select='{sel}',scale=270:-2,tile={len(times)}x1:padding=6:color=white",
             "-frames:v", "1", "-vsync", "vfr", str(sheet)])
        print(f"Onizleme: {safe}\nKare ozeti: {sheet}")


def main():
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg bulunamadi (apt install ffmpeg / brew install ffmpeg)")
    ap = argparse.ArgumentParser(description="Dikey urun tanitim reklami montajlayici")
    ap.add_argument("senaryo", help="JSON senaryo dosyasi")
    ap.add_argument("--onizleme", action="store_true", help="guvenli alan onizlemesi ve kare ozeti de uret")
    args = ap.parse_args()
    Ad(args.senaryo).build(preview=args.onizleme)


if __name__ == "__main__":
    main()
