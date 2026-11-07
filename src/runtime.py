import hashlib
from collections.abc import Callable
import hmac
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

MO_EXECUTABLE_NAME = "mo"
MO_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
RELEASES_ATOM_URL = "https://github.com/tw93/Mole/releases.atom"
WINDOWS_FALLBACK_TAG = "V1.42.0-windows"
DARWIN_FALLBACK_TAG = "V1.69.0"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
WINDOWS_TAG = re.compile(r"^V(\d+\.\d+\.\d+)-windows$")
DARWIN_TAG = re.compile(r"^V(\d+\.\d+\.\d+)$")
MOLE_DIR_MARKERS = {"mo.exe", "mo.cmd", "mole.ps1", "install.ps1", "VERSION", "bin", "lib"}


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
            return (os.path.join(os.path.expanduser("~"), ".local", "bin"), *MO_BIN_DIRS)
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

    @staticmethod
    def _install_cli(progress: Callable[[str, int, int, str], None] | None = None) -> None:
        if sys.platform == "win32":
            MoleRuntime._install_windows(progress)
        elif sys.platform == "darwin":
            MoleRuntime._install_darwin(progress)
        else:
            raise OSError("Automatic Mole install is only available on Windows and macOS.")

    @staticmethod
    def _note(progress: Callable[[str, int, int, str], None] | None, phase: str, received: int = 0, total: int = 0, label: str = "") -> None:
        if progress is not None:
            progress(phase, received, total, label)

    @staticmethod
    def _install_windows(progress: Callable[[str, int, int, str], None] | None = None) -> None:
        if platform.machine().lower() not in {"amd64", "x86_64"}:
            raise OSError("Automatic Mole install supports 64-bit Windows only.")
        destination = MoleRuntime._windows_install_dir()
        MoleRuntime._windows_destination_state(destination)
        MoleRuntime._note(progress, "lookup")
        release = MoleRuntime._select_release(MoleRuntime._release_list(), "win32")
        match = WINDOWS_TAG.fullmatch(str(release.get("tag_name") or ""))
        if match is None:
            raise OSError("No stable Mole release was found.")
        version = match.group(1)
        filename = f"mole-{version}-x64.zip"
        assets = {item.get("name"): item for item in release.get("assets") or [] if isinstance(item, dict)}
        archive_asset = assets.get(filename)
        sums_asset = assets.get("SHA256SUMS.txt")
        if not isinstance(archive_asset, dict) or not isinstance(sums_asset, dict):
            raise OSError("Mole Windows release is missing its archive or checksums.")
        sums_url = MoleRuntime._release_download_url(sums_asset.get("browser_download_url"))
        archive_url = MoleRuntime._release_download_url(archive_asset.get("browser_download_url"))
        def on_checksum(received: int, total: int) -> None:
            MoleRuntime._note(progress, "checksum", received, total)

        archive_size = archive_asset.get("size")
        archive_bytes = archive_size if isinstance(archive_size, int) and archive_size > 0 else 0

        def on_archive(received: int, total: int) -> None:
            MoleRuntime._note(progress, "archive", received, total or archive_bytes, version)

        sums = MoleRuntime._http_get(sums_url, 1_000_000, on_checksum)
        archive = MoleRuntime._http_get(archive_url, 80_000_000, on_archive)
        expected = MoleRuntime._checksum(sums.decode("utf-8"), filename)
        published = MoleRuntime._asset_digest(archive_asset)
        sums_published = MoleRuntime._asset_digest(sums_asset)
        if published or sums_published:
            if not re.fullmatch(r"[0-9a-f]{64}", published) or not re.fullmatch(r"[0-9a-f]{64}", sums_published):
                raise OSError("Mole release did not include a checksum.")
            if not hmac.compare_digest(published, expected):
                raise OSError("Mole checksum file did not match the release.")
        MoleRuntime._note(progress, "verify")
        if sums_published:
            MoleRuntime._require_digest(sums, sums_published)
        MoleRuntime._require_digest(archive, expected)
        MoleRuntime._note(progress, "install")
        MoleRuntime._install_windows_tree(archive, destination)

    @staticmethod
    def _install_darwin(progress: Callable[[str, int, int, str], None] | None = None) -> None:
        MoleRuntime._note(progress, "lookup")
        release = MoleRuntime._select_release(MoleRuntime._release_list(), "darwin")
        tag = str(release.get("tag_name") or "")
        version = tag[1:] if tag.startswith("V") else tag

        def on_script(received: int, total: int) -> None:
            MoleRuntime._note(progress, "archive", received, total, version)

        script = MoleRuntime._http_get(MoleRuntime._darwin_installer_url(tag), 2_000_000, on_script)
        MoleRuntime._note(progress, "install", label=version)
        if not script.startswith(b"#!"):
            raise OSError("Mole installer was not a shell script.")
        prefix_parent = os.path.join(os.path.expanduser("~"), ".local")
        os.makedirs(prefix_parent, exist_ok=True)
        prefix = os.path.join(prefix_parent, "bin")
        with tempfile.TemporaryDirectory(prefix="mole-install-") as folder:
            path = os.path.join(folder, "install.sh")
            with open(path, "wb") as handle:
                handle.write(script)
            os.chmod(path, 0o700)
            try:
                completed = subprocess.run(
                    ["/bin/bash", path, "--prefix", prefix],
                    check=False,
                    capture_output=True,
                    timeout=300,
                    env={**os.environ, "NO_COLOR": "1"},
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("Mole installer could not be run.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or b"Mole installer failed.").decode("utf-8", "replace").strip()
            line = detail.splitlines()[-1][:300] if detail else "Mole installer failed."
            raise OSError(f"Mole install failed: {line}")

    @staticmethod
    def _windows_install_dir() -> str:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise OSError("LOCALAPPDATA is not set, so Mole cannot be installed.")
        return os.path.join(local, "Programs", "mole-cli")

    @staticmethod
    def _windows_destination_state(destination: str) -> str:
        if not os.path.exists(destination):
            return "missing"
        if not os.path.isdir(destination) or os.path.islink(destination):
            raise OSError(f"{destination} is not a directory.")
        names = set(os.listdir(destination))
        if not names:
            return "empty"
        if names.isdisjoint(MOLE_DIR_MARKERS):
            raise OSError(f"{destination} already exists and is not a Mole install.")
        return "mole"

    @staticmethod
    def _install_windows_tree(payload: bytes, destination: str) -> None:
        parent = os.path.dirname(destination)
        os.makedirs(parent, exist_ok=True)
        state = MoleRuntime._windows_destination_state(destination)
        temp = tempfile.mkdtemp(dir=parent, prefix=".mole-cli-")
        try:
            MoleRuntime._extract_zip(payload, temp)
            if not os.path.isfile(os.path.join(temp, "mole.ps1")):
                raise OSError("Mole archive did not contain mole.ps1.")
            MoleRuntime._replace_tree(temp, destination, state)
        finally:
            if os.path.isdir(temp):
                shutil.rmtree(temp, ignore_errors=True)

    @staticmethod
    def _replace_tree(source: str, destination: str, state: str) -> None:
        if state == "missing":
            os.rename(source, destination)
            return
        if state == "empty":
            os.rmdir(destination)
            os.rename(source, destination)
            return
        backup = tempfile.mkdtemp(dir=os.path.dirname(destination), prefix=".mole-cli-old-")
        os.rmdir(backup)
        os.rename(destination, backup)
        try:
            os.rename(source, destination)
        except Exception:
            os.rename(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _extract_zip(payload: bytes, destination: str) -> None:
        os.makedirs(destination, exist_ok=True)
        total = 0
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                total += info.file_size
                if total > 200_000_000:
                    raise OSError("Mole archive is larger than expected.")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise OSError("Mole archive contains a symlink.")
                target = MoleRuntime._archive_target(destination, info.filename)
                if info.is_dir() or info.filename.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                parent = os.path.dirname(target)
                os.makedirs(parent, exist_ok=True)
                root_real = os.path.realpath(destination)
                parent_real = os.path.realpath(parent)
                try:
                    if os.path.commonpath([root_real, parent_real]) != root_real:
                        raise OSError("Mole archive contains an unsafe path.")
                except ValueError as exc:
                    raise OSError("Mole archive contains an unsafe path.") from exc
                if os.path.lexists(target) and os.path.islink(target):
                    raise OSError("Mole archive contains an unsafe path.")
                with archive.open(info, "r") as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def _archive_target(destination: str, name: str) -> str:
        relative = name.replace("\\", "/")
        if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise OSError("Mole archive contains an unsafe path.")
        parts = [part for part in relative.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise OSError("Mole archive contains an unsafe path.")
        root = os.path.abspath(destination)
        target = os.path.abspath(os.path.join(root, *parts))
        try:
            if os.path.commonpath([root, target]) != root:
                raise OSError("Mole archive contains an unsafe path.")
        except ValueError as exc:
            raise OSError("Mole archive contains an unsafe path.") from exc
        return target

    @staticmethod
    def _release_list() -> list:
        try:
            raw = MoleRuntime._http_get(RELEASES_ATOM_URL, 2_000_000)
            return MoleRuntime._releases_from_atom(raw)
        except OSError:
            return []

    @staticmethod
    def _releases_from_atom(raw: bytes) -> list:
        if not raw:
            raise OSError("Mole release list was not valid.")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise OSError("Mole release list was not valid.") from exc
        releases = []
        seen: set[str] = set()
        for entry in root.findall(f"{ATOM_NS}entry"):
            tag = MoleRuntime._atom_tag(entry)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            release = MoleRuntime._release_from_tag(tag)
            if release is not None:
                releases.append(release)
        return releases

    @staticmethod
    def _atom_tag(entry: ET.Element) -> str | None:
        marker = "/releases/tag/"
        for link in entry.findall(f"{ATOM_NS}link"):
            href = link.attrib.get("href") or ""
            if marker not in href:
                continue
            tag = urllib.parse.unquote(href.split(marker, 1)[1])
            tag = tag.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
            if tag:
                return tag
        entry_id = (entry.findtext(f"{ATOM_NS}id") or "").strip()
        if "/" not in entry_id:
            return None
        return urllib.parse.unquote(entry_id.rsplit("/", 1)[1].strip()) or None

    @staticmethod
    def _select_release(releases: list, platform_name: str) -> dict:
        try:
            return MoleRuntime._choose_release(releases, platform_name)
        except OSError:
            tag = WINDOWS_FALLBACK_TAG if platform_name == "win32" else DARWIN_FALLBACK_TAG
            release = MoleRuntime._release_from_tag(tag)
            if release is None:
                raise OSError("No stable Mole release was found.")
            return release

    @staticmethod
    def _release_from_tag(tag: str) -> dict | None:
        windows = WINDOWS_TAG.fullmatch(tag)
        if windows is None and DARWIN_TAG.fullmatch(tag) is None:
            return None
        assets: list[dict[str, str]] = []
        if windows is not None:
            version = windows.group(1)
            filename = f"mole-{version}-x64.zip"
            base = f"https://github.com/tw93/Mole/releases/download/{tag}"
            assets = [
                {"name": filename, "browser_download_url": f"{base}/{filename}"},
                {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"},
            ]
        return {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets}

    @staticmethod
    def _choose_release(releases: list, platform_name: str) -> dict:
        pattern = WINDOWS_TAG if platform_name == "win32" else DARWIN_TAG
        best: tuple[tuple[int, ...], dict] | None = None
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            match = pattern.fullmatch(str(release.get("tag_name") or ""))
            if match is None:
                continue
            version = tuple(int(part) for part in match.group(1).split("."))
            if best is None or version > best[0]:
                best = (version, release)
        if best is None:
            raise OSError("No stable Mole release was found.")
        return best[1]

    @staticmethod
    def _checksum(listing: str, filename: str) -> str:
        digest = ""
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[-1].lstrip("*") != filename:
                continue
            digest = parts[0].lower()
            break
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise OSError(f"Checksum for {filename} was not listed.")
        return digest

    @staticmethod
    def _asset_digest(asset: dict) -> str:
        digest = str(asset.get("digest") or "")
        prefix = "sha256:"
        if not digest.startswith(prefix):
            return ""
        return digest[len(prefix) :].lower()

    @staticmethod
    def _require_digest(payload: bytes, expected: str) -> None:
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise OSError("Mole download checksum did not match.")

    @staticmethod
    def _release_download_url(url: object) -> str:
        if not isinstance(url, str):
            raise OSError("Mole release URL was missing.")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or ".." in parsed.path.split("/"):
            raise OSError("Mole release URL was not on GitHub.")
        if not parsed.path.startswith("/tw93/Mole/releases/download/"):
            raise OSError("Mole release URL was not an official download.")
        return url

    @staticmethod
    def _darwin_installer_url(tag: str) -> str:
        if DARWIN_TAG.fullmatch(tag) is None:
            raise OSError("Mole release tag was not a stable macOS release.")
        return f"https://raw.githubusercontent.com/tw93/Mole/{tag}/install.sh"

    @staticmethod
    def _http_get(url: str, limit: int, on_bytes: Callable[[int, int], None] | None = None) -> bytes:
        parsed = urllib.parse.urlparse(url)
        accept = "application/octet-stream"
        if parsed.hostname == "github.com" and parsed.path.endswith("/releases.atom"):
            accept = "application/atom+xml"
        request = urllib.request.Request(url, headers={"User-Agent": "Wox.Plugin.Mole", "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                length = response.headers.get("Content-Length")
                expected = int(length) if isinstance(length, str) and length.isdigit() else 0
                chunks = []
                received = 0
                if on_bytes is not None:
                    on_bytes(0, expected)
                while block := response.read(256 * 1024):
                    received += len(block)
                    if received > limit:
                        raise OSError("Mole download is larger than expected.")
                    chunks.append(block)
                    if on_bytes is not None:
                        on_bytes(received, expected)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read(1000))
                if isinstance(body, dict) and body.get("message"):
                    detail = " " + str(body["message"])
            except (OSError, json.JSONDecodeError, UnicodeError):
                detail = ""
            raise OSError(f"Could not download Mole ({exc.code}).{detail}") from exc
        except urllib.error.URLError as exc:
            raise OSError("Could not download Mole.") from exc
        return b"".join(chunks)
