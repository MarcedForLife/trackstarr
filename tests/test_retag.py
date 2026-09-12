"""Editing a track's tags in place. mkvtoolnix is faked here; test_integration
runs the real tools where they are installed."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import (
    REWROTE,
    audio,
    needed_plan,
    probe_data,
    read_events,
    set_config,
    subtitle,
    video,
)
from trackstarr import library, mkvtag, retag, rewrites, sweep_cache
from trackstarr.media import ProbeError
from trackstarr.planner import Plan
from trackstarr.policy import Policy
from trackstarr.processing import ProcessResult
from trackstarr.retag import Edit, Outcome
from trackstarr.status import Status

UID = 5324733994220836459


def listing(*tracks: dict) -> str:
    """mkvmerge -J's answer, as far as anything here reads it."""
    return json.dumps({"tracks": list(tracks)})


def track(kind: str, uid: int = UID, lang: str = "und", channels: int | None = None) -> dict:
    props: dict = {"uid": uid, "language": lang}
    if channels is not None:
        props["audio_channels"] = channels
    return {"type": kind, "properties": props}


def entry(path: str) -> dict:
    """The file's sweep cache entry as far as the staleness check reads it."""
    found = os.stat(path)
    return {"size": found.st_size, "mtime_ns": found.st_mtime_ns}


class Tools:
    """Stands in for mkvmerge, mkvpropedit and ffprobe. ``probes`` are handed
    out in order, the read before the edit then the read after; one that is an
    exception is raised in its turn, as ``propedit`` is."""

    def __init__(self, probes: list, listed: str, propedit=None):
        self.probes = list(probes)
        self.listed = listed
        self.propedit = propedit or subprocess.CompletedProcess([], 0, "Done.\n", "")
        self.calls: list[list[str]] = []
        self.timeouts: list[int] = []

    def run(self, args, timeout, **kwargs):
        self.calls.append(args)
        self.timeouts.append(timeout)
        if args[0] == "mkvmerge":
            return subprocess.CompletedProcess(args, 0, self.listed, "")
        if isinstance(self.propedit, Exception):
            raise self.propedit
        return self.propedit

    def probe(self, path):
        answer = self.probes.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def edits(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "mkvpropedit"]


