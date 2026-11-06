import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard import LABELS, Session
from src.scene import EDGES, POINTS, SPOTS, Facing, HouseScene, route
from src.views import SPRITE, render, timer_node


class TestChoreography(unittest.TestCase):
    def test_all_room_routes_follow_doorways(self):
        edges = {frozenset(edge) for edge in EDGES}
        for start in POINTS:
            for end in SPOTS:
                steps = [start, *route(start, end)]
                self.assertEqual(steps[-1], end)
                for a, b in zip(steps, steps[1:]):
                    self.assertIn(frozenset((a, b)), edges)
        self.assertEqual(route("porch", "bedroom")[:3], ["door", "hall", "junction"])

    def test_wait_enter_walk_sweep_and_return_at_completion(self):
        scene = HouseScene(seed=7)
        scene.update(100, "ready")
        self.assertEqual(scene.position, POINTS["porch"])
        scene.start(100)
        scene.update(100.4, "running")
        self.assertEqual(scene.destination, "door")
        self.assertEqual(scene.facing, Facing.BACK)
        self.assertGreater(scene.motion_ms, 0)
        observed = set()
        for i in range(600):
            scene.update(100.6 + i * 0.2, "running")
            if scene.mode == "sweeping":
                observed.add(scene.location)
        self.assertGreaterEqual(len(observed), 3)
        for i in range(150):
            scene.update(221 + i * 0.2, "completed")
        position = scene.position
        self.assertEqual(position, POINTS["porch"])
        scene.update(999, "completed")
        self.assertEqual(scene.position, position)
        self.assertEqual(scene.motion_ms, 0)

    def test_completion_returns_from_every_room_through_doorways(self):
        for spot in SPOTS:
            with self.subTest(spot=spot):
                scene = HouseScene(seed=7)
                scene.location = spot
                scene.position = POINTS[spot]
                scene.mode = "sweeping"
                scene.frozen = False
                destinations = []
                for i in range(150):
                    previous_motion = scene.motion_id
                    scene.update(100 + i * 0.2, "completed")
                    if scene.motion_id != previous_motion:
                        destinations.append(scene.destination)
                    self.assertNotEqual(scene.mode, "sweeping")
                self.assertEqual(destinations, route(spot, "porch"))
                self.assertEqual(scene.location, "porch")
                self.assertEqual(scene.position, POINTS["porch"])
                self.assertEqual(scene.facing, Facing.FRONT)
                self.assertTrue(scene.frozen)

    def test_completion_mid_walk_finishes_the_edge_without_teleporting(self):
        scene = HouseScene(seed=7)
        scene.start(100)
        scene.update(100.4, "running")
        expected = scene.current_position(100.6)
        destination, deadline = scene.destination, scene.deadline
        scene.update(100.6, "completed")
        self.assertEqual(scene.current_position(100.6), expected)
        self.assertEqual(scene.destination, destination)
        self.assertEqual(scene.deadline, deadline)
        self.assertEqual(scene.steps, route(destination, "porch"))
        for i in range(100):
            scene.update(100.8 + i * 0.2, "completed")
        self.assertEqual(scene.position, POINTS["porch"])
        self.assertTrue(scene.frozen)

    def test_completion_before_entering_stays_outside(self):
        scene = HouseScene(seed=7)
        scene.start(100)
        scene.update(100.1, "completed")
        self.assertTrue(scene.frozen)
        self.assertEqual(scene.position, POINTS["porch"])

    def test_stop_mid_walk_does_not_teleport_to_target(self):
        scene = HouseScene(seed=1)
        scene.start(0)
        scene.update(0.3, "running")
        expected = scene.current_position(0.5)
        scene.update(0.5, "stopped")
        self.assertEqual(scene.position, expected)
        self.assertNotEqual(scene.position, POINTS["door"])

    def test_walk_faces_the_direction_of_travel(self):
        for start, end, facing in (
            ("porch", "door", Facing.BACK),
            ("door", "porch", Facing.FRONT),
            ("junction", "study_door", Facing.RIGHT),
            ("study_door", "junction", Facing.LEFT),
        ):
            with self.subTest(facing=facing):
                scene = HouseScene(seed=7)
                scene.position = POINTS[start]
                scene.steps = [end]
                scene._walk(100)
                self.assertEqual(scene.facing, facing)
        # Body and broom occupy only part of this cell, leaving doorway clearance.
        self.assertLessEqual(SPRITE, 60)

    def test_sweep_uses_fixed_particles_and_a_native_shared_clock(self):
        scene = HouseScene(seed=7)
        scene.frozen = False
        scene.mode = "sweeping"
        scene.started, scene.deadline = 100, 104
        scene.position = POINTS["living"]
        state = Session(state="running", started_at=100)
        with patch("src.views.time.monotonic", return_value=100.55):
            tree = json.loads(render(state, scene, LABELS, Path("image")).preview_data)
        children = tree["root"]["children"][0]["children"]
        sprite = children[1]
        self.assertEqual(sprite["type"], "sprite")
        self.assertEqual(sprite["props"]["elapsed"], 550)
        particles = [child for child in children if child["key"].startswith("dust-")]
        self.assertEqual(len(particles), 2)
        for puff in particles:
            timeline = puff["props"]["timeline"]
            self.assertEqual(timeline["playbackId"], sprite["props"]["playbackId"])
            self.assertEqual(timeline["elapsed"], 550)
            self.assertTrue(timeline["loop"])
            self.assertEqual(timeline["keyframes"][-1]["opacity"], 0)

    def test_timer_runs_in_wox_and_freezes_at_the_recorded_finish(self):
        session = Session(state="running", started_at=100)
        running = timer_node(session, "Elapsed", 159.9)["children"][1]
        self.assertEqual(running["type"], "timer")
        self.assertTrue(running["props"]["running"])
        self.assertEqual(running["props"]["elapsed"], 59900)
        session.state, session.ended_at = "completed", 161
        stopped = timer_node(session, "Elapsed", 999)["children"][1]
        self.assertFalse(stopped["props"]["running"])
        self.assertEqual(stopped["props"]["elapsed"], 61000)

    def test_scene_uses_local_assets_and_no_background_fill_or_log_list(self):
        state = Session(state="running")
        state.feed("➤ Browser caches")
        state.feed("✓ Older cache (1MB)")
        state.feed('→ Current <cache> "中文" (2MB)')
        result = render(state, HouseScene(), LABELS, Path("image"))
        self.assertEqual(result.preview_type, "woxview")
        tree = json.loads(result.preview_data)
        self.assertNotIn("Older cache", result.preview_data)
        self.assertIn("Current <cache>", result.preview_data)

        def check(node):
            self.assertNotIn("backgroundColor", node.get("props", {}).get("style", {}))
            if node["type"] in ("image", "sprite"):
                path = Path(node["props"]["source"]["imageData"])
                self.assertTrue(path.is_absolute() and path.is_file())
                # The host sniffs SVG in the first 512 bytes of absolute assets.
                self.assertNotIn(b"<svg", path.read_bytes()[:512])
            for child in node.get("children", []):
                check(child)

        check(tree["root"])
