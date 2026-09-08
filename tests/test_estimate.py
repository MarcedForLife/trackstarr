"""How long the rewrites a sweep has found will take, and where the number
that says so comes from."""

import pytest

from trackstarr import config, estimate, events, runs
from trackstarr.estimate import Speeds, measured, shape


def _generated(*layouts: str) -> list[dict]:
    """A planned track list making a downmix for each layout named."""
    return [{"codec": "h264", "kind": "video"}] + [
        {"codec": "aac", "title": layout, "flags": ["generated"]} for layout in layouts
    ]


def _fixed(duration: float, seconds: float, *layouts: str, waited: float = 0.0) -> None:
    events.record(
        "fixed",
        path="/library/film.mkv",
        duration=duration,
        seconds=seconds,
        waited=waited or None,
        downmixed=list(layouts) or None,
    )


def test_a_plans_shape_is_the_downmixes_it_makes():
    assert shape(_generated("2.0", "5.1")) == ("2.0", "5.1")
    assert shape(_generated()) == ()


def test_a_shape_is_what_the_file_needs_rather_than_what_the_rules_ask_for():
    """A file that already has its 5.1 and only wants a stereo track is a
    one-encode rewrite, whatever AUDIO_LAYOUTS names."""
    assert shape(_generated("2.0")) == ("2.0",)


def test_a_files_length_and_its_shape_are_what_it_costs():
    speeds = Speeds({("2.0",): 100.0})
    assert speeds.seconds(6000, _generated("2.0")) == 60.0


def test_a_file_nothing_could_time_costs_nothing():
    """A length nobody knows must not be guessed at. One file missing from a
    backlog of hundreds costs the total very little; one invented does not."""
    assert Speeds().seconds(0, _generated("2.0")) == 0.0


def test_an_install_with_no_history_estimates_from_the_guesses():
    speeds = measured()

    assert speeds.of(()) == estimate.BOOTSTRAP[0]
    assert speeds.of(("2.0",)) == estimate.BOOTSTRAP[1]
    assert speeds.of(("2.0", "5.1", "7.1")) == estimate.SLOWEST


def test_what_this_machine_has_actually_run_at_beats_the_guess():
    """The whole point of reading the history back: a box that encodes at 20x
    must not be estimated at 120x, however reasonable that was to start with."""
    for _ in range(estimate.ENOUGH):
        _fixed(3600, 180, "2.0")

    assert measured().of(("2.0",)) == 20.0


def test_too_few_rewrites_to_mean_anything_leave_the_guess_alone():
    """Two rewrites can both have been the same unusual file."""
    for _ in range(estimate.ENOUGH - 1):
        _fixed(3600, 180, "2.0")

    assert measured().of(("2.0",)) == estimate.BOOTSTRAP[1]


def test_each_shape_is_measured_on_its_own():
    """Two downmixes cost more than one, and a remux with neither costs least,
    so one number across all of them would be wrong for every file."""
    for _ in range(estimate.ENOUGH):
        _fixed(3600, 12, waited=0)
        _fixed(3600, 180, "2.0")
        _fixed(3600, 360, "2.0", "5.1")

    speeds = measured()
    assert (speeds.of(()), speeds.of(("2.0",)), speeds.of(("2.0", "5.1"))) == (
        300.0,
        20.0,
        10.0,
    )


def test_the_queue_for_a_slot_is_not_the_work():
    """A rewrite that waited an hour for its turn is not a slow rewrite, and
    taking it for one would put every file behind it an hour out."""
    for _ in range(estimate.ENOUGH):
        _fixed(3600, 3780, "2.0", waited=3600)

    assert measured().of(("2.0",)) == 20.0


def test_the_middle_rewrite_is_the_one_believed():
    """One file that hit a full disk and took an hour must not drag the
    estimate for the hundred behind it."""
    for seconds in (180, 180, 180, 180, 3600):
        _fixed(3600, seconds, "2.0")

    assert measured().of(("2.0",)) == 20.0


def test_a_history_of_everything_but_rewrites_says_nothing_about_them():
    for _ in range(20):
        events.record("skipped", path="/library/film.mkv")

    assert measured().observed == {}


