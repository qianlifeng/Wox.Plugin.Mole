"""Build a portable .wox archive, including nested art and only the runtime SDK."""

import importlib.metadata
import json
import tempfile
import zipfile
from pathlib import Path

import wox_plugin

ROOT = Path(__file__).resolve().parent.parent


def package() -> Path:
    if importlib.metadata.version("wox-plugin") != "0.0.97":
        raise RuntimeError("Run uv sync before packaging: wox-plugin 0.0.97 is required")
    metadata = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    output = ROOT / "wox.plugin.mole.wox"
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=build, suffix=".wox", delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(ROOT / "plugin.json", "plugin.json")
            archive.writestr("__init__.py", "from .mole.main import plugin\n\n__all__ = ['plugin']\n")
            for source, destination in (
                (ROOT / "src", "mole"),
                (ROOT / "image", "image"),
                (Path(wox_plugin.__file__).parent, "dependencies/wox_plugin"),
            ):
                for path in sorted(source.rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                        archive.write(path, destination + "/" + path.relative_to(source).as_posix())
            for path in ("LICENSE", "README.md"):
                if (ROOT / path).is_file():
                    archive.write(ROOT / path, path)
            assert metadata["Entry"] in archive.namelist()
            assert "image/scene/house.png" in archive.namelist()
            assert "image/scene/house-base.png" in archive.namelist()
            assert "image/scene/mole-atlas.png" in archive.namelist()
            assert "image/scene/dust-puff.png" in archive.namelist()
            assert "image/scene/walls-atlas.png" in archive.namelist()
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    return output


if __name__ == "__main__":
    print(package())