@pytest.fixture
def mkv(tmp_path):
    """A Matroska file that exists, so the refusals are about anything else."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    return str(path)


@pytest.fixture
def installed(monkeypatch):
    """mkvtoolnix on the path, as far as the refusals look."""
    monkeypatch.setattr(mkvtag.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def tools(installed, monkeypatch):
    """Fakes for the tools, and a judging that needs no ffprobe: process()
    answers with a plan that has work to do."""
    monkeypatch.setattr(
        retag,
        "process",
        lambda job, dry_run: ProcessResult(Status.PENDING, needed_plan(job.path)),
    )

    def install(probes: list, listed: str = "", propedit=None) -> Tools:
        fake = Tools(
            probes,
            listed or listing(track("video", 1), track("audio", UID, "und", 6)),
            propedit,
        )
        monkeypatch.setattr(mkvtag.subprocess, "run", fake.run)
        monkeypatch.setattr(mkvtag, "probe", fake.probe)
        return fake

    return install


def commentary_case(lang: str | None = None, comment: int = 0, title: str = "") -> dict:
    """A 5.1 whose language and commentary flag the tests change."""
    return probe_data(video(0), audio(1, 6, lang, title=title, comment=comment))


@pytest.mark.parametrize(
    ("body", "edit"),
    [
        ({"lang": "ja"}, Edit("jpn")),
        ({"lang": "Japanese"}, Edit("jpn")),
        ({"lang": ""}, Edit("und")),
        ({"lang": " UND "}, Edit("und")),
        ({"flags": {"commentary": True}}, Edit(None, {"commentary": True})),
        ({"lang": "eng", "flags": {"forced": False}}, Edit("eng", {"forced": False})),
    ],
)
def test_an_edit_is_parsed_and_its_language_normalised(body, edit):
    assert retag.parse_edit(body) == edit


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({}, "nothing to change"),
        ({"flags": {}}, "nothing to change"),
        ({"lang": 5}, "lang is a language code"),
        ({"lang": "japan!"}, "not a language code"),
        ({"flags": ["commentary"]}, "flags is a map"),
        ({"flags": {"loud": True}}, "'loud' is not a flag"),
        ({"flags": {"commentary": "yes"}}, "commentary is on or off"),
    ],
)
def test_a_body_that_asks_for_nothing_usable_is_refused(body, said):
    with pytest.raises(ValueError, match=said):
        retag.parse_edit(body)


def test_the_tools_have_to_be_installed(monkeypatch, mkv):
    monkeypatch.setattr(mkvtag.shutil, "which", lambda name: None)
    assert retag.refusal(mkv, entry(mkv)) == (
        "mkvtoolnix is not installed, so track tags cannot be edited in place"
    )


def test_a_file_with_no_verdict_is_refused(installed, mkv):
    """The page shows tracks off the cache, so a file with no entry has shown
    nothing to aim at."""
    assert retag.refusal(mkv, None) == (
        "the file has no verdict yet; sweep or re-check the title first"
    )


def test_a_file_that_cannot_be_read_is_refused(installed, tmp_path):
    gone = str(tmp_path / "gone.mkv")
    assert "could not read the file" in (retag.refusal(gone, {"size": 1, "mtime_ns": 1}) or "")


def test_a_hardlink_is_refused_whatever_skip_hardlinks_says(installed, mkv):
    """A rewrite publishes a new inode and leaves the seeding copy whole; an
    edit in place changes both."""
    set_config(SKIP_HARDLINKS=False)
    os.link(mkv, mkv + ".link")
    assert "hardlinked" in (retag.refusal(mkv, entry(mkv)) or "")


def test_a_file_under_a_rewrite_is_refused(installed, mkv, monkeypatch):
    monkeypatch.setattr(mkvtag, "is_rewriting", lambda path: path == mkv)
    assert retag.refusal(mkv, entry(mkv)) == (
        "a rewrite of this file is under way; wait for it to finish"
    )


def test_a_file_changed_since_it_was_judged_is_refused(installed, mkv):
    """The page showed the tracks the cache holds, which are not this file's
    if it has changed since. Its own entry says so."""
    stale = entry(mkv) | {"size": entry(mkv)["size"] + 1}
    assert "changed since it was last checked" in (retag.refusal(mkv, stale) or "")
    assert retag.refusal(mkv, entry(mkv)) is None


def test_a_refused_file_is_answered_before_anything_is_read(tools, tmp_path):
    """Only Matroska is edited in place; the remux rule converts the rest."""
    fake = tools([])
    mp4 = tmp_path / "f.mp4"
    mp4.write_bytes(b"x")
    result = retag.apply(str(mp4), 1, Edit("jpn"), "admin", entry(str(mp4)))
    assert result.status is Outcome.REFUSED
    assert "Matroska" in result.detail
    assert fake.calls == []


def test_the_state_read_is_the_kinds_flags_off_the_disposition():
    """The commentary title regex is the planner's business; here a flag is
    the flag, so setting it on a track titled Commentary still reads as a
    change."""
    assert mkvtag._state(audio(1, 6, "eng", title="Commentary")) == {
        "lang": "eng",
        "commentary": False,
    }
    assert mkvtag._state(subtitle(2, None, forced=1)) == {
        "lang": "und",
        "forced": True,
        "sdh": False,
    }


def test_an_edit_is_aimed_by_uid(tools, mkv):
    fake = tools([commentary_case(), commentary_case("jpn", comment=1)])
    result = retag.apply(mkv, 1, Edit("jpn", {"commentary": True}), "admin", entry(mkv))

    assert result == retag.Result(mkv, Outcome.RETAGGED, "", "pending")
    assert fake.edits == [
        [
            "mkvpropedit",
            mkv,
            "--edit",
            f"track:={UID}",
            "--set",
            "language=jpn",
            "--set",
            "flag-commentary=1",
        ]
    ]


def test_the_listing_takes_the_probe_timeout_and_the_edit_the_rewrites(tools, mkv):
    """A timeout kills, and a kill mid header rewrite is the one way this
    could damage a file, so the write gets the ceiling a rewrite gets."""
    set_config(PROBE_TIMEOUT=45, FFMPEG_TIMEOUT=9000)
    fake = tools([commentary_case(), commentary_case("jpn")])
    retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv))
    assert fake.timeouts == [45, 9000]


def test_the_change_is_recorded_and_the_verdict_booked(tools, mkv):
    tools([commentary_case(), commentary_case("jpn", comment=1)])
    retag.apply(mkv, 1, Edit("jpn", {"commentary": True}), "admin", entry(mkv))

    (recorded,) = read_events()
    assert recorded["event"] == "retagged"
    assert (recorded["path"], recorded["index"], recorded["kind"]) == (mkv, 1, "audio")
    assert recorded["changed"] == {
        "lang": {"from": "und", "to": "jpn"},
        "commentary": {"from": False, "to": True},
    }
    assert recorded["by"] == "admin"
    # Booked at once, so the sheet reloads to the new verdict.
    stored = sweep_cache.read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    assert stored.files[mkv]["status"] == "pending"


@pytest.mark.parametrize(
    ("before", "after", "edit", "sets"),
    [
        # A flag the track already carries is not an edit of it.
        (
            commentary_case("eng", comment=1),
            commentary_case("jpn", comment=1),
            Edit("jpn", {"commentary": True}),
            ["language=jpn"],
        ),
        # A flag alone sets no language.
        (
            commentary_case("eng"),
            commentary_case("eng", comment=1),
            Edit(flags={"commentary": True}),
            ["flag-commentary=1"],
        ),
        (
            commentary_case("eng", comment=1),
            commentary_case("eng"),
            Edit(flags={"commentary": False}),
            ["flag-commentary=0"],
        ),
    ],
)
def test_only_what_differs_is_set(tools, mkv, before, after, edit, sets):
    fake = tools([before, after], listing(track("audio", UID, "eng", 6)))
    assert retag.apply(mkv, 1, edit, "admin", entry(mkv)).status is Outcome.RETAGGED
    assert fake.edits[0][4:] == [word for name in sets for word in ("--set", name)]


def test_an_edit_that_changes_nothing_touches_nothing(tools, mkv):
    fake = tools([commentary_case("jpn")])
    assert retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv)) == retag.Result(
        mkv, Outcome.UNCHANGED, "already tagged that way"
    )
    assert fake.calls == []
    assert read_events() == []


@pytest.mark.parametrize(
    ("stream", "edit", "said"),
    [
        (
            audio(1, 6, "eng", title="Director's Commentary"),
            Edit(flags={"commentary": False}),
            (
                "commentary is already off; the rules read it off the title "
                '"Director\'s Commentary" instead'
            ),
        ),
        (
            subtitle(1, "eng", title="Forced"),
            Edit(flags={"forced": False}),
            "forced is already off; the rules read it off the title 'Forced' instead",
        ),
        (
            subtitle(1, "eng", title="English (SDH)"),
            Edit(flags={"sdh": False}),
            "sdh is already off; the rules read it off the title 'English (SDH)' instead",
        ),
    ],
)
def test_clearing_a_flag_the_title_carries_is_refused_rather_than_agreed_with(
    tools, mkv, stream, edit, said
):
    """The page's switch was on because the rules read the title, not because
    the bit was set. Clearing a bit the file never had would be reported as
    "already so", which is the opposite of what the reader is told."""
    fake = tools([probe_data(video(0), stream)])
    result = retag.apply(mkv, 1, edit, "admin", entry(mkv))
    assert result == retag.Result(mkv, Outcome.REFUSED, said)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("index", "edit", "said"),
    [
        (0, Edit("jpn"), "stream 0 is not an audio or subtitle track"),
        (9, Edit("jpn"), "stream 9 is not an audio or subtitle track"),
        (1, Edit(None, {"forced": True}), "forced is a flag on subtitle tracks, not audio"),
        # Only the API can aim the other way; the editor offers each flag on
        # its own kind.
        (
            2,
            Edit(None, {"commentary": True}),
            "commentary is a flag on audio tracks, not subtitle",
        ),
    ],
)
def test_a_track_that_cannot_take_the_edit_is_refused(tools, mkv, index, edit, said):
    fake = tools([probe_data(video(0), audio(1, 6), subtitle(2))])
    result = retag.apply(mkv, index, edit, "admin", entry(mkv))
    assert result == retag.Result(mkv, Outcome.REFUSED, said)
    assert fake.calls == []


def test_the_track_is_found_by_its_place_among_its_kind(tools, mkv):
    """ffprobe counts every stream and mkvmerge every track, which part company
    at the first attachment. The second audio stream is the second audio
    track, whatever either is numbered."""
    font = {"index": 3, "codec_type": "attachment"}
    before = probe_data(video(0), audio(1, 6), audio(2, 2, None), font)
    after = probe_data(video(0), audio(1, 6), audio(2, 2, "jpn"), font)
    fake = tools(
        [before, after],
        listing(track("video", 1), track("audio", 2, "eng", 6), track("audio", 3, "und", 2)),
    )
    assert retag.apply(mkv, 2, Edit("jpn"), "admin", entry(mkv)).status is Outcome.RETAGGED
    assert fake.edits[0][3] == "track:=3"


@pytest.mark.parametrize(
    ("listed", "said"),
    [
        (listing(track("video", 1)), "do not list the same audio tracks"),
        # More on mkvmerge's side: ffprobe drops a track it cannot read, and
        # counting on from there would land the edit on the wrong one.
        (listing(track("audio", 7, "und", 6), track("audio", UID, "und", 6)), "same audio"),
        (listing(track("audio", UID, "und", 2)), "channels differ"),
        (listing(track("audio", UID, "eng", 6)), "language differs"),
        (listing({"type": "audio", "properties": {"language": "und"}}), "no UID"),
        ("not json", "unparseable"),
        (json.dumps([]), "same audio tracks"),
    ],
)
def test_a_track_mkvmerge_describes_differently_is_not_edited(tools, mkv, listed, said):
    """Nothing is set until both tools agree which track is meant."""
    fake = tools([commentary_case()], listed)
    result = retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv))
    assert result.status is Outcome.FAILED
    assert said in result.detail
    assert fake.edits == []


def test_mkvmerge_may_read_the_language_off_either_element(tools, mkv):
    """A file tagged by another tool can carry the newer IETF element alone,
    which is what ffprobe then reads."""
    listed = json.dumps(
        {"tracks": [{"type": "audio", "properties": {"uid": UID, "language_ietf": "ja"}}]}
    )
    tools([commentary_case("jpn"), commentary_case("eng")], listed)
    assert retag.apply(mkv, 1, Edit("eng"), "admin", entry(mkv)).status is Outcome.RETAGGED


@pytest.mark.parametrize(
    ("stdout", "stderr", "kept"),
    [
        # mkvtoolnix writes its errors to stdout; stderr is the fallback.
        ("Error: The value 'xx' is not a valid ISO 639 language code.\n", "", "'xx'"),
        ("", "Error: cannot open the file\n", "cannot open"),
        # The tail, where the error is, not the whole of a long run.
        ("x" * 500 + " the end", "", "the end"),
    ],
)
def test_a_tool_that_fails_is_reported_in_its_own_words(tools, mkv, stdout, stderr, kept):
    tools([commentary_case()], propedit=subprocess.CompletedProcess([], 2, stdout, stderr))
    result = retag.apply(mkv, 1, Edit("xx"), "admin", entry(mkv))
    assert result.status is Outcome.FAILED
    assert result.detail.startswith("mkvpropedit failed (2): ")
    assert kept in result.detail
    assert len(result.detail) < 400
    assert read_events() == []


def test_a_tool_that_hangs_is_a_failure(tools, mkv):
    set_config(FFMPEG_TIMEOUT=300)
    tools([commentary_case()], propedit=subprocess.TimeoutExpired(["mkvpropedit"], 300))
    result = retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv))
    assert result == retag.Result(mkv, Outcome.FAILED, "mkvpropedit timed out after 300s")


def test_a_warning_is_not_a_failure(tools, mkv):
    """mkvtoolnix exits 1 for a warning with the work done, and 2 for an
    error."""
    tools(
        [commentary_case(), commentary_case("jpn")],
        propedit=subprocess.CompletedProcess([], 1, "Warning: something.\nDone.\n", ""),
    )
    assert retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv)).status is Outcome.RETAGGED


@pytest.mark.parametrize(
    "probes",
    [
        [ProbeError("ffprobe failed: no")],
        # After the edit, which is still a failure the page hears about.
        [commentary_case(), ProbeError("ffprobe failed: no")],
    ],
)
def test_a_probe_that_fails_is_a_failure(tools, mkv, probes):
    tools(probes)
    assert retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv)) == retag.Result(
        mkv, Outcome.FAILED, "ffprobe failed: no"
    )


@pytest.mark.parametrize(
    ("read_back", "said", "recorded"),
    [
        # Something else took: the history has what did move, and the page the
        # words rather than a success.
        (
            commentary_case("ger"),
            "the edit did not take: the track reads language ger, commentary off",
            {"lang": {"from": "und", "to": "ger"}},
        ),
        # Nothing moved, so nothing to record.
        (
            commentary_case(),
            "the edit did not take: the track reads language und, commentary off",
            None,
        ),
        # The file changed under the edit.
        (probe_data(video(0)), "the track is gone from the file after the edit", None),
    ],
)
def test_a_file_that_reads_back_wrong_is_a_failure(tools, mkv, read_back, said, recorded):
    """mkvpropedit said yes and ffprobe disagrees. The verdict is booked
    against the file as it now is either way."""
    tools([commentary_case(), read_back])
    result = retag.apply(mkv, 1, Edit("jpn", {"commentary": True}), "admin", entry(mkv))
    assert (result.status, result.detail) == (Outcome.FAILED, said)
    if recorded is None:
        assert read_events() == []
    else:
        assert result.verdict == "pending"
        (line,) = read_events()
        assert line["changed"] == recorded


def test_edits_take_turns(tools, mkv, monkeypatch):
    """Two mkvpropedit runs interleaved on one header can leave the file
    unreadable, and two requests can name one file."""
    tools([commentary_case(), commentary_case("jpn")] * 2)
    steps: list[str] = []

    def slow_propedit(path, uid, before, wanted):
        steps.append("in")
        time.sleep(0.05)
        steps.append("out")

    monkeypatch.setattr(mkvtag, "_propedit", slow_propedit)
    threads = [
        threading.Thread(target=retag.apply, args=(mkv, 1, Edit("jpn"), "admin", entry(mkv)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert steps == ["in", "out", "in", "out"]


def test_the_verdict_is_reached_in_the_titles_language_and_the_servers_told(
    tools, mkv, monkeypatch
):
    """As after a rewrite: Plex's track list is read off the file, and the
    file has changed. The *arr is apply_all's, once per title."""
    tools([commentary_case(), commentary_case("jpn")])
    refreshed: list[str] = []
    monkeypatch.setattr(retag, "refresh_servers", refreshed.append)
    dune = library.Title("arr:radarr:7", "Dune", "/m/Dune", "movie", lang="jpn")
    judged: list[str | None] = []
    monkeypatch.setattr(
        retag,
        "process",
        lambda job, dry_run: (
            judged.append(job.lang),
            ProcessResult(Status.CONFORM, Plan(path=job.path)),
        )[1],
    )
    result = retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv), title=dune)
    assert (result.status, result.verdict) == (Outcome.RETAGGED, "conform")
    assert judged == ["jpn"]
    assert refreshed == [mkv]


