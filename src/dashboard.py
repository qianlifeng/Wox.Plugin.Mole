"""Read-only presentation of a Mole command session."""

import html
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from wox_plugin import WoxPreview, WoxPreviewType

LABELS = {
    "ready": "Ready",
    "running": "Working",
    "completed": "Completed",
    "failed": "Failed",
    "stopped": "Stopped",
    "unavailable": "Not connected yet",
    "ready_hint": "Enter to clean · Ctrl+Enter to preview",
    "unavailable_hint": "This command's in-app workflow has not been connected yet.",
    "preview": "Preview only",
    "cleanup": "Cleanup",
    "estimated": "Potential space",
    "freed": "Space freed",
    "unknown": "Not reported",
    "recent": "LATEST OUTPUT",
    "no_output": "Waiting to start",
    "truncated": "Older output was truncated.",
    "stopped_hint": "Stopped. Changes already made are not undone.",
    "preview_done": "Preview complete. No files were removed.",
    "cleanup_done": "Cleanup finished.",
    "starting": "Starting Mole…",
    "details": "Cleanup details",
    "tracked": "Tracked cleanup",
    "issues": "Mole reported errors. Check the output.",
    "elapsed": "Elapsed",
    "quiet": "No new output for {seconds}s · Mole is still running",
    "scan_complete": "Scan complete",
    "clean_complete": "Cleanup complete",
    "scanning": "Scanning",
    "cleaning": "Cleaning",
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SIZE = r"[\d.]+\s*(?:TB|GB|MB|KB|B)"


@dataclass
class Session:
    cards: list[dict] = field(default_factory=list)
    state: str = "ready"
    dry_run: bool = True
    stage: str = ""
    message: str = ""
    estimated: str = ""
    freed: str = ""
    tracked: str = ""
    errors: bool = False
    in_summary: bool = False
    sections: list[tuple[str, deque[str]]] = field(default_factory=list)
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1500))
    truncated: bool = False
    output_revision: int = 0
    section_offset: int = 0
    row_offsets: dict[int, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    last_output_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None

    def feed(self, line: str) -> None:
        line = ANSI.sub("", line)
        line = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line).strip()
        if not line:
            return
        self.output_revision += 1
        self.last_output_at = time.monotonic()
        # Bound both line count and line size.
        if len(self.logs) == self.logs.maxlen or len(line) > 2000:
            self.truncated = True
        self.logs.append(line[:2000])
        self.message = line[:2000]
        separator = bool(re.fullmatch(r"[=\-─━═_]{3,}", line))
        if re.match(r"^(?:Cleanup complete|Dry run complete\b|Cleanup summary\b)", line, re.I):
            self.in_summary = True
        is_error = bool(line.startswith(("☻", "✗", "✘")) or re.search(r"\bCleanup error:", line, re.I))
        if line.startswith("➤"):
            self.in_summary = False
            self.stage = line[1:].strip()
            if len(self.sections) >= 100:
                self.sections.pop(0)
                self.row_offsets.pop(self.section_offset, None)
                self.section_offset += 1
                self.truncated = True
            self.sections.append((self.stage, deque(maxlen=100)))
        elif self.sections and not separator and (not self.in_summary or is_error) and not re.match(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏].*Scanning", line):
            rows = self.sections[-1][1]
            if not rows or rows[-1] != line[:2000]:
                if len(rows) == rows.maxlen:
                    self.truncated = True
                    key = self.section_offset + len(self.sections) - 1
                    self.row_offsets[key] = self.row_offsets.get(key, 0) + 1
                rows.append(line[:2000])
        # Mole's Windows runner catches cleanup errors and can still exit zero.
        if is_error:
            self.errors = True
        if match := re.search(rf"Potential space:\s*({SIZE})", line, re.I):
            self.estimated = match[1]
        if not self.dry_run and (match := re.search(rf"Space freed:\s*({SIZE})", line, re.I)):
            self.freed = match[1]
        if not self.dry_run and (match := re.search(rf"Tracked cleanup:\s*({SIZE})", line, re.I)):
            self.tracked = match[1]

    def finish(self, code: int, stopped: bool = False) -> None:
        self.ended_at = time.monotonic()
        self.state = "stopped" if stopped else ("failed" if code or self.errors else "completed")
        if code and not stopped:
            self.feed(f"Mole exited with code {code}.")


