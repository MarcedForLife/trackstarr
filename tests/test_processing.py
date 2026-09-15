"""process() outcomes and their side effects. No media, no network."""

import contextlib
import fcntl
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import cache_verdict, configured_arr, needed_plan, read_events, set_config
from trackstarr import config, estimate, mkvtag, pauses, planner, processing, sweep_cache
from trackstarr.executor import Cancel, Outcome
from trackstarr.media import ProbeError
from trackstarr.planner import OutStream, Plan
from trackstarr.policy import Policy
from trackstarr.processing import Job, process
from trackstarr.status import Status
from trackstarr.sweep import remember


def stereo_from(*, generated: bool) -> Plan:
    """A plan over one 5.1 track, either copied or downmixed beside."""
    tracks = [{"index": 0, "kind": "audio", "codec": "eac3", "channels": 6}]
    streams = [OutStream(src=0, kind="audio")]
    if generated:
        streams.append(OutStream(src=0, kind="audio", encode=True, channels=2))
    return needed_plan(tracks=tracks, streams=streams)


def test_a_rewrite_that_moved_no_track_records_nothing_to_compare():
    """A remux or a cleared title leaves the same streams in the same order, so
    a before-and-after is one list twice, on the verdict and on every line of
    the history."""
    assert processing._before(stereo_from(generated=False)) == {}


def test_a_rewrite_records_the_tracks_it_started_from():
    """The file itself is the after and the plan is gone by the time anything
    reads this, so the before is the one thing nothing else could recover."""
    told = processing._before(stereo_from(generated=True))
    assert [track["index"] for track in told["was"]] == [0]
    assert told["added"] == [1]


def test_deferred_rewrite_is_not_a_failure(stub_rewrite):
    """A benign mid-rewrite race must not alert like a corruption."""
    stub_rewrite(
        needed_plan(),
        Outcome.DEFERRED,
        "source changed during the rewrite",
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "deferred"
    assert "source changed" in result.detail


def test_a_rewritten_file_notifies_media_servers(monkeypatch, stub_rewrite):
    stub_rewrite(needed_plan())
    refreshed = []
    monkeypatch.setattr(processing, "refresh_servers", refreshed.append)

    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "modified"
    assert refreshed == ["/x.mkv"]


def test_a_video_in_a_container_we_never_write_is_its_own_verdict(tmp_path):
    """Skipped put an AVI in with the hardlinked and the silent, which are both
    about the moment. This one is about the file, and the reason travels with
    it."""
    stale = tmp_path / "old.avi"
    stale.write_bytes(b"not really an avi")
    result = process(Job(str(stale)), dry_run=True)
    assert result.status is Status.UNSUPPORTED
    assert result.plan is not None
    assert result.plan.skip == "container .avi not in ALLOWED_EXTS"


def test_a_file_that_is_not_a_video_at_all_is_still_only_skipped(tmp_path):
    """Nothing walks these, but `trackstarr fix` takes a path by hand, and
    "Unsupported" for a text file promises a container that could be added."""
    named = tmp_path / "notes.txt"
    named.write_text("not a film")
    assert process(Job(str(named)), dry_run=True).status is Status.SKIP


def test_report_mode_bottoms_out_in_process(monkeypatch):
    """No caller can rewrite on REWRITE_MODE's bottom rung, whatever dry_run it
    passes."""
    set_config(REWRITE_MODE="report")
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda path, lang, policy=None: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("report mode must not rewrite")
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "pending"


def test_a_paused_file_is_planned_and_reported_but_never_rewritten(monkeypatch):
    """The whole point: a title somebody is watching goes on being judged, so
    the library still shows the work, and nothing touches the file."""
    pauses.place("/data/media/movies/Dune (2024)", by="operator", reason="watching it")
    set_config(MEDIA_DIRS=["/data/media/movies"])
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda path, lang, policy=None: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("a hold must not rewrite")
    )
    result = process(Job("/data/media/movies/Dune (2024)/Dune (2024).mkv"), dry_run=False)
    assert result.status is Status.PENDING
    # The row and pending.tsv say why this one is not being rewritten, since
    # the plan's own reasons would read as work about to happen.
    assert result.detail == "paused (watching it)"
    assert pauses.paused("/data/media/movies/Dune (2024)").as_json()["by"] == "operator"
    assert "watching it" in result.detail


