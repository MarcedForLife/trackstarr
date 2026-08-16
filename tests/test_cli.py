"""Command line startup checks. No media, no network."""

from __future__ import annotations

import pytest

from trackstarr import config
from trackstarr.cli import main


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        # A typo would silently leave the rule on.
        ("DISABLED_RULES", {"langauges"}),
        # A typo would silently drop the layout.
        ("DOWNMIX_LAYOUTS", {"surround"}),
        # A typo (or a bare "true") would silently regenerate nothing.
        ("REGENERATE_DOWNMIXES", "true"),
    ],
)
def test_bad_config_fails_fast(monkeypatch, setting, value):
    monkeypatch.setattr(config, setting, value)
    assert main(["plan", "f.mkv"]) == 1
