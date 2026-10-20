"""Bridge to Mole's installed Windows application functions."""

import asyncio
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from .runtime import MoleRuntime

MARKER = "__WOX_MOLE_RESULT__"


def find_root() -> Path:
    if sys.platform != "win32":
        raise OSError("The Mole application-list adapter currently supports Windows only.")
    executable = MoleRuntime._find_mo_executable()
    candidates = [Path(executable).resolve().parent] if executable else []
    if executable:
        shim = Path(executable).with_suffix(".shim")
        if shim.is_file():
            match = re.search(r'^path\s*=\s*"([^"]+)"', shim.read_text(encoding="utf-8-sig"), re.M)
            if match:
                candidates.insert(0, Path(match[1]).resolve().parent)
    candidates.extend(Path(directory) for directory in MoleRuntime._bin_dirs())
    for root in candidates:
        if (root / "bin" / "uninstall.ps1").is_file():
            return root
    raise OSError("Cannot find Mole's bin/uninstall.ps1. Install or update the Mole Windows CLI.")


def parse_apps(raw: str) -> list[dict]:
    apps = json.loads(raw)
    if not isinstance(apps, list):
        raise ValueError("Mole returned an invalid application list")
    seen = set()
    for app in apps:
        if (
            not isinstance(app, dict)
            or not all(isinstance(app.get(key), str) and app[key] for key in ("key", "name"))
            or not isinstance(app.get("size"), str)
            or type(app.get("size_kb")) not in (int, float)
            or not math.isfinite(app["size_kb"])
            or app["size_kb"] < 0
            or app["key"] in seen
        ):
            raise ValueError("Mole returned an invalid or ambiguous application")
        seen.add(app["key"])
    return sorted(apps, key=lambda app: app["size_kb"], reverse=True)


class UninstallBackend:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        await self.terminate()

    async def terminate(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if await killer.wait() != 0 and process.returncode is None:
                raise OSError("Could not stop Mole's uninstall adapter")
            await process.wait()

    async def run(self, operation: str, key: str = "", refresh: bool = False) -> list[dict]:
        if self.closed:
            raise OSError("Mole uninstall adapter is closed")
        if operation not in ("list", "uninstall"):
            raise ValueError("Invalid uninstall operation")
        env = os.environ.copy()
        env["WOX_MOLE_REQUEST"] = json.dumps(
            {
                "root": str(find_root()),
                "operation": operation,
                "key": key,
                "refresh": refresh or operation == "uninstall",
            }
        )
        self.process = await asyncio.create_subprocess_exec(
            MoleRuntime._powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(__file__).with_name("uninstall_bridge.ps1")),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
            limit=16 * 1024 * 1024,
        )
        process = self.process

        async def collect() -> list[dict]:
            result = None
            recent: list[str] = []
            assert process.stdout
            while line := await process.stdout.readline():
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith(MARKER):
                    result = parse_apps(text[len(MARKER) :])
                else:
                    recent.append(text)
                    recent = recent[-8:]
            code = await process.wait()
            if code or result is None:
                raise OSError("\n".join(recent) or "Mole did not return application data.")
            return result

        try:
            if self.closed:
                await self.terminate()
                raise OSError("Mole uninstall adapter was closed")
            return await asyncio.wait_for(collect(), timeout=1800 if operation == "uninstall" else 120)
        finally:
            await self.terminate()
            self.process = None
