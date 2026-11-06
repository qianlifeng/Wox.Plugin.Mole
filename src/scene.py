"""Doorway choreography; WoxView owns sprite frames, dust and motion timelines.

Coordinates are measured against the 1536x1024 house asset, at the mole's feet.
Only graph edges are traversed: random destinations never create wall crossings.
"""

import math
import random
from collections import deque
from enum import IntEnum

from .navigation import safe_position, safe_segment


class Facing(IntEnum):
    RIGHT = 0
    LEFT = 1
    BACK = 2
    FRONT = 3


POINTS = {
    "porch": (768, 923),
    "door": (768, 785),
    "hall": (760, 620),
    "junction": (730, 490),
    "bedroom_door": (725, 400),
    "bedroom": (728, 305),
    "living": (685, 555),
    # Center the full walking silhouette (including the broom) in the opening.
    "study_door": (894, 490),
    "study_threshold": (894, 400),
    "study_entry": (920, 370),
    # Finish passing the north end of the partition before turning south.
    "study_turn": (1064, 380),
    "study": (1150, 420),
    "right_room": (1165, 582),
}
EDGES = (
    ("porch", "door"),
    ("door", "hall"),
    ("hall", "junction"),
    ("junction", "bedroom_door"),
    ("bedroom_door", "bedroom"),
    ("junction", "living"),
    ("junction", "study_door"),
    ("study_door", "study_threshold"),
    ("study_threshold", "study_entry"),
    ("study_entry", "study_turn"),
    ("study_turn", "study"),
    ("study", "right_room"),
)
SPOTS = ("bedroom", "living", "study", "right_room", "hall")
SWEEP_FACING = {spot: Facing.LEFT if spot == "bedroom" else Facing.RIGHT for spot in SPOTS}


def route(start: str, end: str) -> list[str]:
    pending: deque[tuple[str, list[str]]] = deque([(start, [])])
    seen = {start}
    while pending:
        current, steps = pending.popleft()
        if current == end:
            return steps
        for a, b in EDGES:
            neighbor = b if a == current else a if b == current else None
            if neighbor and neighbor not in seen:
                seen.add(neighbor)
                pending.append((neighbor, [*steps, neighbor]))
    raise ValueError(f"No walking route from {start} to {end}")


class HouseScene:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.location = "porch"
        self.position: tuple[float, float] = POINTS["porch"]
        self.origin: tuple[float, float] = self.position
        self.destination = "porch"
        self.steps: list[str] = []
        self.mode = "idle"
        self.started = 0.0
        self.duration = 0.0
        self.deadline = 0.0
        self.facing = Facing.RIGHT
        self.motion_ms = 0
        self.motion_id = 0
        self.frozen = True
        self.returning = False

    def start(self, now: float) -> None:
        self.returning = False
        self.mode = "waiting"
        self.deadline = now + 0.3
        self.steps = route("porch", self.rng.choice(SPOTS))
        self.frozen = False

    def current_position(self, now: float) -> tuple[float, float]:
        if self.mode != "walking" or not self.duration:
            return self.position
        ratio = min(1.0, max(0.0, (now - self.started) / self.duration))
        return (
            self.origin[0] + (self.position[0] - self.origin[0]) * ratio,
            self.origin[1] + (self.position[1] - self.origin[1]) * ratio,
        )

    def _walk(self, now: float) -> None:
        destination = self.steps[0]
        position = POINTS[destination]
        dx = position[0] - self.position[0]
        dy = position[1] - self.position[1]
        if abs(dy) > abs(dx):
            facing = Facing.BACK if dy < 0 else Facing.FRONT
        else:
            facing = Facing.RIGHT if dx > 0 else Facing.LEFT
        if not safe_segment(self.position, position, facing):
            raise ValueError(f"Unsafe walking segment to {destination}")
        self.origin, self.position = self.position, position
        self.destination = self.steps.pop(0)
        self.facing = facing
        self.duration = max(0.1, math.dist(self.origin, self.position) / 135)
        self.started = now
        self.deadline = now + self.duration
        self.motion_ms = round(self.duration * 1000)
        self.motion_id += 1
        self.mode = "walking"

    def _return_home(self, now: float) -> None:
        if not self.returning:
            self.returning = True
            self.frozen = False
            if self.mode == "walking":
                # Finish the current doorway edge without jumping or cutting a
                # corner, then head outside instead of visiting another room.
                self.steps = route(self.destination, "porch")
            else:
                self.steps = route(self.location, "porch")
                self.deadline = now
        if self.frozen:
            return
        if now >= self.deadline:
            if self.mode == "walking":
                self.location = self.destination
            if self.steps:
                self._walk(now)
            else:
                self.mode = "idle"
                self.motion_ms = 0
                self.facing = Facing.FRONT
                self.frozen = True
                return

    def update(self, now: float, state: str) -> None:
        if state == "completed":
            self._return_home(now)
            return
        if state != "running":
            if not self.frozen:
                self.position = self.current_position(now)
                self.origin = self.position
                self.motion_ms = 0
                self.mode = "idle"
                self.frozen = True
            return
        if self.frozen:
            self.start(now)
        if now >= self.deadline:
            if self.mode == "walking":
                self.location = self.destination
            if self.steps:
                self._walk(now)
            elif self.mode == "sweeping":
                target = self.rng.choice([spot for spot in SPOTS if spot != self.location])
                self.steps = route(self.location, target)
                self._walk(now)
            else:
                facing = SWEEP_FACING[self.location]
                if not safe_position(self.position, facing, sweeping=True):
                    raise ValueError(f"Unsafe sweeping spot: {self.location}")
                self.mode = "sweeping"
                self.started = now
                self.deadline = now + self.rng.uniform(1.8, 3.2)
                self.motion_ms = 0
                self.motion_id += 1
                self.facing = facing
