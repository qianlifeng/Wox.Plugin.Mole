"""Mole-backed command sessions; no independent cleanup or filesystem scanner."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from .dashboard import Session
from .runtime import MoleRuntime


def size(value):
    amount = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(amount) < 1024 or unit == "TB":
            return f"{amount:.1f}{unit}"
        amount /= 1024


def status_cards(data):
    """Use measured percentages only; unavailable values remain unknown."""

    def percent(value):
        return f"{value:.1f}%" if isinstance(value, (int, float)) else "—"

    def card(key, title, value, note="", rows=None, usage=None):
        return dict(
            key=key,
            title=title,
            value=value,
            note=note,
            rows=rows or [],
            usage=max(0, min(100, usage)) if isinstance(usage, (int, float)) else None,
        )

    cpu, memory = data.get("cpu") or {}, data.get("memory") or {}
    disks, network, processes = data.get("disks") or [], data.get("network") or [], data.get("top_processes") or []
    total = sum(d.get("total", 0) for d in disks)
    used = sum(d.get("used", 0) for d in disks)
    rx = sum(n.get("rx_rate_mbs", 0) for n in network)
    tx = sum(n.get("tx_rate_mbs", 0) for n in network)
    return [
        card(
            "cpu",
            "CPU",
            percent(cpu.get("usage")),
            "Current usage",
            usage=cpu.get("usage"),
            rows=[str((data.get("hardware") or {}).get("cpu_model", ""))],
        ),
        card(
            "memory",
            "Memory",
            size(memory["used"]) if "used" in memory else "—",
            "of " + size(memory["total"]) if "total" in memory else "Not reported",
            usage=memory.get("used_percent"),
            rows=[
                "Used · " + percent(memory.get("used_percent")),
                "Available · " + size(memory["available"]) if "available" in memory else "",
            ],
        ),
        card(
            "disks",
            "Disk space",
            size(total - used) if total else "—",
            "Available across reported disks",
            usage=used / total * 100 if total else None,
            rows=[f'{d.get("mount", "")} · {size(d.get("used"))} / {size(d.get("total"))}' for d in disks],
        ),
        card(
            "network",
            "Network",
            f"{rx:.2f} MB/s" if network else "—",
            "Download",
            rows=[f"Upload · {tx:.2f} MB/s"] + [str(n.get("name", "")) for n in network],
        ),
        card(
            "processes",
            "Processes",
            str(data.get("procs", "—")),
            "Reported by Mole",
            rows=[f'{p.get("name", "")} · {p.get("cpu", 0):.1f}% · {size(p.get("memory_bytes"))}' for p in processes[:8]],
        ),
        card(
            "system",
            "System health",
            str(data.get("health_score", "—")),
            str(data.get("health_score_msg", "")),
            rows=[str(data.get("platform", "")), "Uptime · " + str(data.get("uptime", "—"))],
        ),
    ]


def load_report(session, command, data):
    """Replace metric snapshots rather than accumulating an endless status log."""
    if not isinstance(data, dict):
        raise ValueError("Mole returned an invalid JSON report")
    sections = []
    if command == "analyze":
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("Mole returned invalid disk entries")
        rows = []
        for entry in sorted(entries, key=lambda e: e.get("size", 0), reverse=True):
            completeness = entry.get("scan_status", "complete")
            amount = "Unknown" if completeness == "unavailable" else size(entry.get("size"))
            suffix = " · Partial" if completeness == "partial" else ""
            rows.append(f'{entry.get("name", entry.get("path", ""))} · {amount}{suffix}')
        sections.append((str(data.get("path") or "Disk overview"), rows))
        session.message = (
            "Scan complete" if data.get("scan_status", "complete") == "complete" else "Partial scan · Some sizes could not be measured"
        )
    else:
        cpu, memory = data.get("cpu", {}), data.get("memory", {})
        sections = [
            (
                "System",
                [
                    str(data.get("host", "")),
                    f'Uptime · {data.get("uptime", "—")}',
                    f'Health · {data.get("health_score", "—")} · {data.get("health_score_msg", "")}',
                ],
            ),
            ("CPU", [f'Usage · {cpu.get("usage", 0):.1f}%']),
            ("Memory", [f'{size(memory.get("used"))} / {size(memory.get("total"))} · {memory.get("used_percent", 0):.1f}%']),
            ("Disks", [f'{d.get("mount", "")} · {size(d.get("used"))} / {size(d.get("total"))}' for d in data.get("disks") or []]),
            (
                "Network",
                [
                    f'{n.get("name", "")} · ↓ {n.get("rx_rate_mbs", 0):.2f} MB/s · ↑ {n.get("tx_rate_mbs", 0):.2f} MB/s'
                    for n in data.get("network") or []
                ],
            ),
            (
                "Processes",
                [
                    f'{p.get("name", "")} · CPU {p.get("cpu", 0):.1f}% · {size(p.get("memory_bytes"))}'
                    for p in (data.get("top_processes") or [])[:20]
                ],
            ),
        ]
        session.message = str(data.get("host", "")) + " · Uptime " + str(data.get("uptime", "—"))
        session.cards = status_cards(data)
    session.sections = [(title, deque(rows[:100], maxlen=100)) for title, rows in sections]
    session.truncated = any(len(rows) > 100 for _, rows in sections)
    session.output_revision += 1
    session.last_output_at = time.monotonic()


class CommandSession(MoleRuntime):
    def __init__(self, command, publish):
        self.command = command
        self.publish = publish
        self.session = Session(dry_run=False)
        self.task = None
        self.process = None
        self.path = ""
        self.stopped = False

    def argv(self):
        executable = self._find_mo_executable()
        if not executable:
            raise OSError("Mole CLI was not found. Install Mole and restart Wox.")
        args = [executable, self.command]
        if self.command == "analyze":
            args += ["--json", self.path or os.path.expanduser("~")]
        elif self.command == "status":
            args += ["--watch", "--interval=2s"]
        elif self.session.dry_run:
            args += ["--dry-run"]
        if sys.platform == "win32":
            root = Path(executable).resolve().parent
            binary = root / "bin" / (self.command + ".exe")
            # Windows' PowerShell analyze wrapper does not forward --json.
            if self.command in ("analyze", "status") and binary.is_file():
                return [str(binary), *args[2:]]
            script = root / "mole.ps1"
            if script.is_file():
                args[0] = str(script)
            literal = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
            return [
                self._powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); try { "
                + literal
                + "; exit $LASTEXITCODE } catch { Write-Output $_; exit 1 }",
            ]
        return args

    async def spawn(self, args):
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "NO_COLOR": "1", "MOLE_NO_COLOR": "1", "MOLE_DRY_RUN": "1" if self.session.dry_run else "0"},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
            limit=4 * 1024 * 1024,
        )

    async def start(self, dry_run=False, path=""):
        if self.task and not self.task.done():
            return
        self.session = Session(state="running", dry_run=dry_run)
        if self.command == "status":
            self.session.cards = status_cards({})
        self.path, self.stopped = path, False
        self.task = asyncio.create_task(self.run())
        await self.publish()

    async def run(self):
        async def tick():
            while True:
                await asyncio.sleep(1)
                try:
                    await self.publish()
                except Exception:
                    pass  # A transient preview failure must not stop Mole.

        async def errors(stream):
            while line := await stream.readline():
                self.session.feed(line.decode("utf-8", "replace"))

        ticker = asyncio.create_task(tick())
        stderr_task = None
        try:
            args = self.argv()
            if self.command == "analyze":
                if sys.platform == "win32" and not args[0].lower().endswith("analyze.exe"):
                    raise OSError("Mole analyze.exe is missing. Install the Mole Analyze component first.")
                # Never accidentally launch a TUI when JSON is unsupported.
                self.process = await self.spawn(
                    [*args[:1], "--help"] if args[0].lower().endswith("analyze.exe") else [args[0], "analyze", "--help"]
                )
                help_out, help_err = await asyncio.wait_for(self.process.communicate(), 15)
                if self.stopped:
                    return
                if b"--json" not in help_out + help_err:
                    raise OSError("This Mole version does not provide Analyze JSON output. Update Mole to a version with 'analyze --json'.")
            self.process = await self.spawn(args)
            if self.stopped:
                await self.stop()
                return
            stderr_task = asyncio.create_task(errors(self.process.stderr))
            if self.command == "analyze":
                raw = bytearray()
                while chunk := await self.process.stdout.read(65536):
                    raw.extend(chunk)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError("Mole report exceeds the 16 MB display limit")
                load_report(self.session, self.command, json.loads(raw))
            else:
                while line := await self.process.stdout.readline():
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    if self.command == "status":
                        load_report(self.session, self.command, json.loads(text))
                    else:
                        if not self.session.sections:
                            self.session.feed("➤ Optimize")
                        self.session.feed(text)
                    await self.publish()
            code = await self.process.wait()
            await stderr_task
            self.session.finish(code, self.stopped)
        except asyncio.CancelledError:
            self.stopped = True
            await self.stop_process()
            raise
        except Exception as exc:
            if not self.stopped:
                self.session.state = "failed"
                self.session.feed(str(exc))
            await self.stop_process()
        finally:
            ticker.cancel()
            if stderr_task:
                stderr_task.cancel()
            await asyncio.gather(ticker, *([stderr_task] if stderr_task else []), return_exceptions=True)
            if self.stopped:
                self.session.state = "stopped"
            self.session.ended_at = time.monotonic()
            self.process = None
            self.task = None
            await self.publish()

    async def stop_process(self):
        process = self.process
        if process and process.returncode is None:
            if sys.platform == "win32":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def stop(self):
        self.stopped = True
        await self.stop_process()
