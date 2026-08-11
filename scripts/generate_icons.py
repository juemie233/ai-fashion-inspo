"""生成浏览器插件所需的占位图标。使用 Pillow 绘制简单彩色图标。"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create_icon(size: int, output_path: Path):
    """创建一个带渐变背景的图标（紫色主题）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 绘制圆角矩形背景（紫色渐变效果）
    margin = size // 8
    for i in range(size - margin * 2):
        # 从上到下渐变
        ratio = i / (size - margin * 2)
        r = int(99 + (139 - 99) * ratio)   # 99 → 139
        g = int(102 + (92 - 102) * ratio)  # 102 → 92
        b = int(241 + (246 - 241) * ratio) # 241 → 246
        color = (r, g, b, 255)

        draw.rounded_rectangle(
            [margin, margin + i, size - margin, margin + i + 1],
            radius=size // 6,
            fill=color,
        )

    # 绘制字母 "F" (Fashion)
    try:
        font_size = size // 2
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    text = "F"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text(
        ((size - text_w) // 2, (size - text_h) // 2 - size // 16),
        text,
        fill=(255, 255, 255, 255),
        font=font,
    )

    img.save(output_path, "PNG")


def main():
    extension_dir = Path(__file__).parent.parent / "browser-extension" / "icons"
    extension_dir.mkdir(parents=True, exist_ok=True)

    for size, name in [(16, "icon16.png"), (48, "icon48.png"), (128, "icon128.png")]:
        create_icon(size, extension_dir / name)
        print(f"已生成: {name} ({size}x{size})")


if __name__ == "__main__":
    main()