def test_our_own_edit_keeps_the_rewrite_record(tools, mkv, monkeypatch):
    """mkvpropedit moves the file's size and mtime, which is what tells a
    record its file was rewritten by something else. Ours was not."""
    fake = tools([commentary_case(), commentary_case("jpn")])
    rewrites.record(mkv, sweep_cache.cache_key(mkv, "jpn"), REWROTE)
    edit = fake.run

    def touching(args, timeout, **kwargs):
        answer = edit(args, timeout, **kwargs)
        if args[0] == "mkvpropedit":
            Path(mkv).write_bytes(b"xx")
        return answer

    monkeypatch.setattr(mkvtag.subprocess, "run", touching)

    assert retag.apply(mkv, 1, Edit("jpn"), "admin", entry(mkv)).status is Outcome.RETAGGED
    assert rewrites.against({mkv: entry(mkv)}) == {mkv: REWROTE}


class FakeArr:
    def __init__(self):
        self.rescanned: list[int] = []

    def rescan(self, item_id):
        self.rescanned.append(item_id)


def judged_at(path: str) -> None:
    """A stored verdict for the file as it stands, so apply_all finds an
    entry and the staleness check passes."""
    found = os.stat(path)
    sweep_cache.update(
        path,
        sweep_cache.FileKey(found.st_size, found.st_mtime_ns, 1, "jpn"),
        sweep_cache.Verdict(Status.CONFORM),
        Policy.from_config().fingerprint(),
    )


