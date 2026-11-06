"""Generate native renderer fixtures from simulated output; never execute Mole."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from src.dashboard import LABELS, Session
from src.scene import Facing, HouseScene, POINTS
from src.views import render


def main(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    metadata = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    labels = {key: metadata["I18n"]["zh_CN"]["preview_" + key] for key in LABELS}
    session = Session(started_at=100, last_output_at=100)
    scene = HouseScene(seed=7)
    frames = []

    def capture(name, dark=False, now=108, wait_ms=600):
        with patch("src.views.time.monotonic", return_value=now):
            tree = json.loads(render(session, scene, labels, root / "image").preview_data)
        frames.append({"name": name, "dark": dark, "tree": tree, "waitMs": wait_ms})

    capture("ready-light")
    capture("ready-dark", True)
    capture("ready-compact")
    scene.start(100)
    session.state = "running"
    session.feed("➤ 浏览器缓存")
    session.feed("→ Chrome 浏览器缓存 (2 items, 94MB)")
    # An in-room pose also verifies that the atlas clips to one character.
    scene.position = POINTS["bedroom"]
    scene.mode, scene.started, scene.frozen = "sweeping", 107.6, False
    scene.facing = Facing.LEFT
    capture("sweeping-light")
    capture("sweeping-compact")
    scene.facing = Facing.RIGHT
    scene.position = POINTS["study"]
    capture("sweeping-dark", True)
    scene.position = POINTS["study_entry"]
    scene.mode = "idle"
    scene.facing = Facing.BACK
    capture("walking-back")
    scene.facing = Facing.FRONT
    capture("walking-front")
    session.feed("Tracked cleanup: 1.24GB")
    session.finish(0)
    session.ended_at = 118
    scene = HouseScene(seed=7)
    scene.update(118, "completed")
    capture("complete-light")
    session.state = "failed"
    session.errors = True
    capture("failed-dark", True)
    (folder / "scenes.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")

    # Stream the same small snapshots the plugin sends during a real run.
    frames.clear()
    scene = HouseScene(seed=7)
    session = Session(started_at=100, last_output_at=100)
    capture("motion-ready")
    session.state = "running"
    scene.start(100)
    for index in range(80):
        now = 100 + index * 0.2
        scene.update(now, "running")
        session.current_item = "Chrome 浏览器缓存" if index < 40 else "开发工具缓存"
        capture(f"motion-{index:03d}", now=now, wait_ms=200)
    (folder / "motion.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")

    frames.clear()
    # Capture the carry from 00:59 to 01:00 during the native slide, not only after.
    session.started_at = 100
    scene.position = POINTS["living"]
    scene.facing = Facing.RIGHT
    for index, now in enumerate((159.8, 160, 160.1, 160.2, 160.4, 161, 161.1)):
        capture(f"effects-{index:03d}", now=now, wait_ms=100 if index else 600)
    session.state = "stopped"
    session.ended_at = 161
    capture("effects-stopped", now=180)
    (folder / "effects.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")

    frames.clear()
    session = Session(state="running", started_at=80)
    scene = HouseScene(seed=7)
    scene.location = "bedroom"
    scene.position = POINTS["bedroom"]
    scene.mode = "sweeping"
    scene.facing = Facing.LEFT
    scene.frozen = False
    capture("exit-before", now=99.8)
    session.feed("Tracked cleanup: 2.29GB")
    session.finish(0)
    session.ended_at = 100
    for index in range(40):
        now = 100 + index * 0.2
        scene.update(now, "completed")
        capture(f"exit-{index:03d}", now=now, wait_ms=200)
    capture("exit-finished", now=120)
    (folder / "exit.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")

    frames.clear()
    for name, detail in (
        ("amount-reported", "Tracked cleanup: 59.8MB | Items cleaned: 2"),
        ("amount-zero", "System was already clean; no additional space freed."),
        ("amount-unknown", "Tool cache cleanup completed."),
    ):
        session = Session(started_at=100)
        session.feed("Cleanup complete")
        session.feed(detail)
        session.feed("Free space: 123.45GB")
        session.finish(0)
        session.ended_at = 138
        scene = HouseScene(seed=7)
        scene.update(138, "completed")
        capture(name, now=138)
    (folder / "amounts.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
