"""Genera packaging/icon.ico - microfono dorato su sfondo scuro art-deco.
Render a 1024 px con anti-alias, poi salva un .ico multi-size."""
import os

from PIL import Image, ImageDraw

S = 1024


def vgrad(top, bot):
    g = Image.new("RGB", (1, S))
    for y in range(S):
        t = y / (S - 1)
        g.putpixel((0, y), tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
    return g.resize((S, S)).convert("RGBA")


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # sfondo arrotondato con gradiente verticale caldo
    mask = Image.new("L", (S, S), 0)
    r = int(S * 0.18)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
    img = Image.composite(vgrad((30, 24, 15), (10, 8, 6)), img, mask)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 10, S - 11, S - 11], radius=r - 6, outline=(201, 162, 39, 150), width=6)

    gold = (233, 198, 106, 255)
    cx = S // 2

    # capsula (testa del microfono) con gradiente dorato
    cap_w, cap_h = int(S * 0.26), int(S * 0.40)
    cx0, cx1 = cx - cap_w // 2, cx + cap_w // 2
    cy0 = int(S * 0.17)
    cy1 = cy0 + cap_h
    capmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(capmask).rounded_rectangle([cx0, cy0, cx1, cy1], radius=cap_w // 2, fill=255)
    img = Image.composite(vgrad((245, 224, 162), (198, 158, 36)), img, capmask)

    d = ImageDraw.Draw(img)
    # griglia
    for i in range(3):
        yy = cy0 + cap_h * (0.30 + 0.17 * i)
        d.line([cx0 + cap_w * 0.20, yy, cx1 - cap_w * 0.20, yy], fill=(70, 52, 14, 170), width=int(S * 0.013))

    # arco/forcella (U che culla il microfono)
    pad = int(S * 0.075)
    d.arc([cx0 - pad, cy0 + int(cap_h * 0.42), cx1 + pad, cy1 + pad],
          start=18, end=162, fill=gold, width=int(S * 0.032))
    # stelo + base
    stem_bot = int(S * 0.80)
    d.line([cx, cy1 + pad - int(S * 0.015), cx, stem_bot], fill=gold, width=int(S * 0.032))
    bw = int(S * 0.20)
    d.rounded_rectangle([cx - bw // 2, stem_bot, cx + bw // 2, stem_bot + int(S * 0.030)],
                        radius=int(S * 0.015), fill=gold)

    out = img.resize((256, 256), Image.LANCZOS)
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    out.save(dst, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved", dst)


if __name__ == "__main__":
    main()
