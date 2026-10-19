"""Check the actual Git publication boundary without printing credential values.

These checks inspect the Git index and unignored files, so they still protect a
new checkout before its first commit. They make no network calls.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template"}
KEY_PATTERN = re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b")
EMPTY_OR_PLACEHOLDER = {
    "", "your_api_key", "your-api-key", "your-deepseek-api-key",
    "replace-with-your-own-key", "replace_me", "changeme", "...",
}


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True
    )


def is_private_path(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    parts = [part.lower() for part in path.parts]
    basename = path.name.lower()
    return (
        "secrets" in parts
        or basename == ".env"
        or (basename.startswith(".env.") and basename not in EXAMPLE_NAMES)
        or basename in {"deepseek_api_key.txt", "id_rsa", "id_ed25519"}
        or basename.endswith((".pem", ".key"))
    )


def _read_blobs(entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    if not entries:
        return []
    result = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=ROOT, check=False, capture_output=True,
        input=b"\n".join(object_id for _name, object_id in entries) + b"\n",
    )
    if result.returncode:
        raise RuntimeError("Unable to inspect staged Git contents")
    output = result.stdout
    offset = 0
    contents = []
    for name, _object_id in entries:
        end = output.index(b"\n", offset)
        _hash, object_type, raw_size = output[offset:end].split()
        size = int(raw_size)
        offset = end + 1
        if object_type == b"blob":
            contents.append((name, output[offset:offset + size]))
        offset += size + 1
    return contents


def indexed_contents() -> list[tuple[str, bytes]]:
    """Read staged blobs, which can differ from their worktree versions."""
    listing = git("ls-files", "--stage", "-z")
    if listing.returncode:
        raise RuntimeError("Unable to inspect staged Git objects")
    entries = []
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        _mode, object_id, _stage = metadata.split()
        entries.append((name.decode("utf-8"), object_id))
    return _read_blobs(entries)


def committed_contents() -> list[tuple[str, bytes]]:
    """Inspect the real HEAD snapshot, including staged-deletion old content."""
    if git("rev-parse", "--verify", "HEAD").returncode:
        return []
    listing = git("ls-tree", "-rz", "HEAD")
    if listing.returncode:
        raise RuntimeError("Unable to inspect the committed Git snapshot")
    entries = []
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        _mode, object_type, object_id = metadata.split()
        if object_type == b"blob":
            entries.append((name.decode("utf-8"), object_id))
    return _read_blobs(entries)


class PublicationSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("git") or git("rev-parse", "--is-inside-work-tree").returncode:
            raise unittest.SkipTest("A Git working tree is required for publication checks")
        # Include staged/tracked files and files that a normal `git add .` adds.
        result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        if result.returncode:
            raise RuntimeError("Unable to inspect the Git publication file list")
        cls.public_files = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
        cls.indexed_files = indexed_contents()
        cls.committed_files = committed_contents()

    def test_credentials_are_not_in_publication_file_list(self) -> None:
        names = set(self.public_files) | {name for name, _ in self.committed_files}
        exposed = sorted(name for name in names if is_private_path(name))
        self.assertEqual(exposed, [], "Credential files must be removed from the Git index or ignored")

    def test_local_credential_paths_are_ignored(self) -> None:
        for name in ("secrets/deepseek_api_key.txt", ".env"):
            with self.subTest(path=name):
                result = git("check-ignore", "--no-index", "-q", name)
                self.assertEqual(result.returncode, 0, f"Git does not ignore private path {name}")

    def test_no_actual_key_or_key_shaped_literal_is_publishable(self) -> None:
        # Compare in memory only. Assertion messages contain paths, never values.
        secret_path = ROOT / "secrets" / "deepseek_api_key.txt"
        actual_key = secret_path.read_bytes().strip() if secret_path.is_file() else b""
        contents = [(name, "HEAD", content) for name, content in self.committed_files]
        contents.extend((name, "index", content) for name, content in self.indexed_files)
        for name in self.public_files:
            path = ROOT / name
            if not path.is_file():
                continue
            contents.append((name, "worktree", path.read_bytes()))
        for name, version, content in contents:
            with self.subTest(path=name, version=version):
                if len(actual_key) >= 12:
                    self.assertFalse(actual_key in content, f"Local credential leaked into {name}")
                self.assertFalse(bool(KEY_PATTERN.search(content)), f"A key-shaped literal is publishable in {name}")

    def test_environment_example_has_no_real_credential(self) -> None:
        example = ROOT / ".env.example"
        self.assertTrue(example.is_file(), "Provide a safe .env.example for reproducible setup")
        for line in example.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip().removeprefix("export ") != "DEEPSEEK_API_KEY":
                continue
            value = value.strip().strip("\"'").lower()
            self.assertTrue(value in EMPTY_OR_PLACEHOLDER, "The environment example contains a credential-like value")


if __name__ == "__main__":
    unittest.main()
