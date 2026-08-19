"""Command line startup checks. No media, no network."""

from __future__ import annotations

import pytest

from trackstarr import config
from trackstarr.cli import main
from trackstarr.media import ProbeError


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


def test_plan_fails_when_a_file_cannot_be_read(monkeypatch, capsys):
    """A script watching the exit code must see a probe failure."""
    monkeypatch.setattr("trackstarr.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    assert main(["plan", "--original", "eng", "/nowhere/missing.mkv"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_plan_normalises_the_original_language(monkeypatch, capsys):
    """Stream tags are ISO 639-2/B, so a 639-1 flag value must become one
    too or the plan drops the very language it was told to keep."""
    monkeypatch.setattr("trackstarr.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    seen = []

    def capture_lang(path, lang):
        seen.append(lang)
        raise ProbeError("stop before probing")

    monkeypatch.setattr("trackstarr.cli.build_plan", capture_lang)
    main(["plan", "--original", "ja", "f.mkv"])
    assert seen == ["jpn"]
