"""Hidden Mole process lifecycle; all cleanup is delegated to the installed CLI."""

import asyncio
import codecs
import os
import re
import subprocess
import sys
import time

from wox_plugin import Context
from .runtime import MoleRuntime
from .dashboard import Session


class DownloadStopped(Exception):
    """The user stopped cleanup while the CLI download was in progress."""


class CleanupRunner(MoleRuntime):
    session: Session
    process: asyncio.subprocess.Process | None
    task: asyncio.Task | None
    stop_requested: bool

    async def publish(self, ctx: Context) -> None:
        raise NotImplementedError

    def clean_command(self) -> list[str]:
        executable = self._find_mo_executable()
        if not executable:
            raise OSError("Mole CLI is still not available after installation.")
        args = [executable, "clean"]
        if sys.platform == "win32":
            # Prefer the script beside an actual launcher, for reliable UTF-8.
            script = os.path.join(os.path.dirname(os.path.realpath(executable)), "mole.ps1")
            if os.path.isfile(script):
                args[0] = script
            literal = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
            return [
                self._powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
                + "try { "
                + literal
                + "; exit $LASTEXITCODE } catch { Write-Output $_; exit 1 }",
            ]
        return args

    async def run_clean(self, ctx: Context) -> None:
        try:
            if not self._find_mo_executable():
                self.session.state = "downloading"
                self.session.download_phase = "lookup"
                self.session.feed("Mole CLI was not found. Downloading the official release.")
                await self.publish(ctx)
                loop = asyncio.get_running_loop()
                last_publish = 0.0
                last_phase = ""
                last_label = ""

                def progress(phase: str, received: int, total: int, label: str = "") -> None:
                    nonlocal last_publish, last_phase, last_label
                    if self.stop_requested:
                        raise DownloadStopped()
                    now = time.monotonic()
                    changed = phase != last_phase or label != last_label
                    finished = total > 0 and received >= total
                    if not changed and not finished and now - last_publish < 0.25:
                        return
                    last_publish = now
                    last_phase = phase
                    last_label = label

                    def apply() -> None:
                        if self.session.state != "downloading":
                            return
                        self.session.download_phase = phase
                        self.session.download_received = received
                        self.session.download_total = total
                        if label:
                            self.session.download_label = label
                        asyncio.create_task(self.publish(ctx))

                    loop.call_soon_threadsafe(apply)

                try:
                    await asyncio.to_thread(self._install_cli, progress)
                except DownloadStopped:
                    self.session.state = "stopped"
                    return
                if self.stop_requested:
                    self.session.state = "stopped"
                    return
                self.session.state = "running"
                self.session.download_phase = ""
                self.session.download_received = 0
                self.session.download_total = 0
                self.session.download_label = ""
                self.session.current_item = ""
                self.session.feed("Mole CLI installed.")
                await self.publish(ctx)
            env = os.environ.copy()
            env.update(NO_COLOR="1", MOLE_NO_COLOR="1", LC_ALL="C", LANG="C")
            env["MOLE_DRY_RUN"] = "0"
            self.process = await asyncio.create_subprocess_exec(
                *self.clean_command(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
            )
            if self.stop_requested:
                await self.terminate()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""
            last_update = 0.0
            assert self.process.stdout is not None
            while chunk := await self.process.stdout.read(4096):
                buffer += decoder.decode(chunk)
                lines = re.split(r"[\r\n]+", buffer)
                buffer = lines.pop()
                for line in lines:
                    self.session.feed(line)
                if len(buffer) > 8000:
                    self.session.feed(buffer[:8000])
                    buffer = ""
                    self.session.truncated = True
                if time.monotonic() - last_update >= 0.25:
                    await self.publish(ctx)
                    last_update = time.monotonic()
            self.session.feed(buffer + decoder.decode(b"", final=True))
            code = await self.process.wait()
            self.session.finish(code, self.stop_requested)
        except asyncio.CancelledError:
            self.session.state = "stopped"
            await self.terminate()
            raise
        except Exception as exc:
            self.session.state = "failed"
            self.session.feed(str(exc))
            if self.process and self.process.returncode is None:
                await self.terminate()
        finally:
            self.session.ended_at = time.monotonic()
            self.process = None
            self.task = None
            await self.publish(ctx)

    async def terminate(self) -> None:
        process = self.process
        if not process or process.returncode is not None:
            return
        if sys.platform == "win32":
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
            code = await killer.wait()
            if code and process.returncode is None:
                raise OSError("Could not stop the Mole process tree")
        else:
            import signal

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), 2)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
