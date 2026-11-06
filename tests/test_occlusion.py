"""Depth, silhouette clearance and packaged-art regression checks."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from src.dashboard import LABELS, Session
from src.occlusion import ART_HEIGHT, ART_SCALE, ART_WIDTH, WALLS
from src.navigation import FLOOR_BLOCKS, safe_position, safe_segment
from src.scene import EDGES, POINTS, SPOTS, SWEEP_FACING, Facing, HouseScene
from src.views import SPRITE, WIDTH, render

ASSETS = Path(__file__).resolve().parents[1] / "image"


def at_art(x, y):
    return x / ART_SCALE, y / ART_SCALE


class TestOcclusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atlas = Image.open(ASSETS / "scene" / "mole-atlas.png").convert("RGBA")
        cls.layers = {}
        with Image.open(ASSETS / "scene" / "walls-atlas.png") as atlas:
            cls.wall_art = [atlas.crop((0, i * ART_HEIGHT, ART_WIDTH, (i + 1) * ART_HEIGHT)).convert("RGBA") for i in range(len(WALLS))]
        for wall, art in zip(WALLS, cls.wall_art):
            cls.layers[wall.name] = art.getchannel("A")

    def silhouette(self, feet, facing, phase):
        size = round(SPRITE * ART_WIDTH / WIDTH)
        cell = self.atlas.crop((phase * 192, facing * 192, (phase + 1) * 192, (facing + 1) * 192))
        alpha = cell.resize((size, size), Image.Resampling.LANCZOS).getchannel("A")
        layer = Image.new("L", (ART_WIDTH, ART_HEIGHT))
        x, y = (v * ART_SCALE for v in feet)
        layer.paste(alpha, (round(x - size / 2), round(y - size * 0.866)))
        return layer

    def tree(self, scene, now=100):
        with patch("src.views.time.monotonic", return_value=now):
            tree = json.loads(render(Session(state="running", started_at=100), scene, LABELS, ASSETS).preview_data)
        return tree["root"]["children"][0]["children"]

    def test_wall_cutouts_preserve_source_pixels_and_transparent_openings(self):
        with Image.open(ASSETS / "scene" / "house.png") as source:
            for wall, art in zip(WALLS, self.wall_art):
                with self.subTest(wall=wall.name):
                    cutout = art.crop(wall.bounds)
                    self.assertEqual(cutout.mode, "RGBA")
                    self.assertEqual(cutout.size, (wall.bounds[2] - wall.bounds[0], wall.bounds[3] - wall.bounds[1]))
                    self.assertEqual(cutout.getpixel((0, 0))[3], 0)
                    difference = ImageChops.difference(cutout.convert("RGB"), source.crop(wall.bounds).convert("RGB"))
                    self.assertIsNone(difference.getbbox())
        for point in ((670, 290), (675, 330), (545, 300), (576, 520), (576, 620)):
            for layer in self.layers.values():
                self.assertEqual(layer.getpixel(point), 0, point)
        for name, point in (
            ("study", (733, 300)),
            ("center", (625, 300)),
            ("bedroom", (420, 300)),
            ("front-left", (470, 580)),
            ("front-right", (680, 580)),
        ):
            # The source art contains a few nearly opaque (253/254) wall pixels.
            self.assertGreaterEqual(self.layers[name].getpixel(point), 250)

    def test_base_and_wall_layers_reconstruct_original_rgba_without_double_shadows(self):
        with Image.open(ASSETS / "scene" / "house.png") as original, Image.open(ASSETS / "scene" / "house-base.png") as base:
            restored = base.convert("RGBA")
            for art in self.wall_art:
                restored = Image.alpha_composite(restored, art)
            self.assertEqual(ImageChops.difference(original, restored).getextrema(), ((0, 0),) * 4)

    def test_front_and_back_of_each_wall_and_study_corner(self):
        probes = {
            "bedroom": ((420, 310), (420, 370)),
            "center": ((625, 310), (625, 370)),
            "study": ((733, 315), (733, 370)),
            "front-left": ((470, 620), (470, 690)),
            "front-right": ((680, 620), (680, 690)),
        }
        for wall in WALLS:
            with self.subTest(wall=wall.name):
                behind, front = probes[wall.name]
                self.assertTrue(wall.covers(at_art(*behind)))
                self.assertFalse(wall.covers(at_art(*front)))
        study = next(w for w in WALLS if w.name == "study")
        self.assertTrue(study.covers(at_art(800, 430)))
        self.assertFalse(study.covers(at_art(730, 430)))

    def test_depth_switches_clear_both_walking_frames_in_both_directions(self):
        checked = 0
        for edge in EDGES:
            for start, end in (edge, edge[::-1]):
                scene = HouseScene()
                scene.position = POINTS[start]
                scene.steps = [end]
                scene._walk(100)
                for wall in WALLS:
                    frames = wall.keyframes(scene.origin, scene.position, scene.motion_ms)
                    self.assertEqual(frames[0]["at"], 0)
                    self.assertEqual(frames[-1]["at"], scene.motion_ms)
                    for previous, current in zip(frames, frames[1:]):
                        self.assertGreater(current["at"], previous["at"])
                        if previous["opacity"] == current["opacity"]:
                            continue
                        self.assertEqual(current["at"] - previous["at"], 1)
                        checked += 1
                        for at in (previous["at"], current["at"]):
                            feet = scene.current_position(100 + at / scene.motion_ms * scene.duration)
                            for phase in (1, 2):
                                with self.subTest(start=start, end=end, wall=wall.name, phase=phase, at=at):
                                    overlap = ImageChops.multiply(self.silhouette(feet, scene.facing, phase), self.layers[wall.name])
                                    # Ignore only the faint antialias fringe, never solid body/broom pixels.
                                    self.assertIsNone(overlap.point(lambda value: 255 if value > 32 else 0).getbbox())
        self.assertGreater(checked, 10)

    def test_native_depth_timeline_matches_feet_through_every_edge(self):
        for edge in EDGES:
            for start, end in (edge, edge[::-1]):
                scene = HouseScene()
                scene.position = POINTS[start]
                scene.steps = [end]
                scene._walk(100)
                for wall in WALLS:
                    frames = wall.keyframes(scene.origin, scene.position, scene.motion_ms)
                    for index in range(101):
                        at = scene.motion_ms * index / 100
                        a, b = next((a, b) for a, b in zip(frames, frames[1:]) if a["at"] <= at <= b["at"])
                        if a["opacity"] != b["opacity"]:
                            continue  # The intentionally discrete 1 ms crossing interval.
                        feet = scene.current_position(100 + scene.duration * index / 100)
                        self.assertEqual(a["opacity"], int(wall.covers(feet)), (start, end, wall.name, index))

    def test_invalid_wall_straddling_and_wall_top_sweeping_are_rejected(self):
        for facing in Facing:
            self.assertFalse(safe_position(at_art(625, 310), facing))
        self.assertFalse(safe_position(at_art(730, 430), Facing.RIGHT, sweeping=True))
        # The old doorway turn is too wide for a side-facing body, even though
        # its foot anchor alone fits in the opening.
        self.assertFalse(safe_segment((894, 385), (1150, 420), Facing.RIGHT))
        scene = HouseScene()
        scene.position = at_art(625, 310)
        scene.steps = ["study"]
        with self.assertRaisesRegex(ValueError, "Unsafe walking segment"):
            scene._walk(100)
        self.assertEqual(scene.position, at_art(625, 310))
        self.assertEqual(scene.steps, ["study"])
        scene = HouseScene()
        scene.location, scene.position = "study", at_art(730, 430)
        scene.mode, scene.frozen = "waiting", False
        with self.assertRaisesRegex(ValueError, "Unsafe sweeping spot"):
            scene.update(100, "running")
        self.assertNotEqual(scene.mode, "sweeping")

    def test_all_sweep_strokes_clear_visible_walls_and_all_spots_are_on_floor(self):
        walls = Image.new("L", (ART_WIDTH, ART_HEIGHT))
        for layer in self.layers.values():
            walls = ImageChops.lighter(walls, layer)
        for spot in SPOTS:
            facing = SWEEP_FACING[spot]
            self.assertTrue(safe_position(POINTS[spot], facing, sweeping=True), spot)
            for phase in (0, 3):
                overlap = ImageChops.multiply(self.silhouette(POINTS[spot], facing, phase), walls)
                self.assertIsNone(overlap.point(lambda value: 255 if value > 32 else 0).getbbox(), (spot, phase))

    def test_every_walk_frame_keeps_body_and_carried_broom_off_wall_footprints(self):
        ground = Image.new("L", (ART_WIDTH, ART_HEIGHT))
        draw = ImageDraw.Draw(ground)
        for polygon in FLOOR_BLOCKS:
            draw.polygon(polygon, fill=255)
        size = round(SPRITE * ART_WIDTH / WIDTH)
        for edge in EDGES:
            for start, end in (edge, edge[::-1]):
                scene = HouseScene()
                scene.position = POINTS[start]
                scene.steps = [end]
                scene._walk(100)
                for phase in (0, 1, 2):
                    cell = self.atlas.crop((phase * 192, int(scene.facing) * 192, (phase + 1) * 192, (int(scene.facing) + 1) * 192))
                    contact = cell.resize((size, size), Image.Resampling.LANCZOS).getchannel("A")
                    # Test actual floor-contact alpha, independently of the
                    # conservative runtime rectangle. Heads can be occluded.
                    ImageDraw.Draw(contact).rectangle((0, 0, size, round(size * 155 / 192) - 1), fill=0)
                    for sample in range(101):
                        feet = scene.current_position(100 + scene.duration * sample / 100)
                        x, y = (v * ART_SCALE for v in feet)
                        bounds = (round(x - size / 2), round(y - size * 0.866))
                        region = ground.crop((*bounds, bounds[0] + size, bounds[1] + size))
                        overlap = ImageChops.multiply(contact, region)
                        self.assertIsNone(overlap.point(lambda value: 255 if value > 32 else 0).getbbox(), (start, end, phase, sample))

    def test_walls_follow_particles_and_share_motion_clock_without_remounting(self):
        scene = HouseScene()
        scene.position = POINTS["study_door"]
        scene.steps = ["study_threshold"]
        scene.frozen = False
        scene._walk(100)
        children = self.tree(scene, 100.1)
        sprite = children[1]
        self.assertEqual([c["key"] for c in children[2:4]], ["dust-0", "dust-1"])
        overlays = children[4:]
        behind = children[0]["children"][1:]
        self.assertEqual(len(overlays), len(WALLS))
        timed = [node for node in overlays if "timeline" in node["props"]]
        self.assertGreater(len(timed), 0)
        for node in timed:
            timeline = node["props"]["timeline"]
            self.assertEqual(timeline["playbackId"], sprite["props"]["playbackId"])
            self.assertEqual(timeline["elapsed"], 100)
            self.assertEqual(timeline["duration"], scene.motion_ms)
        for front, rear in zip(overlays, behind):
            self.assertEqual(front["props"]["source"], rear["props"]["source"])
            self.assertEqual(front["props"]["style"]["opacity"] + rear["props"]["style"]["opacity"], 1)
            if "timeline" in front["props"]:
                for a, b in zip(front["props"]["timeline"]["keyframes"], rear["props"]["timeline"]["keyframes"]):
                    self.assertEqual(a["at"], b["at"])
                    self.assertEqual(a["opacity"] + b["opacity"], 1)
        keys = [node["key"] for node in overlays]
        for spot in SPOTS:
            scene.position = POINTS[spot]
            scene.mode = "sweeping"
            self.assertEqual([node["key"] for node in self.tree(scene)[4:]], keys)

    def test_stop_and_failure_freeze_current_depth_not_destination_depth(self):
        for state in ("stopped", "failed"):
            for ratio in (0.15, 0.75):
                scene = HouseScene()
                scene.position = POINTS["study_door"]
                scene.steps = ["study_threshold"]
                scene.frozen = False
                scene._walk(100)
                now = 100 + scene.duration * ratio
                feet = scene.current_position(now)
                scene.update(now, state)
                for wall, node in zip(WALLS, self.tree(scene, now)[4:]):
                    self.assertNotIn("timeline", node["props"])
                    self.assertEqual(node["props"]["style"]["opacity"], int(wall.covers(feet)))
