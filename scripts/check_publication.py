"""Check tracked files without ever displaying credential values."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = [p.decode("utf-8") for p in result.stdout.split(b"\0") if p]
    secret_path = ROOT / "secrets" / "deepseek_api_key.txt"
    secret = secret_path.read_bytes().strip() if secret_path.exists() else b""
    problems = []
    for name in paths:
        parts = Path(name).parts
        if any(part in {"secrets", ".venv", ".cache", "storage"} for part in parts) or name == ".env":
            problems.append(f"私密/本机运行目录被跟踪：{name}")
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if secret and len(secret) > 12 and secret in data:
            problems.append(f"发现现有凭证内容：{name}")
        if path.suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
            continue
        if re.search(rb"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,})", data):
            problems.append(f"疑似凭证：{name}")
    return problems


if __name__ == "__main__":
    errors = check()
    for error in errors:
        print(error)
    if not errors:
        print("Publication check passed: tracked files contain no detected credentials or local caches.")
    sys.exit(bool(errors))
