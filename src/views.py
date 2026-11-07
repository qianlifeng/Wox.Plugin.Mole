"""Theme-native WoxView scene: a transparent house and native sprite and timeline playback."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast

from wox_plugin import WoxPreview, WoxPreviewType
from .dashboard import Session, summary
from .scene import Facing, HouseScene
from .occlusion import WALLS
from .navigation import DISPLAY_WIDTH, FOOT_ANCHOR, SPRITE_SIZE

WIDTH = DISPLAY_WIDTH
HEIGHT = WIDTH * 2 / 3
SPRITE = SPRITE_SIZE


def node(kind, key, children=None, style=None, **props):
    if style:
        props["style"] = style
    value = {"type": kind, "key": key, "props": props}
    if children is not None:
        value["children"] = children
    return value


def text(key, value, size=13, color="$textSecondary", weight="regular", **props):
    return node(
        "text",
        key,
        text=value,
        style={"fontSize": size, "color": color, "fontWeight": weight, "textAlign": "center", "maxLines": 1},
        **props,
    )


def image(key, path, width, height, **style):
    return node(
        "image",
        key,
        source={"imageType": "absolute", "imageData": str(path.resolve())},
        fit="fill",
        style={"width": width, "height": height, **style},
    )


def scene_node(scene: HouseScene, assets: Path, identity: str, now: float):
    base = int(scene.facing) * 4
    moving = scene.mode == "walking" and not scene.frozen
    sweeping = scene.mode == "sweeping" and not scene.frozen
    elapsed = max(0, round((now - scene.started) * 1000)) if moving or sweeping else 0
    playback = f"{identity}-{scene.motion_id}"
    x, y = scene.position
    position = {"translateX": x * WIDTH / 1536 - SPRITE / 2, "translateY": y * HEIGHT / 1024 - SPRITE * FOOT_ANCHOR}
    props: dict[str, Any] = {}
    if moving or sweeping:
        name = f"{'walk' if moving else 'sweep'}-{scene.facing}"
        props.update(
            clip=name,
            clips=[
                {
                    "name": name,
                    "frames": [base + 1, base + 2] if moving else [base, base + 3, base + 3, base],
                    "fps": 5 if moving else 4,
                    "loop": True,
                }
            ],
            playbackId=playback,
            elapsed=elapsed,
        )
    if moving:
        origin_x, origin_y = scene.origin
        props["timeline"] = {
            "playbackId": playback,
            "duration": scene.motion_ms,
            "elapsed": elapsed,
            "keyframes": [
                {
                    "at": 0,
                    "translateX": origin_x * WIDTH / 1536 - SPRITE / 2,
                    "translateY": origin_y * HEIGHT / 1024 - SPRITE * FOOT_ANCHOR,
                },
                {"at": scene.motion_ms, **position},
            ],
        }
    actor = node(
        "sprite",
        "actor-" + identity,
        source={"imageType": "absolute", "imageData": str((assets / "scene" / "mole-atlas.png").resolve())},
        columns=4,
        rows=4,
        frame=base,
        style={"width": SPRITE, "height": SPRITE, **position},
        **props,
    )
    return node(
        "stack",
        "house",
        [
            node(
                "stack",
                "background",
                [
                    image("floor", assets / "scene" / "house-base.png", WIDTH, HEIGHT),
                    *wall_nodes(scene, assets, playback, elapsed, rear=True),
                ],
                style={"width": WIDTH, "height": HEIGHT},
            ),
            actor,
            *dust_nodes(scene, assets, playback, elapsed),
            *wall_nodes(scene, assets, playback, elapsed),
        ],
        style={"width": WIDTH, "height": HEIGHT},
    )


def wall_nodes(scene: HouseScene, assets: Path, playback: str, elapsed: int, rear: bool = False):
    """Persistent foreground images share the actor's native movement clock."""
    result = []
    for index, wall in enumerate(WALLS):
        # Full-canvas atlas cells share the base image's exact destination box.
        # Small cropped image boxes snap independently on Windows, shifting
        # texture sampling at noninteger DPI. All cells share one GPU texture.
        overlay = node(
            "sprite",
            ("rear-" if rear else "wall-") + wall.name,
            source={"imageType": "absolute", "imageData": str((assets / "scene" / "walls-atlas.png").resolve())},
            columns=1,
            rows=len(WALLS),
            frame=index,
            style={"width": WIDTH, "height": HEIGHT, "opacity": int(wall.covers(scene.position)) ^ int(rear)},
        )
        if scene.mode == "walking" and not scene.frozen:
            frames = wall.keyframes(scene.origin, scene.position, scene.motion_ms)
            if rear:
                frames = [{"at": frame["at"], "opacity": 1 - frame["opacity"]} for frame in frames]
            if len({frame["opacity"] for frame in frames}) > 1:
                overlay["props"]["timeline"] = {
                    "playbackId": playback,
                    "duration": scene.motion_ms,
                    "elapsed": elapsed,
                    "keyframes": frames,
                }
        result.append(overlay)
    return result