def test_apply_all_answers_for_each_file_and_rescans_the_title_once(
    tools, mkv, tmp_path, monkeypatch
):
    """One refusal stops nothing else, and two changed files of one title are
    one rescan, since the *arr's media info is per title."""
    mp4 = tmp_path / "g.mp4"
    mp4.write_bytes(b"x")
    other = tmp_path / "h.mkv"
    other.write_bytes(b"x")
    for path in (mkv, str(mp4), str(other)):
        judged_at(path)
    tools([commentary_case(), commentary_case("jpn")] * 2)
    arr = FakeArr()
    dune = library.Title(
        "arr:radarr:7", "Dune", str(tmp_path), "movie", lang="jpn", arr=arr, item_id=7
    )
    monkeypatch.setattr(library, "known", lambda: library.Shelf([dune]))

    results = retag.apply_all([(str(mp4), 1), (mkv, 1), (str(other), 1)], Edit("jpn"), "admin")

    assert [result.status for result in results] == [
        Outcome.REFUSED,
        Outcome.RETAGGED,
        Outcome.RETAGGED,
    ]
    assert arr.rescanned == [7]


def test_apply_all_hands_each_file_its_entry_and_title(installed, mkv, tmp_path, monkeypatch):
    judged_at(mkv)
    dune = library.Title("arr:radarr:7", "Dune", str(tmp_path), "movie", lang="jpn")
    monkeypatch.setattr(library, "known", lambda: library.Shelf([dune]))
    seen: list[tuple] = []

    def fake_apply(path, index, edit, by, stored, title=None):
        seen.append((path, index, edit, by, stored and stored["mtime_ns"], title))
        return retag.Result(path, Outcome.REFUSED, "no")

    monkeypatch.setattr(retag, "apply", fake_apply)
    edit = Edit("jpn")
    retag.apply_all([(mkv, 1), ("/elsewhere/x.mkv", 2)], edit, "admin")
    assert seen == [
        (mkv, 1, edit, "admin", os.stat(mkv).st_mtime_ns, dune),
        ("/elsewhere/x.mkv", 2, edit, "admin", None, None),
    ]


