"""Draw the app icon (alm/ui/app.ico). Build-time only: needs Pillow (pip install pillow).

Design: blue rounded tile, white IC chip with pins, green "download" arrow badge.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "alm" / "ui" / "app.ico"
S = 1024                      # draw big, downscale for crisp edges


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: vertical blue gradient in a rounded square
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (37, 99, 235), (17, 52, 140)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # IC chip body with pins
    body = [262, 262, 762, 762]
    pin_w, pin_l, n = 46, 92, 5
    span = body[2] - body[0]
    for i in range(n):
        c = body[0] + span * (i + 0.5) / n
        for box in ([c - pin_w / 2, body[1] - pin_l, c + pin_w / 2, body[1] + 10],
                    [c - pin_w / 2, body[3] - 10, c + pin_w / 2, body[3] + pin_l],
                    [body[0] - pin_l, c - pin_w / 2, body[0] + 10, c + pin_w / 2],
                    [body[2] - 10, c - pin_w / 2, body[2] + pin_l, c + pin_w / 2]):
            d.rounded_rectangle(box, radius=12, fill=(226, 232, 240, 255))
    d.rounded_rectangle(body, radius=56, fill=(255, 255, 255, 255))
    d.ellipse([310, 310, 370, 370], fill=(37, 99, 235, 255))          # pin-1 marker
    # schematic-symbol hint on the chip: a footprint pad grid
    for r in range(3):
        for c in range(3):
            x, y = 410 + c * 80, 430 + r * 80
            d.rounded_rectangle([x, y, x + 52, y + 52], radius=10, fill=(148, 163, 184, 255))

    # green download badge, bottom-right
    d.ellipse([600, 600, 960, 960], fill=(255, 255, 255, 255))
    d.ellipse([622, 622, 938, 938], fill=(22, 163, 74, 255))
    d.rectangle([752, 680, 808, 800], fill=(255, 255, 255, 255))
    d.polygon([(690, 790), (870, 790), (780, 880)], fill=(255, 255, 255, 255))
    return img


def main() -> None:
    img = draw()
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.resize((256, 256), Image.LANCZOS).save(OUT, sizes=sizes)
    img.resize((512, 512), Image.LANCZOS).save(OUT.with_name("app_preview.png"))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