def dust_nodes(scene: HouseScene, assets: Path, playback: str, elapsed: int):
    """Two fixed particles share the sweep clock; no per-frame nodes or snapshots."""
    result = []
    sweeping = scene.mode == "sweeping" and not scene.frozen
    direction = -1 if scene.facing == Facing.LEFT else 1
    x, y = scene.position
    for particle, size in enumerate((34, 24)):
        left = x * WIDTH / 1536 + direction * SPRITE * 0.3 - size / 2
        top = y * HEIGHT / 1024 - size * 0.58
        puff = image(f"dust-{particle}", assets / "scene" / "dust-puff.png", size, size, translateX=left, translateY=top, opacity=0)
        if sweeping:
            born = 500 + particle * 70
            puff["props"]["timeline"] = {
                "playbackId": playback,
                "duration": 1000,
                "loop": True,
                "elapsed": elapsed,
                "keyframes": [
                    {"at": 0, "scale": 0.5, "opacity": 0},
                    {"at": born, "scale": 0.5, "opacity": 0},
                    {"at": born + 55, "scale": 0.7, "opacity": 0.8},
                    {"at": 1000, "scale": 1.4, "opacity": 0, "translateX": left + direction * 14, "translateY": top - 8 - particle * 3},
                ],
            }
        result.append(puff)
    return result


def timer_node(session: Session, label: str, now: float):
    if session.state == "ready":
        return node("row", "elapsed", style={"height": 24})
    elapsed = max(0, round(((session.ended_at if session.ended_at is not None else now) - session.started_at) * 1000))
    return node(
        "row",
        "elapsed",
        [
            node("text", "elapsed-label", text=label, style={"fontSize": 12, "color": "$textSecondary"}),
            node(
                "timer",
                "clock",
                elapsed=elapsed,
                running=session.state in ("running", "downloading"),
                animate=True,
                playbackId=str(session.started_at),
                style={"fontSize": 16, "fontWeight": "semibold", "color": "$textSecondary"},
            ),
        ],
        style={"gap": 8, "height": 24, "alignItems": "center"},
    )


def render(session: Session, scene: HouseScene, labels: dict[str, str], assets: Path) -> WoxPreview:
    now = time.monotonic()
    view = summary(session, labels, now)
    tone = "$error" if session.state == "failed" else "$text"
    title = view["amount"] or view["title"]
    subtitle = view["title"] if view["amount"] else ("" if session.state == "completed" else view["message"])
    message_key = "message-" + hashlib.sha256(subtitle.encode()).hexdigest()[:12]
    content = [
        scene_node(scene, assets, str(session.started_at), now),
        node(
            "column",
            "caption",
            [
                text("title", title, 28 if view["amount"] else 22, tone, "semibold"),
                text(message_key, subtitle, enter={"type": "slideUp", "duration": 240}, exit={"type": "fade", "duration": 120}),
                timer_node(session, labels["elapsed"], now),
            ],
            style={"gap": 6, "alignItems": "center", "alignSelf": "stretch"},
        ),
    ]
    tree = {
        "version": 1,
        "root": node(
            "column",
            "mole-scene",
            content,
            style={
                "padding": {"horizontal": 12, "vertical": 8},
                "gap": 6,
                "alignItems": "center",
                "justifyContent": "center",
            },
        ),
    }
    # Published SDK 0.0.97 serializes this wire value but does not yet name the
    # enum member. WoxView is a host capability; no WebView fallback is involved.
    return WoxPreview(
        preview_type=cast(WoxPreviewType, "woxview"), preview_data=json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    )