def test_a_title_nothing_changed_in_is_not_rescanned(installed, mkv, tmp_path, monkeypatch):
    judged_at(mkv)
    arr = FakeArr()
    dune = library.Title(
        "arr:radarr:7", "Dune", str(tmp_path), "movie", lang="jpn", arr=arr, item_id=7
    )
    monkeypatch.setattr(library, "known", lambda: library.Shelf([dune]))
    monkeypatch.setattr(
        retag, "apply", lambda *args, **kwargs: retag.Result(mkv, Outcome.REFUSED, "no")
    )
    retag.apply_all([(mkv, 1)], Edit("jpn"), "admin")
    assert arr.rescanned == []


def test_a_result_drops_what_it_has_nothing_to_say():
    assert retag.Result("/f.mkv", Outcome.UNCHANGED, "already tagged that way").as_json() == {
        "path": "/f.mkv",
        "status": "unchanged",
        "detail": "already tagged that way",
    }


def test_the_rules_write_a_tag_without_the_page_s_checks(tools, mkv):
    """write_lang is the rules' way in. It takes the plan's word for what the
    file holds, where a page must prove the tracks it showed are still there."""
    fake = tools([commentary_case(), commentary_case("jpn")])
    assert mkvtag.write_lang(mkv, 1, "jpn").status is Outcome.RETAGGED
    assert fake.edits[0][4:] == ["--set", "language=jpn"]


def test_a_file_the_tools_cannot_touch_refuses_the_tag(mkv, monkeypatch):
    """The caller's cue to write the tag the slow way, in a rewrite."""
    monkeypatch.setattr(mkvtag.shutil, "which", lambda name: None)
    refused = mkvtag.write_lang(mkv, 1, "jpn")
    assert refused.status is Outcome.REFUSED
    assert "mkvtoolnix is not installed" in refused.detail


def test_a_track_already_in_that_language_is_unchanged(tools, mkv):
    """A race with the page's own editor. Nothing is written and nothing is
    booked."""
    tools([commentary_case("jpn")])
    assert mkvtag.write_lang(mkv, 1, "jpn").status is Outcome.UNCHANGED