def test_a_pause_on_one_title_leaves_the_rest_alone(stub_rewrite):
    """A hold is not a pause; everything else goes on being rewritten."""
    pauses.place("/data/media/movies/Dune (2024)", by="operator")
    stub_rewrite(needed_plan(path="/data/media/movies/Arrival (2016)/Arrival (2016).mkv"))
    result = process(Job("/data/media/movies/Arrival (2016)/Arrival (2016).mkv"), dry_run=False)
    assert result.status is Status.MODIFIED


def held_slot():
    """One claimed rewrite slot; closing the handle releases it, which is
    exactly what process() does around apply_plan."""
    return contextlib.closing(processing._claim_slot())


def _is_locked(name: str) -> bool:
    """Whether another holder has the named slot; closing the probe handle
    releases whatever this took."""
    with open(os.path.join(processing._lock_dir(), name)) as probe_file:
        try:
            fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def test_rewrites_hold_a_cross_process_file_lock():
    """A sweep run via docker exec must queue behind serve's rewrites."""
    with held_slot():
        assert _is_locked("rewrite.lock.0")
    assert not _is_locked("rewrite.lock.0")


def test_every_slot_gets_its_own_lock_file():
    """Two processes sharing one lock file would serialize whatever the
    budget says."""
    set_config(MAX_CONCURRENT_REWRITES=3)
    with held_slot(), held_slot():
        held = sorted(
            name
            for name in os.listdir(processing._lock_dir())
            if name.startswith("rewrite.lock.") and _is_locked(name)
        )
    assert held == ["rewrite.lock.0", "rewrite.lock.1"]


def test_all_slots_held_yields_false_while_a_rewrite_runs():
    """Startup's WORK_DIR cleanup relies on this to tell a live rewrite from an
    orphan."""
    with held_slot(), processing.all_slots_held() as held:
        assert held is False
    with processing.all_slots_held() as held:
        assert held is True


def test_a_writable_state_dir_passes_and_is_created():
    assert processing.state_dir_errors() == []
    assert os.path.isdir(config.STATE_DIR), "the check should create STATE_DIR"


def test_an_unusable_state_dir_is_an_error(tmp_path, monkeypatch):
    """A STATE_DIR serve cannot use. A blocker file rather than mode bits, which
    mean nothing to the root the in-image suite runs as."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker))
    errors = processing.state_dir_errors()
    assert len(errors) == 1
    assert "not usable" in errors[0]


def test_a_state_dir_check_tolerates_a_slot_another_process_holds():
    """The question is whether the directory can be written, so a busy slot must
    not read as a broken mount."""
    with held_slot():
        assert processing.state_dir_errors() == []


def test_all_slots_held_sees_slots_beyond_our_budget():
    """A process started with a bigger budget can hold a slot past our range;
    its lock file exists on disk, so it must be checked too."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    os.makedirs(processing._lock_dir(), exist_ok=True)
    with open(os.path.join(processing._lock_dir(), "rewrite.lock.7"), "w") as foreign:
        fcntl.flock(foreign, fcntl.LOCK_EX)
        with processing.all_slots_held() as held:
            assert held is False


