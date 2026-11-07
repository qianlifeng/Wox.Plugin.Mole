import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.runtime import MoleRuntime


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()



def atom_feed(*tags: str) -> bytes:
    entries = []
    for tag in tags:
        entries.append(
            "<entry>"
            f"<id>tag:github.com,2008:Repository/1/{tag}</id>"
            f'<link rel="alternate" type="text/html" href="https://github.com/tw93/Mole/releases/tag/{tag}"/>'
            f"<title>{tag}</title>"
            "</entry>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        + "\n".join(entries)
        + "\n</feed>"
    )
    return body.encode()


def release(tag: str, filename: str, payload: bytes, sums: bytes, **flags: bool) -> dict:
    return {
        "tag_name": tag,
        "draft": flags.get("draft", False),
        "prerelease": flags.get("prerelease", False),
        "assets": [
            {
                "name": filename,
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "browser_download_url": f"https://github.com/tw93/Mole/releases/download/{tag}/{filename}",
            },
            {
                "name": "SHA256SUMS.txt",
                "digest": "sha256:" + hashlib.sha256(sums).hexdigest(),
                "browser_download_url": f"https://github.com/tw93/Mole/releases/download/{tag}/SHA256SUMS.txt",
            },
        ],
    }


class TestMoleInstall(unittest.TestCase):
    def test_release_choice_skips_prerelease_and_other_platforms(self):
        releases = [
            {"tag_name": "V1.9.0-windows", "draft": False, "prerelease": False, "assets": []},
            {"tag_name": "V1.10.0-windows", "draft": False, "prerelease": False, "assets": []},
            {"tag_name": "V2.0.0-windows", "draft": False, "prerelease": True, "assets": []},
            {"tag_name": "V9.0.0", "draft": False, "prerelease": False, "assets": []},
            {"tag_name": "V0.4.0-linux", "draft": False, "prerelease": False, "assets": []},
        ]
        self.assertEqual(MoleRuntime._choose_release(releases, "win32")["tag_name"], "V1.10.0-windows")
        self.assertEqual(MoleRuntime._choose_release(releases, "darwin")["tag_name"], "V9.0.0")
        self.assertEqual(
            MoleRuntime._darwin_installer_url("V9.0.0"),
            "https://raw.githubusercontent.com/tw93/Mole/V9.0.0/install.sh",
        )
        with self.assertRaises(OSError):
            MoleRuntime._darwin_installer_url("V9.0.0-windows")

    def test_release_urls_stay_on_github(self):
        with self.assertRaises(OSError):
            MoleRuntime._release_download_url("http://github.com/tw93/Mole/releases/download/V1.0.0/mole.zip")
        with self.assertRaises(OSError):
            MoleRuntime._release_download_url("https://example.com/tw93/Mole/releases/download/V1.0.0/mole.zip")
        with self.assertRaises(OSError):
            MoleRuntime._release_download_url("https://github.com/tw93/Mole/releases/download/../../evil/file")

    def test_release_list_reads_first_page(self):
        headers = []
        advertisement = atom_feed("V1.9.0-windows", "V1.10.0-windows", "V9.0.0", "V0.4.0-linux", "v1.30.0-windows")

        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size):
                payload = self.payload
                self.payload = b""
                return payload

        def fake_urlopen(request, timeout):
            headers.append(request.get_header("Accept"))
            if "releases.atom" in request.full_url:
                self.assertNotIn("api.github.com", request.full_url)
                return Response(advertisement)
            return Response(b"ok")

        with patch("src.runtime.urllib.request.urlopen", fake_urlopen):
            releases = MoleRuntime._release_list()
            MoleRuntime._http_get("https://github.com/tw93/Mole/releases/download/V1.0.0/SHA256SUMS.txt", 10)
        self.assertEqual([item["tag_name"] for item in releases], ["V1.9.0-windows", "V1.10.0-windows", "V9.0.0"])
        windows = releases[1]
        self.assertEqual(
            windows["assets"][0]["browser_download_url"],
            "https://github.com/tw93/Mole/releases/download/V1.10.0-windows/mole-1.10.0-x64.zip",
        )
        self.assertEqual(
            windows["assets"][1]["browser_download_url"],
            "https://github.com/tw93/Mole/releases/download/V1.10.0-windows/SHA256SUMS.txt",
        )
        self.assertNotIn("digest", windows["assets"][0])
        self.assertEqual(headers, ["application/atom+xml", "application/octet-stream"])

    def test_release_list_ignores_malformed_feed(self):
        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size):
                payload = self.payload
                self.payload = b""
                return payload

        with patch("src.runtime.urllib.request.urlopen", lambda request, timeout: Response(b"not xml")):
            self.assertEqual(MoleRuntime._release_list(), [])
        with self.assertRaisesRegex(OSError, "not valid"):
            MoleRuntime._releases_from_atom(b"not xml")

    def test_fallback_is_used_only_when_the_platform_is_missing(self):
        page = MoleRuntime._releases_from_atom(atom_feed("V1.69.0", "V0.4.0-linux"))
        windows = MoleRuntime._select_release(page, "win32")
        darwin = MoleRuntime._select_release(page, "darwin")
        self.assertEqual(windows["tag_name"], "V1.42.0-windows")
        self.assertEqual(
            windows["assets"][0]["browser_download_url"],
            "https://github.com/tw93/Mole/releases/download/V1.42.0-windows/mole-1.42.0-x64.zip",
        )
        self.assertEqual(
            windows["assets"][1]["browser_download_url"],
            "https://github.com/tw93/Mole/releases/download/V1.42.0-windows/SHA256SUMS.txt",
        )
        self.assertEqual(darwin["tag_name"], "V1.69.0")
        newer = MoleRuntime._releases_from_atom(atom_feed("V1.50.0-windows", "V1.70.0"))
        self.assertEqual(MoleRuntime._select_release(newer, "win32")["tag_name"], "V1.50.0-windows")
        self.assertEqual(MoleRuntime._select_release(newer, "darwin")["tag_name"], "V1.70.0")
        self.assertEqual(MoleRuntime._select_release([], "win32")["tag_name"], "V1.42.0-windows")
        self.assertEqual(MoleRuntime._select_release([], "darwin")["tag_name"], "V1.69.0")

    def test_http_get_reports_byte_progress(self):
        class Response:
            def __init__(self):
                self.chunks = [b"abcd", b"ef"]
                self.headers = {"Content-Length": "6"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size):
                if not self.chunks:
                    return b""
                return self.chunks.pop(0)

        seen = []

        def on_bytes(received, total):
            seen.append((received, total))

        with patch("src.runtime.urllib.request.urlopen", lambda request, timeout: Response()):
            payload = MoleRuntime._http_get(
                "https://github.com/tw93/Mole/releases/download/V1.0.0/mole.zip",
                10,
                on_bytes,
            )
        self.assertEqual(payload, b"abcdef")
        self.assertEqual(seen, [(0, 6), (4, 6), (6, 6)])


    def test_archive_path_traversal_is_rejected(self):
        payload = zip_bytes({"../outside.txt": b"nope", "nested/../../also.txt": b"nope", "mole.ps1": b"ok"})
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                MoleRuntime._extract_zip(payload, str(Path(folder) / "mole-cli"))
            self.assertFalse((Path(folder) / "outside.txt").exists())
            self.assertFalse((Path(folder) / "also.txt").exists())

    def test_unrelated_directory_is_not_replaced(self):
        archive = zip_bytes({"mole.ps1": b"Write-Output ok\n"})
        filename = "mole-1.2.3-x64.zip"
        sums = f"{'0' * 64}  {filename}\n".encode()

        def fake_get(url: str, limit: int, on_bytes=None) -> bytes:
            if "releases.atom" in url:
                return atom_feed("V1.2.3-windows")
            if url.endswith("SHA256SUMS.txt"):
                return sums
            if url.endswith(filename):
                return archive
            raise AssertionError(url)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.runtime.platform.machine", return_value="AMD64"),
            patch.dict(os.environ, {"LOCALAPPDATA": folder}),
            patch.object(MoleRuntime, "_http_get", staticmethod(fake_get)),
        ):
            notes = Path(folder) / "Programs" / "mole-cli" / "notes.txt"
            notes.parent.mkdir(parents=True)
            notes.write_text("keep", encoding="utf-8")
            with self.assertRaises(OSError):
                MoleRuntime._install_windows()
            self.assertEqual(notes.read_text(encoding="utf-8"), "keep")
            self.assertFalse((notes.parent / "mole.ps1").exists())

    def test_checksum_mismatch_writes_nothing(self):
        archive = zip_bytes({"mole.ps1": b"Write-Output ok\n"})
        filename = "mole-1.2.3-x64.zip"
        digest = hashlib.sha256(archive).hexdigest()
        sums = f"{digest}  {filename}\n".encode()

        def fake_get(url: str, limit: int, on_bytes=None) -> bytes:
            if "releases.atom" in url:
                return atom_feed("V1.2.3-windows")
            if url.endswith("SHA256SUMS.txt"):
                return sums
            if url.endswith(filename):
                return b"not-the-archive"
            raise AssertionError(url)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.runtime.platform.machine", return_value="AMD64"),
            patch.dict(os.environ, {"LOCALAPPDATA": folder}),
            patch.object(MoleRuntime, "_http_get", staticmethod(fake_get)),
        ):
            with self.assertRaisesRegex(OSError, "checksum"):
                MoleRuntime._install_windows()
            self.assertFalse((Path(folder) / "Programs" / "mole-cli").exists())

    def test_windows_install_verifies_checksum_and_is_discovered(self):
        archive = zip_bytes({"mole.ps1": b"Write-Output ok\n", "bin/clean.ps1": b"# clean\n"})
        filename = "mole-1.10.0-x64.zip"
        digest = hashlib.sha256(archive).hexdigest()
        sums = f"{digest}  {filename}\n".encode()

        def fake_get(url: str, limit: int, on_bytes=None) -> bytes:
            if "releases.atom" in url:
                return atom_feed("V1.9.0-windows", "V2.0.0-rc1", "V1.10.0-windows", "V9.0.0")
            if url.endswith("SHA256SUMS.txt"):
                self.assertIn("V1.10.0-windows", url)
                self.assertTrue(url.startswith("https://github.com/tw93/Mole/releases/download/"))
                return sums
            if url.endswith(filename):
                return archive
            raise AssertionError(url)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.runtime.platform.machine", return_value="AMD64"),
            patch.dict(os.environ, {"LOCALAPPDATA": folder}),
            patch("src.runtime.sys.platform", "win32"),
            patch("src.runtime.shutil.which", return_value=None),
            patch.object(MoleRuntime, "_http_get", staticmethod(fake_get)),
        ):
            MoleRuntime._install_windows()
            installed = Path(folder) / "Programs" / "mole-cli" / "mole.ps1"
            self.assertEqual(installed.read_bytes(), b"Write-Output ok\n")
            self.assertTrue((installed.parent / "bin" / "clean.ps1").is_file())
            self.assertEqual(MoleRuntime._find_mo_executable(), str(installed))


    def test_published_digest_must_match_checksum_file(self):
        archive = zip_bytes({"mole.ps1": b"Write-Output ok\n"})
        filename = "mole-1.2.3-x64.zip"
        digest = hashlib.sha256(archive).hexdigest()
        sums = f"{digest}  {filename}\n".encode()
        chosen = release("V1.2.3-windows", filename, archive, sums)
        chosen["assets"][0]["digest"] = "sha256:" + ("ab" * 32)

        def fake_get(url: str, limit: int, on_bytes=None) -> bytes:
            if url.endswith("SHA256SUMS.txt"):
                return sums
            if url.endswith(filename):
                return archive
            raise AssertionError(url)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.runtime.platform.machine", return_value="AMD64"),
            patch.dict(os.environ, {"LOCALAPPDATA": folder}),
            patch.object(MoleRuntime, "_http_get", staticmethod(fake_get)),
            patch.object(MoleRuntime, "_release_list", staticmethod(lambda: [chosen])),
        ):
            with self.assertRaisesRegex(OSError, "checksum"):
                MoleRuntime._install_windows()
            self.assertFalse((Path(folder) / "Programs" / "mole-cli").exists())

    def test_user_prefix_is_discovered_on_macos(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / ".local" / "bin" / "mo"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with (
                patch("src.runtime.sys.platform", "darwin"),
                patch("src.runtime.shutil.which", return_value=None),
                patch("src.runtime.os.path.expanduser", return_value=folder),
            ):
                self.assertEqual(MoleRuntime._find_mo_executable(), str(executable))


if __name__ == "__main__":
    unittest.main()
