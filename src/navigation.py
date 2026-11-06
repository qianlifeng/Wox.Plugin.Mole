"""Ground clearance is independent of whether a wall can hide an unsafe pose.

Polygons use the 1152 x 768 house coordinates. Floor-contact envelopes include
both walking frames and the carried broom. Sweeping additionally reserves the
entire visible stroke, so it never looks as if the brush is on a wall top.
No image decoding or collision work runs on the native animation frame clock.
"""

import math

from .occlusion import ART_SCALE, ART_WIDTH, WALLS, Point

DISPLAY_WIDTH = 460
SPRITE_SIZE = 56
FOOT_ANCHOR = 0.866
CELL_SCALE = SPRITE_SIZE * ART_WIDTH / DISPLAY_WIDTH / 192
CLEARANCE = 2.0  # Artwork pixels, in addition to the opaque silhouette envelope.

# Actual floor footprints, not the upward-projected visible faces used to draw
# occlusion. The long center partition and the L-shaped study wall are solid.
FLOOR_BLOCKS = (
    ((149, 316), (462, 316), (462, 336), (149, 336)),
    ((614, 139), (633, 139), (639, 335), (615, 335)),
    ((713, 318), (773, 318), (776, 336), (713, 336)),
    ((748, 335), (776, 335), (813, 603), (785, 603)),
    ((95, 602), (442, 602), (442, 646), (95, 646)),
    ((714, 602), (1045, 602), (1045, 646), (714, 646)),
    ((442, 634), (498, 592), (498, 610), (454, 654)),
    ((656, 592), (712, 635), (704, 654), (656, 610)),
)
FLOORS = (
    ((220, 139), (935, 139), (1040, 603), (100, 603)),
    ((421, 603), (731, 603), (731, 724), (421, 724)),
)

# Opaque atlas bounds: right, left, back, front. X includes body/tail and the
# broom; Y covers the contact band around the shared foot anchor, not the head.
WALK_CONTACT = ((45, 155, 142, 173), (50, 155, 149, 173), (56, 155, 151, 173), (57, 155, 136, 175))
SWEEP_CONTACT = ((34, 155, 173, 173), (19, 155, 159, 173))
SWEEP_VISIBLE = ((34, 41, 173, 173), (19, 41, 159, 173))


def envelope(feet: Point, bounds: tuple[int, int, int, int]) -> tuple[Point, ...]:
    x, y = (value * ART_SCALE for value in feet)
    left, top, right, bottom = bounds
    left_x = x + (left - 96) * CELL_SCALE - CLEARANCE
    right_x = x + (right - 96) * CELL_SCALE + CLEARANCE
    top_y = y + (top - 192 * FOOT_ANCHOR) * CELL_SCALE - CLEARANCE
    bottom_y = y + (bottom - 192 * FOOT_ANCHOR) * CELL_SCALE + CLEARANCE
    return ((left_x, top_y), (right_x, top_y), (right_x, bottom_y), (left_x, bottom_y))


def inside(point: Point, polygon: tuple[Point, ...]) -> bool:
    """Boundary-inclusive point test for the convex floor regions."""
    crosses = [(b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) for a, b in zip(polygon, (*polygon[1:], polygon[0]))]
    return all(value >= -1e-7 for value in crosses) or all(value <= 1e-7 for value in crosses)


def overlaps(a: tuple[Point, ...], b: tuple[Point, ...]) -> bool:
    """Separating axes for convex footprint polygons (touching is rejected)."""
    for polygon in (a, b):
        for start, end in zip(polygon, (*polygon[1:], polygon[0])):
            axis = start[1] - end[1], end[0] - start[0]
            pa = [x * axis[0] + y * axis[1] for x, y in a]
            pb = [x * axis[0] + y * axis[1] for x, y in b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def safe_position(feet: Point, facing: int, sweeping: bool = False) -> bool:
    if sweeping and facing not in (0, 1):
        return False
    contact = envelope(feet, SWEEP_CONTACT[facing] if sweeping else WALK_CONTACT[facing])
    if any(not any(inside(point, floor) for floor in FLOORS) for point in contact):
        return False
    if any(overlaps(contact, block) for block in FLOOR_BLOCKS):
        return False
    if sweeping:
        visible = envelope(feet, SWEEP_VISIBLE[facing])
        # Wall silhouettes can be concave. Their bounding rectangles provide a
        # deliberately conservative sweep exclusion zone, never a path collider.
        for wall in WALLS:
            left, top, right, bottom = wall.bounds
            if overlaps(visible, ((left, top), (right, top), (right, bottom), (left, bottom))):
                return False
    return True


def safe_segment(origin: Point, destination: Point, facing: int) -> bool:
    # Two artwork pixels between samples; the contact envelope already has a
    # two-pixel safety margin. Checked once per authored edge, not per frame.
    count = max(1, math.ceil(math.dist(origin, destination) * ART_SCALE / 2))
    return all(
        safe_position(
            (origin[0] + (destination[0] - origin[0]) * index / count, origin[1] + (destination[1] - origin[1]) * index / count),
            facing,
        )
        for index in range(count + 1)
    )
