"""Exhaustive native captures, using simulated state and no cleanup process."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from src.dashboard import LABELS, Session
from src.navigation import safe_position
from src.scene import EDGES, POINTS, SPOTS, SWEEP_FACING, HouseScene
from src.views import render


def main(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).resolve().parents[1] / "image"
    frames = []

    def capture(name, scene, phase=0, dark=False, wait=120):
        if not safe_position(scene.position, scene.facing, sweeping=phase == 3 or scene.mode == "sweeping"):
            raise ValueError(f"Refusing to present an unsafe pose as a valid preview: {name}")
        with patch("src.views.time.monotonic", return_value=100):
            tree = json.loads(render(Session(state="running", started_at=100), scene, LABELS, assets).preview_data)
        tree["root"]["children"][0]["children"][1]["props"]["frame"] = int(scene.facing) * 4 + phase
        frames.append({"name": name, "dark": dark, "tree": tree, "waitMs": wait})

    for edge in EDGES:
        for start, end in (edge, edge[::-1]):
            scene = HouseScene()
            scene.position = POINTS[start]
            scene.steps = [end]
            scene._walk(100)
            positions = [scene.current_position(100 + scene.duration * ratio) for ratio in (0, 0.25, 0.5, 0.75, 1)]
            scene.frozen = True
            for index, feet in enumerate(positions):
                scene.position = feet
                capture(f"occlusion-{start}-{end}-{index}", scene, 1 + index % 2)

    for spot in SPOTS:
        scene = HouseScene()
        scene.position = POINTS[spot]
        scene.facing = SWEEP_FACING[spot]
        for dark in (False, True):
            for phase in (0, 3):
                capture(f"occlusion-{spot}-sweep-{phase}-{'dark' if dark else 'light'}", scene, phase, dark)
    for state in ("stopped", "failed"):
        for index, ratio in enumerate((0.15, 0.75)):
            scene = HouseScene()
            scene.position = POINTS["study_door"]
            scene.steps = ["study_threshold"]
            scene.frozen = False
            scene._walk(100)
            scene.update(100 + scene.duration * ratio, state)
            capture(f"occlusion-{state}-{index}", scene)
    (folder / "occlusion.json").write_text(json.dumps(frames), encoding="utf-8")

    frames.clear()
    # Each snapshot independently plays a complete native edge. Capture midway
    # without any new plugin publication, including doorway depth switches.
    for index, (start, end) in enumerate([pair for edge in EDGES for pair in (edge, edge[::-1])]):
        scene = HouseScene()
        scene.position = POINTS[start]
        scene.steps = [end]
        scene.frozen = False
        scene.motion_id = index + 100
        scene._walk(100)
        capture(f"occlusion-native-{start}-{end}", scene, dark=index % 2 == 1, wait=round(scene.motion_ms * 0.72))
    for index, spot in enumerate(SPOTS):
        scene = HouseScene()
        scene.location, scene.position, scene.facing = spot, POINTS[spot], SWEEP_FACING[spot]
        scene.mode, scene.started, scene.frozen = "sweeping", 100, False
        scene.motion_id = 200 + index
        capture(f"occlusion-dust-{spot}", scene, wait=650)
    scene = HouseScene()
    scene.position = POINTS["study_turn"]
    capture("occlusion-study-turn-compact", scene, 1)
    (folder / "occlusion-motion.json").write_text(json.dumps(frames), encoding="utf-8")

    frames.clear()
    scene = HouseScene()
    scene.position = POINTS["study_turn"]
    for enabled in (False, True):
        capture(f"occlusion-corner-{'after' if enabled else 'before'}", scene, 1)
        if not enabled:
            children = frames[-1]["tree"]["root"]["children"][0]["children"]
            for wall in children[4:]:
                wall["props"]["style"]["opacity"] = 0
            for wall in children[0]["children"][1:]:
                wall["props"]["style"]["opacity"] = 1
    for dark in (False, True):
        for enabled in (False, True):
            capture(f"occlusion-empty-{'dark' if dark else 'light'}-{'layers' if enabled else 'base'}", scene, dark=dark)
            children = frames[-1]["tree"]["root"]["children"][0]["children"]
            children[1]["props"]["style"]["opacity"] = 0
            for wall in children[4:]:
                wall["props"]["style"]["opacity"] = int(enabled)
            # Compare against the original, unsplit source image, with all wall
            # layers in front. The character is hidden for an empty-scene check.
            for wall in children[0]["children"][1:]:
                wall["props"]["style"]["opacity"] = 0
            if not enabled:
                children[0]["children"][0]["props"]["source"]["imageData"] = str((assets / "scene" / "house.png").resolve())
    (folder / "occlusion-validation.json").write_text(json.dumps(frames), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
