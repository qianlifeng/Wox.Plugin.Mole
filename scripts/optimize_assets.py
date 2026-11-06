"""Resize original RGBA art and combine directional sheets without regenerating art.

Run: uv run --with pillow python scripts/optimize_assets.py
Originals are retained under ignored build/asset-originals for repeatable exports.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def optimize():
    source = ROOT / "build" / "asset-originals"
    output = ROOT / "image" / "scene"
    source.mkdir(parents=True, exist_ok=True)
    names = ("house.png", "mole-atlas.png", "mole-depth-atlas.png", "dust-puff.png")
    for name in names:
        if not (source / name).exists():
            shutil.copy2(output / name, source / name)
    for name, size in (("house.png", (1152, 768)), ("dust-puff.png", (128, 128))):
        with Image.open(source / name) as image:
            image.convert("RGBA").resize(size, Image.Resampling.LANCZOS).save(output / name, optimize=True)
    atlas = Image.new("RGBA", (768, 768))
    for row, name in enumerate(("mole-atlas.png", "mole-depth-atlas.png")):
        with Image.open(source / name) as image:
            atlas.paste(image.convert("RGBA").resize((768, 384), Image.Resampling.LANCZOS), (0, row * 384))
    atlas.save(output / "mole-atlas.png", optimize=True)
    (output / "mole-depth-atlas.png").unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "scripts.export_walls"], cwd=ROOT, check=True)
    for folder in (source, output):
        total = 0
        for path in folder.glob("*.png"):
            with Image.open(path) as image:
                total += image.width * image.height * 4
                if folder == output:
                    assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
                    print(path.name, image.size, path.stat().st_size)
        print(folder.name, f"decoded RGBA: {total / 1024**2:.2f} MiB")


if __name__ == "__main__":
    optimize()
