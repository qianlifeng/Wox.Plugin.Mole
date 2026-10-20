import os
import shutil
import sys

MO_EXECUTABLE_NAME = "mo"
MO_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


class MoleRuntime:
    @staticmethod
    def _find_mo_executable() -> str | None:
        names = ("mo.exe", "mo.cmd", "mole.ps1") if sys.platform == "win32" else (MO_EXECUTABLE_NAME,)
        for name in names:
            path = shutil.which(name)
            if path is not None:
                if path.lower().endswith(".cmd"):
                    script = os.path.join(os.path.dirname(path), "mole.ps1")
                    if os.path.isfile(script):
                        return script
                    continue
                return path

        for directory in MoleRuntime._bin_dirs():
            for name in names:
                path = os.path.join(directory, name)
                if not name.endswith(".cmd") and os.path.isfile(path):
                    return path
        return None

    @staticmethod
    def _bin_dirs() -> tuple[str, ...]:
        if sys.platform != "win32":
            return MO_BIN_DIRS
        dirs = []
        if local := os.environ.get("LOCALAPPDATA"):
            dirs.append(os.path.join(local, "Programs", "mole-cli"))
        scoop = os.environ.get("SCOOP") or os.path.join(os.path.expanduser("~"), "scoop")
        dirs.append(os.path.join(scoop, "apps", "mole", "current"))
        if global_scoop := os.environ.get("SCOOP_GLOBAL"):
            dirs.append(os.path.join(global_scoop, "apps", "mole", "current"))
        return tuple(dirs)

    @staticmethod
    def _powershell() -> str:
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