def _peak_concurrency(monkeypatch, jobs: int) -> int:
    """Most rewrite slots held at once across ``jobs`` racing threads, with the
    poll interval shortened."""
    monkeypatch.setattr(processing, "_SLOT_POLL_SECONDS", 0.005)
    running = 0
    peak = 0
    counter_lock = threading.Lock()

    def hold() -> None:
        nonlocal running, peak
        with held_slot():
            with counter_lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with counter_lock:
                running -= 1

    threads = [threading.Thread(target=hold) for _ in range(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return peak


@pytest.mark.parametrize(
    ("budget", "jobs"),
    [(1, 4), (3, 8)],
    ids=["the default is still exclusive", "a raised budget is shared"],
)
def test_the_budget_bounds_concurrent_rewrites(monkeypatch, budget, jobs):
    set_config(MAX_CONCURRENT_REWRITES=budget)
    assert _peak_concurrency(monkeypatch, jobs) == budget


def test_a_raising_rewrite_releases_its_slot(monkeypatch):
    """Leak one lock handle and the pool drains, hanging every later rewrite."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    for _ in range(3):
        with pytest.raises(RuntimeError), held_slot():
            raise RuntimeError("ffmpeg exploded")
    assert _peak_concurrency(monkeypatch, 2) == 1


def test_a_probe_failure_during_a_rewrite_is_reported_not_raised(tmp_path, monkeypatch):
    """One corrupt file must not take the rest of a sweep down with it."""
    set_config(MEDIA_DIRS=[str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")

    def fail(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        raise ProbeError("moov atom not found")

    monkeypatch.setattr(processing, "apply_plan", fail)
    monkeypatch.setattr(
        processing, "build_plan", lambda p, lang, policy=None: needed_plan(str(path))
    )
    result = process(Job(str(path)), dry_run=False)
    assert result.status is Status.FAILED
    assert "moov atom not found" in result.detail


def test_a_rewritten_file_asks_its_arr_to_rescan(tmp_path, stub_rewrite):
    """Otherwise Radarr keeps reporting the old size and media info."""
    set_config(MEDIA_DIRS=[str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    rescanned: list[int] = []

    arr = configured_arr()
    arr.rescan = lambda item_id: rescanned.append(item_id)
    stub_rewrite(needed_plan(str(path)))

    result = process(Job(str(path), "eng", 12, arr), dry_run=False)
    assert result.status is Status.MODIFIED
    assert rescanned == [12]


def test_a_rewrite_waits_for_a_busy_slot_rather_than_failing(monkeypatch):
    """The budget is a queue, not a limit that rejects: an import arriving
    mid-sweep waits its turn."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    held = processing._claim_slot()
    released: list[bool] = []

    def release_on_first_wait(cancel):
        """Stand in for the other worker finishing while we poll."""
        if not released:
            released.append(True)
            held.close()

    monkeypatch.setattr(processing, "_pause_for_slot", release_on_first_wait)
    got = processing._claim_slot()
    try:
        assert released == [True]
    finally:
        got.close()


def test_a_skip_that_won_the_race_for_a_slot_hands_it_straight_back(monkeypatch):
    """The slot came free as the skip landed. Keeping it would hold the budget
    against a rewrite that is no longer going to happen."""
    cancel = Cancel("/x.mkv")
    locking = processing._try_lock

    def lock_then_skip(name):
        cancel.ask()
        return locking(name)

    monkeypatch.setattr(processing, "_try_lock", lock_then_skip)
    assert processing._claim_slot(cancel) is None
    assert not _is_locked("rewrite.lock.0")


def test_a_file_skipped_while_the_slots_are_full_gives_up_waiting(monkeypatch):
    """Every slot is somebody else's encode. Without this the skip is answered
    an hour later, long after the page said the file had been left alone."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    cancel = Cancel("/x.mkv")
    with held_slot():
        monkeypatch.setattr(processing, "_pause_for_slot", lambda waiting: waiting.ask())
        assert processing._claim_slot(cancel) is None


def tag_plan(path: str = "/x.mkv") -> Plan:
    """A plan whose one change is a language tag, which is the fast path's
    whole condition."""
    return Plan(
        path=path,
        reasons=["tag audio 1 as jpn"],
        rules={"tag_original"},
        streams=[OutStream(src=1, kind="audio", lang="jpn")],
    )


@pytest.fixture
def stub_tag(monkeypatch):
    """Plan a tag-only rewrite and answer for mkvpropedit. Returns the list the
    calls land in, so a test can say the tag was aimed where the plan put it."""

    def _stub(
        status: mkvtag.Outcome | None = None, detail: str = "", plan: Plan | None = None
    ) -> list:
        calls: list = []
        monkeypatch.setattr(
            processing, "build_plan", lambda path, lang, policy=None: plan or tag_plan()
        )

        def write_lang(path: str, index: int, lang: str, commit=None):
            # Where the real editor asks: the lock is held and the edit is
            # settled, so a no means nothing was written.
            if commit is not None and not commit():
                return mkvtag.Result(path, mkvtag.Outcome.STOPPED, "stopped before the edit")
            calls.append((path, index, lang))
            return mkvtag.Result(path, status or mkvtag.Outcome.RETAGGED, detail)

        monkeypatch.setattr(processing.mkvtag, "write_lang", write_lang)
        return calls

    return _stub


def _never_rewrite(monkeypatch):
    """Fail the test rather than the file if ffmpeg is reached at all."""

    def refuse(*args, **kwargs):
        raise AssertionError("the file was rewritten")

    monkeypatch.setattr(processing, "apply_plan", refuse)


def test_a_tag_is_written_in_place_rather_than_rewritten(monkeypatch, stub_tag):
    """mkvpropedit writes the header in a second where ffmpeg copies the file
    to do it."""
    _never_rewrite(monkeypatch)
    calls = stub_tag()

    result = process(Job("/x.mkv"), dry_run=False)

    assert result.status is Status.MODIFIED
    assert calls == [("/x.mkv", 1, "jpn")]


def test_the_in_place_line_is_never_a_rewrite_speed(monkeypatch, stub_tag):
    """A header written in a second on a two-hour film would tell the estimates
    the machine rewrites at thousands of times realtime."""
    _never_rewrite(monkeypatch)
    stub_tag(plan=replace(tag_plan(), src_duration=7200.0))

    process(Job("/x.mkv"), dry_run=False)

    (entry,) = [line for line in read_events() if line["event"] == "modified"]
    assert entry["in_place"] is True
    assert entry["duration"] == 7200.0
    assert estimate._worked(entry) is None


def test_the_in_place_line_claims_no_ride_alongs(monkeypatch, stub_tag):
    """Nothing rode along, since nothing was rewritten. A line saying otherwise
    credits the rule with a title it never cleared."""
    _never_rewrite(monkeypatch)
    stub_tag(
        plan=replace(
            tag_plan(),
            incidental=["clear release tags on audio 1"],
            incidental_rules={"release_tags"},
        )
    )

    process(Job("/x.mkv"), dry_run=False)

    (entry,) = [line for line in read_events() if line["event"] == "modified"]
    assert "incidental" not in entry
    assert entry["rules"] == ["tag_original"]


def test_a_file_that_cannot_be_edited_in_place_is_rewritten(monkeypatch, stub_tag):
    """An MP4, a hardlink or an image without mkvtoolnix. The rewrite writes
    the same tag, at the price of copying the file."""
    rewritten = []

    def apply_plan(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        rewritten.append(plan.path)
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "apply_plan", apply_plan)
    stub_tag(mkvtag.Outcome.REFUSED, "hardlinked")

    result = process(Job("/x.mkv"), dry_run=False)

    assert result.status is Status.MODIFIED
    assert rewritten == ["/x.mkv"]


def test_a_tag_that_failed_is_not_retried_as_a_rewrite(monkeypatch, stub_tag):
    """mkvpropedit reaching the file and leaving it wrong is a failure to
    report, not a reason to copy 60GB in the hope of better."""
    _never_rewrite(monkeypatch)
    stub_tag(mkvtag.Outcome.FAILED, "the edit did not take")

    result = process(Job("/x.mkv"), dry_run=False)

    assert result.status is Status.FAILED
    assert result.detail == "the edit did not take"


def test_a_rewrite_ordered_by_anything_else_writes_the_tag_itself():
    """The fast path is for the tag alone. A rewrite already happening carries
    it for free, and the ride-alongs go with it."""
    plan = tag_plan()
    plan.rules.add("order")
    assert processing._tag_only(plan) is None


def test_a_plan_with_no_tag_to_write_is_not_a_fast_path():
    """The rule names itself only where it has a stream to stamp, so this is
    the door staying shut rather than a case the rules reach."""
    assert processing._tag_only(replace(tag_plan(), streams=[OutStream(0, "video")])) is None


def test_a_tagged_title_is_rescanned_like_any_other(monkeypatch, stub_tag):
    """The *arr reads its media info off the file, which now says something
    different."""
    _never_rewrite(monkeypatch)
    stub_tag()
    rescanned: list[int] = []
    arr = configured_arr()
    arr.rescan = lambda item_id: rescanned.append(item_id)

    process(Job("/x.mkv", "jpn", 12, arr), dry_run=False)

    assert rescanned == [12]


def test_a_tag_something_else_wrote_first_is_nobody_s_rewrite(monkeypatch, stub_tag):
    """A race with the track editor. The file is as the plan wanted it, so
    there is nothing to book as ours."""
    _never_rewrite(monkeypatch)
    stub_tag(mkvtag.Outcome.UNCHANGED, "already tagged that way")

    result = process(Job("/x.mkv"), dry_run=False)

    assert result.status is Status.CONFORM
    assert [line for line in read_events() if line["event"] == "modified"] == []


def test_a_file_skipped_while_it_was_planned_is_never_edited(monkeypatch, stub_tag):
    """The skip landed during the probe, which is a read: nothing has changed
    yet, so nothing should. Without the check the page says the file was left
    alone and mkvpropedit writes the tag a moment later."""
    _never_rewrite(monkeypatch)
    calls = stub_tag()
    cancel = Cancel("/x.mkv")
    planned = processing.build_plan
    monkeypatch.setattr(
        processing,
        "build_plan",
        lambda path, lang, policy=None: (cancel.ask(), planned(path, lang, policy))[1],
    )

    result = process(Job("/x.mkv"), dry_run=False, cancel=cancel)

    assert result.status is Status.DEFERRED
    assert result.detail == processing.STOPPED_BEFORE_START
    assert calls == [], "the editor was never reached"


def test_a_skip_that_reaches_the_gate_is_deferred_rather_than_rewritten(monkeypatch, stub_tag):
    """A stopped edit is not a refusal: rewriting instead would write the tag
    the skip just stopped, the slow way."""
    _never_rewrite(monkeypatch)
    stub_tag(mkvtag.Outcome.STOPPED, "stopped before the edit")

    result = process(Job("/x.mkv"), dry_run=False)

    assert result.status is Status.DEFERRED
    assert [line for line in read_events() if line["event"] == "modified"] == []


def test_a_skip_after_the_edit_began_reports_what_really_happened(monkeypatch, stub_tag):
    """The header is written and the skip has nothing left to prevent. Booking
    it as deferred would leave the library showing the old tracks."""
    _never_rewrite(monkeypatch)
    stub_tag()
    cancel = Cancel("/x.mkv")
    assert cancel.commit(), "the edit is under way"

    result = process(Job("/x.mkv"), dry_run=False, cancel=cancel)

    assert not cancel.ask(), "too late to stop it"
    assert result.status is Status.MODIFIED
    assert len([line for line in read_events() if line["event"] == "modified"]) == 1


def test_a_rewrite_skipped_while_it_waited_for_a_slot_is_deferred(monkeypatch, stub_rewrite):
    """Every slot on the machine is busy and the file has been taken off its
    run. It settles now rather than after an hour of somebody else's encode."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    stub_rewrite(needed_plan())
    monkeypatch.setattr(
        processing, "apply_plan", lambda *a, **k: pytest.fail("nothing claimed a slot")
    )
    with held_slot():
        monkeypatch.setattr(processing, "_pause_for_slot", lambda waiting: waiting.ask())
        result = process(Job("/x.mkv"), dry_run=False, cancel=Cancel("/x.mkv"))

    assert result.status is Status.DEFERRED
    assert result.detail == processing.STOPPED_BEFORE_START
    # No line of its own: the skip that stopped it wrote one.
    assert not [line for line in read_events() if line["event"] == "deferred"]


def test_observed_policy_is_used_even_if_settings_change_before_planning(monkeypatch, tmp_path):
    path = str(tmp_path / "film.mkv")
    (tmp_path / "film.mkv").write_bytes(b"file")
    set_config(LANGUAGES=("eng",))
    monkeypatch.setattr(planner, "probe", lambda path: {"streams": []})
    with sweep_cache.observing(path) as observation:
        key = sweep_cache.cache_key(path, None)
        before = observation.policy.fingerprint()
        set_config(LANGUAGES=("fre",))
        result = process(Job(path), True, policy=observation.policy)
        assert result.plan.policy.fingerprint() == before
        remember(path, key, result, observation)
    stored = sweep_cache.read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    assert stored.current is False
    assert path in stored.files


def test_explicit_planning_is_independent_of_ambient_observation(monkeypatch, tmp_path):
    path = tmp_path / "film.mkv"
    path.write_bytes(b"file")
    set_config(LANGUAGES=("eng",))
    chosen = Policy.from_config()
    monkeypatch.setattr(planner, "probe", lambda path: {"streams": []})
    expected = planner.build_plan(str(path), None, chosen)
    set_config(LANGUAGES=("fre",))
    with sweep_cache.observing(str(path)):
        set_config(LANGUAGES=("ger",))
        assert planner.build_plan(str(path), None, chosen) == expected
        assert planner.new_plan(str(path), None).policy == Policy.from_config()
        assert process(Job(str(path)), True, policy=chosen).plan == expected


def test_rewrite_recheck_keeps_policy_when_settings_change(monkeypatch, tmp_path):
    path = tmp_path / "film.mkv"
    path.write_bytes(b"file")
    set_config(LANGUAGES=("eng",), REWRITE_MODE="all")
    chosen = Policy.from_config()
    planned = []

    def build(path, lang, policy):
        planned.append(policy)
        return needed_plan(path, policy=policy) if len(planned) == 1 else Plan(path, policy)

    def apply(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        set_config(LANGUAGES=("fre",))
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "build_plan", build)
    monkeypatch.setattr(processing, "apply_plan", apply)
    with sweep_cache.observing(str(path), policy=chosen) as observation:
        key = sweep_cache.cache_key(str(path), None)
        result = process(Job(str(path)), False, policy=chosen)
        remember(str(path), key, result, observation)
    assert planned == [chosen, chosen]
    assert result.became.verdict.status is Status.CONFORM
    stored = sweep_cache.read(sweep_cache.cache_path(), chosen.fingerprint())
    assert stored.current
    assert stored.files[str(path)]["status"] == "conform"
    # A pinned policy does not pin operational pause/report controls.
    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(
        processing, "build_plan", lambda path, lang, policy: needed_plan(path, policy=policy)
    )
    assert process(Job(str(path)), False, policy=chosen).status is Status.PENDING


def test_a_tag_owns_the_file_while_it_writes_the_header(monkeypatch, stub_tag, tmp_path):
    """The verdict comes from a probe of what the edit left, so the file is the
    edit's from before mkvpropedit is called until that verdict is booked."""
    _never_rewrite(monkeypatch)
    path = str(tmp_path / "x.mkv")
    Path(path).write_bytes(b"file")
    owned = []
    stub_tag(plan=tag_plan(path))
    write_lang = processing.mkvtag.write_lang

    def watched(target, index, lang, commit=None):
        owned.append(observation.changes)
        return write_lang(target, index, lang, commit)

    monkeypatch.setattr(processing.mkvtag, "write_lang", watched)
    with sweep_cache.observing(path) as observation:
        result = process(Job(path), dry_run=False, observation=observation)
        assert observation.wrote

    assert result.status is Status.MODIFIED
    assert owned == [(path, path)]


def test_a_refused_tag_gives_the_file_back_before_the_rewrite_waits(
    monkeypatch, stub_tag, tmp_path
):
    """The rewrite it falls through to can wait hours for a slot. Holding the
    file all that time would stall every read of it behind an edit that never
    happened."""
    path = str(tmp_path / "x.mkv")
    Path(path).write_bytes(b"file")
    stub_tag(mkvtag.Outcome.REFUSED, "an mp4 has no header to edit", plan=tag_plan(path))
    workers = ThreadPoolExecutor()

    def read_it() -> bool:
        with sweep_cache.observing(path) as reader:
            return reader.accepts({path})

    def rewriting(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        # Where the wait for a slot happens; a read of the file must not queue
        # behind it.
        assert workers.submit(read_it).result(timeout=3)
        assert claim()
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "apply_plan", rewriting)
    try:
        with sweep_cache.observing(path) as observation:
            result = process(Job(path), dry_run=False, observation=observation)
    finally:
        workers.shutdown(wait=False)
    assert result.status is Status.MODIFIED


def test_a_rewrite_whose_verdict_never_landed_drops_the_stored_one(
    monkeypatch, stub_rewrite, tmp_path
):
    """The file was published and nothing booked what it became, so the entry
    describing what it was before goes with it."""
    path = str(tmp_path / "x.mkv")
    Path(path).write_bytes(b"file")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = sweep_cache.SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache_verdict(
        cache, path, sweep_cache.cache_key(path, None), sweep_cache.Verdict(Status.PENDING)
    )
    cache.save()
    stub_rewrite(needed_plan(path))

    def lost(job, plan, key):
        raise ProbeError("the probe went away with the verdict")

    monkeypatch.setattr(processing, "_rejudged", lost)
    with pytest.raises(ProbeError), sweep_cache.observing(path) as observation:
        process(Job(path), dry_run=False, observation=observation)

    stored = sweep_cache.read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    assert stored.files == {}


def test_a_skip_landing_while_the_file_is_somebody_elses_writes_nothing(tmp_path):
    """Both mutation entries wait for a file another edit holds, and a skip
    arriving there is still in time: the tag defers and the rewrite never
    reaches its rename."""
    path = str(tmp_path / "x.mkv")
    Path(path).write_bytes(b"file")
    cancel = Cancel(path)
    cancel.ask()
    with sweep_cache.observing(path) as waiting, sweep_cache.observing(path) as holder:
        assert holder.changing(path)
        tagged = processing._tag_in_place(
            Job(path), tag_plan(path), (1, "jpn"), "sweep", cancel, waiting
        )
        assert processing._claim(waiting, needed_plan(path), cancel)() is False

    assert tagged.status is Status.DEFERRED
    assert tagged.detail == processing.STOPPED_BEFORE_START
    assert not waiting.wrote


def test_skip_while_waiting_for_a_remux_destination_never_starts_a_tool(monkeypatch, tmp_path):
    path = str(tmp_path / "file.mp4")
    output = str(tmp_path / "file.mkv")
    Path(path).write_text("source")
    Path(output).write_text("destination")
    plan = needed_plan(path, remuxing=True)
    cancel = Cancel(path)
    monkeypatch.setattr(processing, "build_plan", lambda *args: plan)
    monkeypatch.setattr(processing, "apply_plan", lambda *a, **kw: pytest.fail("started tool"))
    with sweep_cache.observing(output) as owner, sweep_cache.observing(path) as observation:
        assert owner.changing(output)
        stopped = threading.Event()
        original = observation._settle

        def waiting(paths, asked=None):
            stopped.set()
            return original(paths, asked)

        monkeypatch.setattr(observation, "_settle", waiting)
        with ThreadPoolExecutor() as pool:
            result = pool.submit(
                process, Job(path), False, cancel=cancel, observation=observation
            )
            assert stopped.wait(3)
            cancel.ask()
            assert result.result(timeout=3).status is Status.DEFERRED
