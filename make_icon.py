# -*- coding: utf-8 -*-
"""画程序图标 → app.ico

设计：圆角渐变方块（蓝 → 紫）+ 白色八分音符 ♪，底部一道高光。
用 4 倍超采样画再缩，边缘干净；输出 16~256 多尺寸 ico。
改配色 / 改符号都在这里改，跑一遍即可。
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
SS = 4                       # 超采样倍数
SIZE = 256 * SS
C_TL = (79, 140, 255)        # 左上：亮蓝
C_BR = (109, 40, 217)        # 右下：紫
WHITE = (255, 255, 255)
FONT_CANDIDATES = (r"C:\Windows\Fonts\seguisym.ttf",   # Segoe UI Symbol，含 ♪
                   r"C:\Windows\Fonts\seguiemj.ttf",
                   r"C:\Windows\Fonts\segoeui.ttf")
GLYPH = "\u266a"             # ♪


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def make_tile():
    """圆角方块 + 对角线渐变 + 顶部高光"""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 渐变：一行一行画（对角线 → 用 x+y 插值）
    n = SIZE
    for i in range(n):
        t = i / (n - 1.0)
        d.line([(0, i), (n, i)], fill=lerp(C_TL, C_BR, t))
    # 再叠一层纵向渐变（半透明压上去，做出对角光感）
    grad = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(n):
        t = i / (n - 1.0)
        gd.line([(i, 0), (i, n)], fill=lerp(C_TL, C_BR, t) + (150,))
    img = Image.alpha_composite(img, grad)

    # 圆角遮罩
    r = int(n * 0.22)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=r, fill=255)
    img.putalpha(mask)
    return img


def font_for(size):
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_glyph(img):
    """居中画白色 ♪"""
    n = img.size[0]
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 先找一个能把 ♪ 撑到目标高度的字号
    target = int(n * 0.62)
    f = font_for(target)
    bb = d.textbbox((0, 0), GLYPH, font=f)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w and h:
        f = font_for(int(target * target / max(w, h)))
        bb = d.textbbox((0, 0), GLYPH, font=f)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
    x = (n - w) / 2 - bb[0]
    y = (n - h) / 2 - bb[1] - n * 0.02
    d.text((x, y), GLYPH, font=f, fill=WHITE + (255,))
    return Image.alpha_composite(img, layer)


def main():
    img = make_tile()
    img = draw_glyph(img)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    print("写出 %s（%d 字节，尺寸 %s）"
          % (OUT, os.path.getsize(OUT), "×".join(str(s) for s in sizes)))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(os.path.dirname(OUT), "app_preview.png"))
    print("另存预览 app_preview.png")


if __name__ == "__main__":
    main()