def test_a_rewrite_the_history_could_not_time_is_left_out():
    """A line from a file nothing could measure the length of, and one whose
    whole cost was the wait for a slot. Neither says anything about how fast
    this machine encodes, and a zero would say it is infinitely fast."""
    for _ in range(estimate.ENOUGH):
        _fixed(3600, 180, "2.0")
        events.record("fixed", path="/library/film.mkv", seconds=180, downmixed=["2.0"])
        _fixed(3600, 3600, "2.0", waited=3600)

    assert measured().of(("2.0",)) == 20.0


def test_the_history_is_read_a_page_at_a_time_and_only_so_far_back():
    """Rewrites can be a page or more back behind deliveries. Reading still
    stops after so many pages; the guess suits a machine that has not
    rewritten lately."""
    for _ in range(estimate.ENOUGH):
        _fixed(3600, 180, "2.0")
    for _ in range(estimate._PAGE + 1):
        events.record("skipped", path="/library/film.mkv")

    assert measured(pages=1).observed == {}
    assert measured(pages=2).of(("2.0",)) == 20.0


@pytest.mark.parametrize("budget", [1, 3])
def test_a_backlog_is_shared_out_across_the_rewrite_budget(monkeypatch, budget):
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)

    assert estimate.backlog(600) == 600 / budget


# What the run says about it


@pytest.fixture
def registry():
    runs._runs.clear()
    yield runs
    runs._runs.clear()


def _sweep_run(registry, budget: int, monkeypatch) -> dict:
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)
    registry.open_run("r#1", runs.SWEEP)
    (snapshot,) = registry.snapshot()["runs"]
    return snapshot


def test_a_run_with_no_rewriting_in_it_has_nothing_to_say(registry, monkeypatch):
    """A reporting sweep is probes the whole way down. How long those have
    left is the walk's own rate to answer, not this."""
    registry.begin("r#1", "/library/film.mkv")

    assert _sweep_run(registry, 1, monkeypatch)["rewrite_seconds"] is None


def test_a_queued_backlog_is_what_is_left(registry, monkeypatch):
    registry.open_run("r#1", runs.SWEEP)
    for i in range(4):
        registry.queue("r#1", f"/library/{i}.mkv", 300.0)

    assert _sweep_run(registry, 2, monkeypatch)["rewrite_seconds"] == 600.0


def test_a_file_in_hand_counts_for_what_it_has_left(registry, monkeypatch):
    """It came off the queue when a worker picked it up, and a file being
    rewritten is not a file with no work left in it."""
    registry.open_run("r#1", runs.SWEEP)
    registry.queue("r#1", "/library/film.mkv", 300.0)
    registry.begin("r#1", "/library/film.mkv")

    left = _sweep_run(registry, 1, monkeypatch)["rewrite_seconds"]
    assert 290.0 < left <= 300.0


def test_ffmpegs_own_readout_beats_the_estimate_that_queued_the_file(registry, monkeypatch):
    """The estimate is a median of other files. Once this one is running, it
    is reporting on itself."""
    registry.open_run("r#1", runs.SWEEP)
    registry.queue("r#1", "/library/film.mkv", 3000.0)
    registry.begin("r#1", "/library/film.mkv")
    registry.stage("r#1", "/library/film.mkv", runs.ENCODING, 7200.0)
    # Half written, at twenty times realtime: three minutes left, not fifty.
    registry.progress("r#1", "/library/film.mkv", 3600.0, 20.0)

    assert _sweep_run(registry, 1, monkeypatch)["rewrite_seconds"] == 180.0


def test_one_long_file_is_not_shared_out_across_the_budget(registry, monkeypatch):
    """A backlog divides between the slots. One file does not: three spare
    workers do not make a two-hour encode take forty minutes."""
    registry.open_run("r#1", runs.SWEEP)
    registry.queue("r#1", "/library/film.mkv", 7200.0)
    registry.begin("r#1", "/library/film.mkv")
    registry.stage("r#1", "/library/film.mkv", runs.ENCODING, 7200.0)
    registry.progress("r#1", "/library/film.mkv", 0.0, 1.0)

    assert _sweep_run(registry, 3, monkeypatch)["rewrite_seconds"] == 7200.0
