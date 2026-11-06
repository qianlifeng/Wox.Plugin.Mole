"""Export pixel-aligned foreground cutouts from the existing house, without repainting.

Run: uv run --with pillow python -m scripts.export_walls
The atlas and complementary base are build assets; Pillow is not a runtime dependency.
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageMath

from src.occlusion import ART_HEIGHT, ART_WIDTH, WALLS

ROOT = Path(__file__).resolve().parent.parent


def export_walls() -> None:
    folder = ROOT / "image" / "scene"
    with Image.open(folder / "house.png") as source:
        assert source.size == (ART_WIDTH, ART_HEIGHT)
        source = source.convert("RGBA")
        remaining_coverage = Image.new("L", source.size, 255)
        remaining_alpha = source.getchannel("A").convert("F")
        atlas = Image.new("RGBA", (ART_WIDTH, ART_HEIGHT * len(WALLS)))
        for index, wall in enumerate(WALLS):
            left, top, right, bottom = wall.bounds
            size = right - left, bottom - top
            mask = Image.new("L", (size[0] * 4, size[1] * 4))
            ImageDraw.Draw(mask).polygon([((x - left) * 4, (y - top) * 4) for x, y in wall.outline], fill=255)
            mask = mask.resize(size, Image.Resampling.LANCZOS)
            # Assign shared boundary pixels once, including the study/front-wall
            # junction. Original art stays unchanged and is the export source.
            mask = ImageChops.multiply(mask, remaining_coverage.crop(wall.bounds))
            coverage = Image.new("L", source.size)
            coverage.paste(mask, (left, top))
            remaining_coverage = ImageChops.subtract(remaining_coverage, coverage)
            cutout = source.crop(wall.bounds)
            cutout.putalpha(ImageChops.multiply(cutout.getchannel("A"), mask))
            atlas.paste(cutout, (left, top + index * ART_HEIGHT))
            removed = Image.new("L", source.size)
            removed.paste(cutout.getchannel("A"), (left, top))
            # Inverse source-over: S = W + B * (1 - W). Simply overlaying W
            # over S doubles translucent shadows; simply subtracting W leaves
            # antialias seams. All layers retain the same original RGB.
            remaining_alpha = ImageMath.lambda_eval(
                lambda v: (v["base"] - v["wall"]) * 255 / (255 - v["wall"] + (v["wall"] == 255)),
                base=remaining_alpha,
                wall=removed.convert("F"),
            )
            print(wall.name, size)
        background = source.copy()
        background.putalpha(ImageMath.lambda_eval(lambda v: v["alpha"] + 0.5, alpha=remaining_alpha).convert("L"))
        background.save(folder / "house-base.png", optimize=True)
        atlas.save(folder / "walls-atlas.png", optimize=True)


if __name__ == "__main__":
    export_walls()
