"""Package metadata smoke tests."""

from __future__ import annotations

from aegis import __version__


def test_version_semver_shape() -> None:
    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])
