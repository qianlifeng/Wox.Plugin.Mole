"""Wall silhouettes and ground-depth tests for the fixed house illustration.

Artwork geometry uses the shipped 1152 x 768 pixels. Choreography continues to
use 1536 x 1024 foot coordinates. Silhouettes include wall tops, visible faces
and door panels; ground tests are separate. The exporter copies original RGB.
"""

from dataclasses import dataclass

ART_WIDTH = 1152
ART_HEIGHT = 768
ART_SCALE = ART_WIDTH / 1536
Point = tuple[float, float]


@dataclass(frozen=True)
class Wall:
    name: str
    outline: tuple[Point, ...]
    # Any negative half-plane places the feet behind this wall. The L-shaped
    # study partition has both a north-facing end and a west-facing long side.
    planes: tuple[tuple[float, float, float], ...]

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs, ys = zip(*self.outline)
        return int(min(xs)) - 2, int(min(ys)) - 2, int(max(xs)) + 3, int(max(ys)) + 3

    def depths(self, feet: Point) -> tuple[float, ...]:
        x, y = (value * ART_SCALE for value in feet)
        return tuple(a * x + b * y + c for a, b, c in self.planes)

    def covers(self, feet: Point) -> bool:
        return any(depth < 0 for depth in self.depths(feet))

    def keyframes(self, origin: Point, destination: Point, duration: int) -> list[dict[str, int]]:
        """Native, effectively discrete depth changes in the clear door openings.

        Wox timelines interpolate linearly, so use a 1 ms transition. Route
        clearance is checked against every opaque walking pose at each switch;
        no character pixels overlap the toggled wall there. This keeps depth
        synchronized with movement even when no plugin snapshots are published.
        """
        before, after = self.depths(origin), self.depths(destination)
        frames = {0: int(self.covers(origin)), duration: int(self.covers(destination))}
        crossings = sorted({a / (a - b) for a, b in zip(before, after) if a != b and 0 < a / (a - b) < 1})
        for ratio in crossings:
            states = [int(any(a + (b - a) * t < 0 for a, b in zip(before, after))) for t in (ratio - 1e-7, ratio + 1e-7)]
            if states[0] == states[1]:
                continue
            at = max(1, min(duration, round(ratio * duration)))
            frames[at - 1], frames[at] = states
        return [{"at": at, "opacity": opacity} for at, opacity in sorted(frames.items())]


WALLS = (
    Wall(
        "bedroom",
        ((156, 265), (456, 265), (459, 269), (459, 282), (462, 286), (462, 335), (149, 335)),
        ((0, 1, -335),),
    ),
    Wall(
        "center",
        ((612, 62), (629, 62), (635, 269), (642, 269), (638, 334), (615, 334)),
        ((0, 1, -335),),
    ),
    Wall(
        "study",
        ((719, 270), (770, 270), (774, 273), (808, 510), (774, 510), (748, 335), (713, 335), (718, 285)),
        ((0, 1, -335), (-1, 26 / 174, 748 - 335 * 26 / 174)),
    ),
    Wall(
        "front-left",
        (
            (73, 527),
            (100, 509),
            (440, 509),
            (455, 490),
            (482, 468),
            (509, 453),
            (516, 452),
            (519, 478),
            (498, 493),
            (498, 604),
            (454, 653),
            (442, 654),
            (440, 644),
            (105, 645),
            (91, 638),
        ),
        ((0, 1, -654),),
    ),
    Wall(
        "front-right",
        (
            (647, 453),
            (654, 452),
            (680, 466),
            (704, 489),
            (720, 509),
            (1039, 509),
            (1068, 527),
            (1045, 633),
            (1038, 642),
            (711, 644),
            (709, 652),
            (703, 654),
            (656, 607),
            (656, 498),
            (645, 481),
        ),
        ((0, 1, -654),),
    ),
)
