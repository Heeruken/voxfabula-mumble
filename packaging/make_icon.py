"""Genera la grafica di Vox Fabula Voice dai file del sito (packaging/brand/):

  icon.ico                 il cristallo del logo (exe, barra delle applicazioni, installer)
  brand/wizard-side.bmp    installer, pagina di benvenuto: cielo astrale + logo
  brand/wizard-small.bmp   installer, angolo in alto a destra: il cristallo

Sorgenti: brand/cristallo.png (favicon di voxfabula.it, gia' scontornato) e
brand/logo.png (logo con la pergamena). Uso:  python packaging/make_icon.py
"""
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.join(HERE, "brand")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def crystal() -> Image.Image:
    src = Image.open(os.path.join(BRAND, "cristallo.png")).convert("RGBA")
    return src.crop(src.getbbox())                      # via i bordi vuoti


def fit_square(img: Image.Image, fill: float) -> Image.Image:
    side = int(max(img.size) / fill)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return sq


def make_icon() -> None:
    # il cristallo e' in diagonale: riempie quasi tutto, cosi' si legge anche a 16 px
    big = fit_square(crystal(), 0.94).resize((256, 256), Image.LANCZOS)
    dst = os.path.join(HERE, "icon.ico")
    big.save(dst, sizes=[(s, s) for s in SIZES])
    print("saved", dst)


def astral_sky(w: int, h: int) -> Image.Image:
    """Lo sfondo delle pagine Eventi/Lore del sito: notte viola e blu, stelle."""
    top, mid, bot = (23, 18, 56), (14, 11, 34), (7, 5, 19)
    sky = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(sky)
    for y in range(h):                                   # sfumatura verticale
        t = y / (h - 1)
        a, b, u = (top, mid, t / 0.55) if t < 0.55 else (mid, bot, (t - 0.55) / 0.45)
        d.line([(0, y), (w, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * u) for i in range(3)))
    glow = Image.new("RGB", (w, h))
    g = ImageDraw.Draw(glow)
    g.ellipse([-w * 0.5, -h * 0.25, w * 0.8, h * 0.45], fill=(70, 45, 150))   # nebulosa viola
    g.ellipse([w * 0.2, h * 0.55, w * 1.5, h * 1.2], fill=(25, 70, 115))      # nebulosa blu
    sky = ImageChops.add(sky, glow.filter(ImageFilter.GaussianBlur(w * 0.3)))
    d = ImageDraw.Draw(sky)
    rnd = random.Random(7)
    for _ in range(int(w * h / 900)):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.choice((0.6, 0.8, 1.0, 1.3))
        v = rnd.randint(150, 255)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(v, v, min(255, v + 15)))
    return sky


def make_wizard() -> None:
    # pagina di benvenuto: 164x314 a 100%, la facciamo al doppio per gli schermi ad alta densita'
    w, h = 328, 628
    side = astral_sky(w, h).convert("RGBA")
    logo = Image.open(os.path.join(BRAND, "logo.png")).convert("RGBA")
    lw = int(w * 0.86)
    logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
    halo = Image.new("RGBA", side.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([w * 0.05, h * 0.2, w * 0.95, h * 0.62], fill=(143, 116, 232, 90))
    side = Image.alpha_composite(side, halo.filter(ImageFilter.GaussianBlur(40)))
    side.alpha_composite(logo, ((w - lw) // 2, int(h * 0.26)))
    side.convert("RGB").save(os.path.join(BRAND, "wizard-side.bmp"))

    # angolo in alto a destra (fondo bianco come l'intestazione dell'installer): 55x58 al doppio
    small = Image.new("RGBA", (110, 116), (255, 255, 255, 255))
    c = crystal()
    c = c.resize((round(c.width * 108 / c.height), 108), Image.LANCZOS)
    small.alpha_composite(c, ((110 - c.width) // 2, 4))
    small.convert("RGB").save(os.path.join(BRAND, "wizard-small.bmp"))
    print("saved wizard images")


if __name__ == "__main__":
    make_icon()
    make_wizard()
