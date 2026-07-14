"""Allow `python -m aegis`."""

from __future__ import annotations

from aegis.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
