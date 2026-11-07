"""Bounded cleanup state parsed from Mole's own output."""

import re
import time
from collections import deque
from dataclasses import dataclass, field

LABELS = {
    "ready": "Ready to clean",
    "running": "Cleaning",
    "downloading": "Downloading Mo",
    "download_lookup": "Finding the latest release",
    "download_checksum": "Downloading checksums",
    "download_verify": "Verifying the download",
    "download_install": "Installing Mo",
    "completed": "Cleanup complete",
    "failed": "Cleanup failed",
    "stopped": "Cleanup stopped",
    "ready_hint": "Press Enter to send Mole in to clean.",
    "starting": "Mole is getting started…",
    "working": "Tidying up…",
    "cleanup_done": "Your space feels lighter.",
    "stopped_hint": "Stopped. Changes already made are not undone.",
    "issues": "Mole reported an error. Copy the output from Actions for details.",
    "elapsed": "Elapsed",
    "freed": "Space freed",
    "tracked": "Cleaned · reported by Mole",
    "unknown": "Mole did not report a cleanup amount.",
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SIZE = r"\d+(?:[.,]\d+)*\s*(?:TB|GB|MB|KB|B)"


@dataclass
class Session:
    state: str = "ready"
    stage: str = ""
    current_item: str = ""
    message: str = ""
    freed: str = ""
    tracked: str = ""
    errors: bool = False
    in_summary: bool = False
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1500))
    truncated: bool = False
    output_revision: int = 0
    started_at: float = field(default_factory=time.monotonic)
    last_output_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    download_phase: str = ""
    download_received: int = 0
    download_total: int = 0
    download_label: str = ""

    def feed(self, line: str) -> None:
        line = ANSI.sub("", line)
        line = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line).strip()
        if not line:
            return
        self.output_revision += 1
        self.last_output_at = time.monotonic()
        if len(self.logs) == self.logs.maxlen or len(line) > 2000:
            self.truncated = True
        line = line[:2000]
        self.logs.append(line)
        self.message = line
        if re.match(r"^(?:Cleanup complete|Cleanup summary\b)", line, re.I):
            self.in_summary = True
        if line.startswith("➤"):
            self.stage = line[1:].strip()
            self.current_item = self.stage
            self.in_summary = False
        elif not self.in_summary and line.startswith(("✓", "→")):
            self.current_item = re.sub(r"\s*\([^()]*\)\s*$", "", line[1:].strip())
        if line.startswith(("☻", "✗", "✘")) or re.search(r"\bCleanup error:", line, re.I):
            self.errors = True
        if match := re.search(rf"Space freed:\s*({SIZE})", line, re.I):
            self.freed = match[1]
        if match := re.search(rf"Tracked cleanup:\s*({SIZE})", line, re.I):
            self.tracked = match[1]
        if self.in_summary and re.search(r"\bSystem was already clean;\s*no additional space freed\.", line, re.I):
            self.freed = "0MB"

    def finish(self, code: int, stopped: bool = False) -> None:
        self.ended_at = time.monotonic()
        self.state = "stopped" if stopped else ("failed" if code or self.errors else "completed")
        if code and not stopped:
            self.feed(f"Mole exited with code {code}.")


def format_bytes(amount: int) -> str:
    if amount >= 1024 * 1024 or amount == 0:
        return f"{amount / (1024 * 1024):.1f} MB"
    if amount >= 1024:
        return f"{amount / 1024:.0f} KB"
    return f"{amount} B"


def download_detail(session: Session, labels: dict[str, str]) -> str:
    phase = session.download_phase
    if phase == "archive":
        received = format_bytes(session.download_received)
        if session.download_total > 0:
            percent = min(100, session.download_received * 100 // session.download_total)
            detail = f"{received} / {format_bytes(session.download_total)} · {percent}%"
        elif session.download_received:
            detail = received
        else:
            detail = ""
        if session.download_label and detail:
            return f"{session.download_label} · {detail}"
        return detail or session.download_label or labels["download_lookup"]
    key = {
        "lookup": "download_lookup",
        "checksum": "download_checksum",
        "verify": "download_verify",
        "install": "download_install",
    }.get(phase, "download_lookup")
    return labels[key]


def summary(session: Session, labels: dict[str, str], now: float) -> dict[str, str]:
    state = session.state
    message = labels["ready_hint"]
    amount = ""
    if state == "downloading":
        message = download_detail(session, labels)
    elif state == "running":
        message = session.current_item or session.stage or labels["working"]
    elif state == "completed":
        amount = session.freed or session.tracked
        message = labels["freed" if session.freed else "tracked"] if amount else labels["unknown"]
        # Owner-managed tools may finish without reporting a measurable amount.
        # Keep the quantity row visible without inventing a zero or reusing a prior run.
        amount = amount or "— MB"
    elif state == "failed":
        message = labels["issues"] if session.errors else session.message or labels["issues"]
    elif state == "stopped":
        message = labels["stopped_hint"]
    elapsed = max(0, int((session.ended_at if session.ended_at is not None else now) - session.started_at))
    return {
        "title": labels[state],
        "message": message[:180],
        "amount": amount,
        "elapsed": "" if state == "ready" else f'{labels["elapsed"]} {elapsed // 60:02d}:{elapsed % 60:02d}',
        "clock": "" if state == "ready" else f"{elapsed // 60:02d}:{elapsed % 60:02d}",
    }