def snapshot(command: str, session: Session, labels: dict[str, str]) -> dict:
    """Build a JSON-only view model; output text never becomes JavaScript or markup."""

    def label(key: str) -> str:
        return labels.get(key, LABELS[key])

    state = session.state
    active = state == "running"
    status = label(state)
    message = session.message or labels.get("ready_hint", LABELS["ready_hint"])
    if state == "unavailable":
        message = labels.get("unavailable_hint", LABELS["unavailable_hint"])
    if state == "stopped":
        message = labels.get("stopped_hint", LABELS["stopped_hint"])
    if state == "running" and not session.message:
        message = labels.get("starting", LABELS["starting"])
    if state == "completed":
        key = "preview_done" if session.dry_run else "cleanup_done"
        message = labels.get(key, LABELS[key])
    if state == "failed" and session.errors:
        message = labels.get("issues", LABELS["issues"])
    quiet = int(time.monotonic() - session.last_output_at)
    if active and quiet >= 10:
        message = labels.get("quiet", LABELS["quiet"]).format(seconds=quiet)
    elif active and session.message.startswith("➤"):
        message = ""
    elapsed = int((session.ended_at or time.monotonic()) - session.started_at)
    duration = f"{elapsed // 60}:{elapsed % 60:02d}"
    amount = session.estimated if session.dry_run else (session.freed or session.tracked)
    metric = "estimated" if session.dry_run else ("freed" if session.freed else "tracked")
    if active:
        status = label("scanning" if session.dry_run else "cleaning")
    elif state == "completed":
        status = label("scan_complete" if session.dry_run else "clean_complete")

    if command != "clean":
        if state == "ready":
            message = {
                "optimize": "Enter to optimize · Ctrl+Enter to preview",
                "analyze": "Enter to analyze · Add a directory after the command",
                "status": "Enter to start monitoring",
            }.get(command, "Enter to start")
        elif state == "completed":
            message = session.message if command == "analyze" else "Mole finished."
        status = (
            {"optimize": "Optimizing", "analyze": "Analyzing", "status": "Monitoring"}.get(command, label("running"))
            if active
            else label(state)
        )
        amount = ""

    if command == "clean" and (state == "completed" or (active and quiet < 10)):
        message = ""

    sections = []
    for index, (title, rows) in enumerate(session.sections):
        section_id = session.section_offset + index
        items = []
        for row_index, row in enumerate(rows):
            error = row.startswith(("☻", "✗", "✘"))
            size = re.search(rf"\([^()]*?({SIZE})(?:\s+dry)?\)", row, re.I)
            description = re.sub(r"^[✓→☻✗✘]\s*", "", row)
            if size:
                description = re.sub(r"\s*\([^()]*\)\s*$", "", description)
            items.append(
                {
                    "id": str(session.row_offsets.get(section_id, 0) + row_index),
                    "text": description,
                    "value": size[1] if size else ("✕" if error else "✓" if row.startswith("✓") else ""),
                    "error": error,
                }
            )
        sections.append(
            {
                "id": str(section_id),
                "title": title,
                "developer": "developer" in title.lower(),
                "current": active and command not in ("status", "analyze") and index == len(session.sections) - 1,
                "items": items,
            }
        )
    return {
        "session": str(session.started_at),
        "revision": session.output_revision,
        "state": state,
        "status": status,
        "message": message,
        "amount": (amount or "—") if command == "clean" else "",
        "metric": label(metric) if command == "clean" else "",
        "caption": session.stage if active and session.stage else status,
        "sections": sections,
        "command": command,
        "cards": session.cards,
        "tag": label("preview") if command in ("clean", "optimize") and session.dry_run else "",
        "elapsed": label("elapsed") + " " + duration if state != "ready" else "",
        "fallback": (session.message or label("no_output")) if not sections else "",
        "truncated": label("truncated") if session.truncated else "",
    }


def script_update(session: Session, labels: dict[str, str], command: str = "clean") -> WoxPreview:
    """Send a one-shot snapshot without regenerating or navigating the document."""
    source = "window.updateCleanup(" + json.dumps(snapshot(command, session, labels), ensure_ascii=True) + ");"
    return WoxPreview(preview_type=WoxPreviewType.WEBVIEW, preview_data=json.dumps({"updateMode": "script", "script": source}))


