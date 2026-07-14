"""Package entrypoints."""

from __future__ import annotations

import runpy
from unittest.mock import patch


def test_main_module() -> None:
    with patch("aegis.cli.main", return_value=0):
        try:
            runpy.run_module("aegis", run_name="not_main")
        except SystemExit:
            pass
