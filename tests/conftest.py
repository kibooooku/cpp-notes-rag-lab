"""Prepare the workspace cache before pytest creates --basetemp on a clean clone."""

from pathlib import Path


def pytest_configure():
    (Path(__file__).resolve().parents[1] / ".cache").mkdir(parents=True, exist_ok=True)