def render(command: str, session: Session, labels: dict[str, str], colors: dict[str, str]) -> WoxPreview:
    def label(key: str) -> str:
        return html.escape(labels.get(key, LABELS[key]))

    def color(key: str, default: str) -> str:
        value = colors.get(key, "")
        return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default

    view = snapshot(command, session, labels)
    state, status, message = view["state"], html.escape(view["status"]), view["message"]
    active = state == "running"
    amount = view["amount"]
    duration = view["elapsed"]

    def color_value(key: str, default: str) -> str:
        return color(key, default)

    background = color_value("background", "#1c1c1e")
    text_color = color_value("text", "#f4f4f5")
    secondary = color_value("secondary_text", "#a1a1aa")
    border = color_value("border", "#353538")
    rgb = background[1:]
    light = sum(int(rgb[i : i + 2], 16) * w for i, w in zip((0, 2, 4), (0.2126, 0.7152, 0.0722))) > 150
    green, blue = ("#087b55", "#2466af") if light else ("#62d9ac", "#91c5ff")
    red, amber = ("#c83d4c", "#987015") if light else ("#f17c88", "#e6b862")
    tone = "var(--tone)"

    def icon(path: str) -> str:
        return (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + "</svg>"
        )

    check = '<path d="m5 12 4 4 10-10"/>'
    cross = '<path d="m6 6 12 12M18 6 6 18"/>'
    mark = check if state == "completed" else (cross if state == "failed" else '<path d="m9 5 10 7-10 7z"/>')
    if active:
        mark = '<path d="M12 3a9 9 0 1 1-9 9"/>'
    elif state in ("stopped", "unavailable"):
        mark = '<path d="M6 12h12"/>'
    details = ""
    selected = session.sections
    for index, (title, rows) in enumerate(selected):
        current = active and index == len(selected) - 1
        category = (
            '<path d="m4 6 6 6-6 6m10 0h6"/>'
            if "developer" in title.lower()
            else '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 10h16M10 10v10"/>'
        )
        items = ""
        for row_index, row in enumerate(rows):
            error = row.startswith(("☻", "✗", "✘"))
            size = re.search(rf"\([^()]*?({SIZE})(?:\s+dry)?\)", row, re.I)
            description = re.sub(r"^[✓→☻✗✘]\s*", "", row)
            if size:
                description = re.sub(r"\s*\([^()]*\)\s*$", "", description)
            value = html.escape(size[1]) if size else (icon(cross if error else check) if error or row.startswith("✓") else "")
            items += (
                f'<div class="item" data-id="{session.row_offsets.get(session.section_offset + index, 0) + row_index}"><p>{html.escape(description)}</p>'
                f'<div class="value {"error" if error else ""}">{value}</div></div>'
            )
        if not rows and current:
            items = f'<div class="item"><p>{status}…</p><span class="dot"></span></div>'
        details += (
            f'<section id="category-{session.section_offset + index}" class="{"current" if current else ""}">'
            f'<div class="section-heading"><div class="category-icon">{icon(category)}</div><h3>{html.escape(title)}</h3></div>'
            f'<div class="items">{items}</div></section>'
        )
    tags = f'<span class="tag" id="mode">{html.escape(view["tag"])}</span>' if view["tag"] else '<span class="tag" id="mode" hidden></span>'
    fallback = html.escape(session.message or labels.get("no_output", LABELS["no_output"]))
    caption = html.escape(session.stage) if active and session.stage else status
    initial_json = json.dumps(view, ensure_ascii=True).replace("<", "\\u003c")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body{{ --tone:{green}}}body.failed{{--tone:{red}}}body.stopped{{--tone:{amber}}}
    *{{box-sizing:border-box}}html,body{{height:100%;margin:0;overflow:hidden}}
    body{{background:{background};color:{text_color};font:14px system-ui,-apple-system,"Segoe UI",sans-serif;
    padding:14px 20px 12px;display:flex;flex-direction:column}}
    .hero{{display:flex;align-items:center;gap:12px;min-height:60px;flex-shrink:0}}
    svg{{width:24px;height:24px}}.status-icon{{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;
    flex-shrink:0;color:{tone};background:color-mix(in srgb,{tone} 15%,transparent)}}
    .completed .status-icon{{background:{tone};color:{background}}}.running .status-icon svg{{animation:spin 1.4s linear infinite;animation-delay:0s}}
    .headline{{min-width:0;flex:1}}h2{{font-size:21px;letter-spacing:-.5px;margin:0 0 4px}}
    p{{margin:0;color:{secondary};line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .headline p{{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:12px}}
    .headline p:empty{{display:none}}
    body[data-command="clean"] .caption{{display:none}}
    body[data-command="clean"] .hero{{margin-bottom:10px}}
    .metric{{text-align:right;flex-shrink:0;max-width:32%}}.metric strong{{font-size:26px;color:{tone};font-weight:650;letter-spacing:-1px}}
    .metric p{{font-size:12px;margin-top:4px}}
    .caption{{font-size:12px;margin:6px 0 8px;color:{secondary};flex-shrink:0}}
    /* Reserve the same native scrollbar gutter in the fixed header and scrolling body. */
    .hero,.caption,.details{{padding-right:14px;scrollbar-gutter:stable;scrollbar-width:thin}}
    .hero,.caption{{overflow:hidden}}
    .details{{min-height:0;flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-color:{border} transparent}}
    section{{padding:6px 0;border-top:1px solid {border}}}
    .section-heading{{display:flex;align-items:center;gap:8px}}
    .items{{margin-left:34px}}.item{{display:flex;align-items:flex-start;gap:10px;padding:2px 0}}
    .item p{{flex:1;min-width:0;white-space:normal;overflow-wrap:anywhere;font-size:12px}}
    .category-icon{{width:26px;height:26px;border-radius:6px;display:grid;place-items:center;flex-shrink:0;
    color:{blue};background:color-mix(in srgb,{blue} 12%,transparent)}}.category-icon svg{{width:18px;height:18px}}
    .current .category-icon{{color:{green};background:color-mix(in srgb,{green} 12%,transparent)}}
    .category-text{{min-width:0;flex:1}}h3{{font-size:14px;font-weight:600;margin:0}}
    .category-text p{{font-size:12px}}.value{{color:{blue};font-weight:600;white-space:nowrap}}.value svg{{width:18px;height:18px;color:{secondary}}}
    .value.error,.value.error svg{{color:{red}}}.dot{{display:block;width:7px;height:7px;border-radius:50%;background:{green}}}
    .tag{{font-size:10px;margin-left:8px;vertical-align:middle;padding:2px 7px;border-radius:12px;border:1px solid color-mix(in srgb,{tone} 45%,transparent);color:{tone}}}.output{{margin-left:auto}}
    .empty{{padding:8px 0}}[hidden]{{display:none!important}}@keyframes spin{{to{{transform:rotate(360deg)}}}}
    @media(max-height:360px){{body{{padding:12px 18px}}.hero{{min-height:60px}}section{{padding:7px 0}}h2{{font-size:20px}}}}
    @media(max-width:480px){{.metric strong{{font-size:22px}}h2{{font-size:19px}}.hero{{gap:10px}}.status-icon{{width:40px;height:40px}}}}
    @media(prefers-reduced-motion:reduce){{*,*::after{{animation:none!important}}}}
    body[data-command="status"] .caption{{display:none}}
    body[data-command="status"] .hero{{display:none}}
    body[data-command="status"] .details{{padding-inline:8px;scrollbar-gutter:stable both-edges}}
    .status-notice{{font-size:12px;color:{red};margin:10px 0 0}}
    body[data-command="status"] .status-icon{{width:30px;height:30px}}
    body[data-command="status"] h2{{font-size:18px}}
    .status-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-content:start}}
    .status-card{{padding:14px;border:1px solid {border};border-radius:12px;min-width:0;
    background:color-mix(in srgb,{text_color} 4%,{background});--card-color:{blue}}}
    .status-card h3{{color:var(--card-color);font-size:12px;margin-bottom:9px}}
    .status-number{{font-size:26px;font-weight:650;letter-spacing:-.7px;line-height:1.2;font-variant-numeric:tabular-nums}}
    .status-note{{font-size:11px;margin:5px 0 10px;min-height:16px}}
    .status-bar{{height:5px;border-radius:5px;background:color-mix(in srgb,var(--card-color) 15%,transparent);overflow:hidden;margin:8px 0}}
    .status-fill{{height:100%;background:var(--card-color);border-radius:inherit;transition:width .6s ease}}
    .status-rows{{font-size:11px;line-height:1.7;max-height:106px;overflow-y:auto;padding-right:6px;scrollbar-width:thin}}
    .status-row{{overflow-wrap:anywhere;color:{secondary}}}
    .status-card[data-key="memory"]{{--card-color:{green}}}
    .status-card[data-key="disks"]{{--card-color:{amber}}}
    .status-card[data-key="network"]{{--card-color:#48bcbc}}
    .status-card[data-key="system"]{{--card-color:{green}}}
    @media(max-width:620px){{.status-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(max-width:360px){{.status-grid{{grid-template-columns:1fr}}}}
    </style></head><body class="{state}" data-command="{command}">
    <div class="hero"><div class="status-icon" id="status-icon">{icon(mark)}</div><div class="headline"><h2><span id="status">{status}</span>{tags}</h2><p id="message">{html.escape(message)}</p></div>
    <div class="metric"><strong id="amount">{html.escape(amount) if amount else ('—' if command == 'clean' else '')}</strong><p id="elapsed">{html.escape(duration)}</p></div></div>
    <div class="caption" id="caption">{caption}</div>
    <div class="details" id="items" data-revision="{session.output_revision}" data-session="{session.started_at:.6f}">
    {(details if command != "status" else "") or '<p class="empty">' + fallback + '</p>'}
    {"<p>" + label("truncated") + "</p>" if session.truncated else ""}</div>
    <script>{Path(__file__).with_name("cleanup.js").read_text(encoding="utf-8")}
    window.updateCleanup({initial_json});
    </script>
    </body></html>"""
    return WoxPreview(preview_type=WoxPreviewType.WEBVIEW, preview_data=json.dumps({"html": page}))
