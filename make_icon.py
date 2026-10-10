# -*- coding: utf-8 -*-
"""画程序图标 → app.ico

设计：圆角渐变方块（蓝 → 紫）上放一台 PSP 掌机（横握），
屏幕里是一组音频波形条 —— PSP + ATRAC3 音频，一眼能对上。
超采样 4 倍绘制再缩，边缘干净；输出 16~256 九种尺寸。
改配色 / 改造型都在这里改，跑一遍即可。
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
SS = 4                       # 超采样倍数
N = 256 * SS
C_TL = (79, 140, 255)        # 左上：亮蓝
C_BR = (109, 40, 217)        # 右下：紫
C_MID = (92, 84, 232)        # 屏幕填充色（取渐变中段）
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def make_tile():
    """圆角方块 + 双向渐变"""
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(N):                                  # 横向渐变
        d.line([(0, i), (N, i)], fill=lerp(C_TL, C_BR, i / (N - 1.0)))
    grad = Image.new("RGBA", (N, N), (0, 0, 0, 0))      # 纵向再压一层，做出斜向光感
    gd = ImageDraw.Draw(grad)
    for i in range(N):
        gd.line([(i, 0), (i, N)], fill=lerp(C_TL, C_BR, i / (N - 1.0)) + (150,))
    img = Image.alpha_composite(img, grad)

    r = int(N * 0.22)
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], radius=r, fill=255)
    img.putalpha(mask)
    return img


def draw_psp(img):
    """白色的 PSP 机身 + 渐变色屏幕 + 白色波形条 + 左右按键"""
    n = img.size[0]
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 机身（横握，居中）
    bw, bh = int(n * 0.80), int(n * 0.36)
    bx, by = (n - bw) // 2, int(n * 0.32)
    d.rounded_rectangle([bx, by, bx + bw, by + bh],
                        radius=int(bh * 0.36), fill=WHITE + (255,))

    # 屏幕（占中间约 62% 宽度，两侧留出操作区）
    sw, sh = int(bw * 0.62), int(bh * 0.66)
    sx, sy = bx + (bw - sw) // 2, by + (bh - sh) // 2
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh],
                        radius=int(sh * 0.18), fill=C_MID + (255,))

    # 屏幕里的音频波形（5 根竖条）
    gap = int(sw * 0.07)
    barw = int((sw - gap * 6) / 5)
    heights = (0.45, 0.85, 1.0, 0.72, 0.38)
    cy = sy + sh // 2
    for i, hf in enumerate(heights):
        h = int(sh * 0.60 * hf)
        x = sx + gap + i * (barw + gap)
        d.rounded_rectangle([x, cy - h // 2, x + barw, cy + h // 2],
                            radius=max(2, barw // 2), fill=WHITE + (255,))

    # 左侧操作区：方向键（一个小十字圆点阵）
    br = int(bh * 0.10)
    lcx = (bx + sx) // 2
    bcy = by + bh // 2
    for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
        cxx = lcx + dx * int(br * 1.9)
        cyy = bcy + dy * int(br * 1.9)
        d.ellipse([cxx - br, cyy - br, cxx + br, cyy + br], fill=C_MID + (255,))

    # 右侧操作区：两颗动作键
    rcx = (sx + sw + bx + bw) // 2
    for dx, dy in ((-1, -1), (1, 1)):
        cxx = rcx + dx * int(br * 1.5)
        cyy = bcy + dy * int(br * 1.5)
        d.ellipse([cxx - br, cyy - br, cxx + br, cyy + br], fill=C_MID + (255,))

    return Image.alpha_composite(img, layer)


def main():
    img = draw_psp(make_tile())
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    print("写出 %s（%d 字节）" % (OUT, os.path.getsize(OUT)))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(os.path.dirname(OUT), "app_preview.png"))
    print("另存预览 app_preview.png")


if __name__ == "__main__":
    main()
