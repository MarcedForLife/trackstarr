"""The change broadcaster behind /api/stream, and the file watcher that
feeds it for anything a second process writes."""

import os

import pytest

from trackstarr import config, events, notify, runs, sweep_cache


@pytest.fixture(autouse=True)
def _no_leftover_subscribers():
    """Subscriptions are module state; none may outlive its test."""
    yield
    with notify._news:
        notify._subscribers.clear()


@pytest.fixture
def subscription():
    taken = notify.subscribe()
    assert taken is not None
    return taken


def test_a_publish_reaches_every_subscriber(subscription):
    other = notify.subscribe()
    notify.publish(notify.LIBRARY)
    assert subscription.take(0) == {notify.LIBRARY}
    assert other.take(0) == {notify.LIBRARY}


def test_a_burst_says_each_kind_once(subscription):
    notify.publish(notify.EVENTS)
    notify.publish(notify.EVENTS)
    notify.publish(notify.RUNS)
    assert subscription.take(0) == {notify.EVENTS, notify.RUNS}
    # Taken, so the next take is the timeout passing quietly.
    assert subscription.take(0) == set()


def test_an_unsubscribed_stream_hears_nothing(subscription):
    notify.unsubscribe(subscription)
    notify.publish(notify.LIBRARY)
    assert subscription.take(0) == set()


def test_the_cap_refuses_the_stream_after_the_last_place(monkeypatch, subscription):
    monkeypatch.setattr(notify, "MAX_STREAMS", 1)
    assert notify.subscribe() is None
    # A tab closing gives its place back.
    notify.unsubscribe(subscription)
    assert notify.subscribe() is not None


def _touch(path: str, line: str) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(path, "a") as watched:
        watched.write(line)


def test_the_watcher_says_which_file_moved(subscription):
    marks: dict = {}
    # The first pass is baseline: a history that merely exists is not news.
    _touch(events.path(), "{}\n")
    notify.watch_once(marks)
    assert subscription.take(0) == set()

    _touch(events.path(), "{}\n")
    notify.watch_once(marks)
    assert subscription.take(0) == {notify.EVENTS}

    _touch(sweep_cache.cache_path(), "{}")
    notify.watch_once(marks)
    assert subscription.take(0) == {notify.LIBRARY}


def test_an_unchanged_file_is_not_news(subscription):
    marks: dict = {}
    _touch(events.path(), "{}\n")
    notify.watch_once(marks)
    notify.watch_once(marks)
    assert subscription.take(0) == set()


def test_a_file_appearing_is_news(subscription):
    marks: dict = {}
    notify.watch_once(marks)
    _touch(events.path(), "{}\n")
    notify.watch_once(marks)
    assert subscription.take(0) == {notify.EVENTS}


@pytest.fixture
def clean_registry():
    """The activity registry is module state shared with other suites."""
    runs._runs.clear()
    yield
    runs._runs.clear()


def test_a_run_appearing_and_leaving_are_both_published(subscription, clean_registry):
    runs.open_run("r#1", runs.IMPORT, filling=True)
    assert subscription.take(0) == {notify.RUNS}

    # Reopening the same run is not a second appearance.
    runs.open_run("r#1", runs.IMPORT)
    assert subscription.take(0) == set()

    # The delivery finishing closes it, which is the other moment an idle
    # page is waiting to hear about.
    runs.add_file("r#1")
    runs.tally("r#1", "conform", path="/a.mkv")
    # Movement within a run, which is a different kind of news.
    assert subscription.take(0) == {notify.PROGRESS}
    runs.seal("r#1")
    assert subscription.take(0) == {notify.RUNS}


def test_a_closed_walk_is_published_once(subscription, clean_registry):
    runs.open_run("s#1", runs.SWEEP)
    subscription.take(0)
    runs.close_run("s#1")
    assert subscription.take(0) == {notify.RUNS}
    # Closing what is already gone says nothing.
    runs.close_run("s#1")
    assert subscription.take(0) == set()


def test_a_walk_says_it_moved_at_most_so_often(subscription, clean_registry):
    runs.open_run("s#1", runs.SWEEP)
    subscription.take(0)
    runs.tally("s#1", "conform", path="/a.mkv")
    assert subscription.take(0) == {notify.PROGRESS}

    # The next file, booked immediately after: a warm library answers from the
    # cache as fast as it can stat, and no page reads at that rate.
    runs.tally("s#1", "conform", path="/b.mkv", cached=True)
    assert subscription.take(0) == set()

    runs._runs["s#1"].told -= runs._MOVED_SECONDS
    runs.tally("s#1", "conform", path="/c.mkv", cached=True)
    assert subscription.take(0) == {notify.PROGRESS}


def test_a_rewrite_readout_says_it_less_often_still(subscription, clean_registry):
    runs.open_run("s#1", runs.SWEEP)
    runs.begin("s#1", "/a.mkv")
    subscription.take(0)

    # ffmpeg's first reading, which is the one there was nothing to draw a
    # moving bar from before.
    runs._runs["s#1"].told -= runs._MOVED_SECONDS
    runs.progress("s#1", "/a.mkv", done=8.0, speed=8.0)
    assert subscription.take(0) == {notify.PROGRESS}

    # A second later. The page has that reading and knows how old it is, so it
    # draws the bar between messages itself.
    runs._runs["s#1"].told -= runs._MOVED_SECONDS
    runs.progress("s#1", "/a.mkv", done=16.0, speed=8.0)
    assert subscription.take(0) == set()

    # Far enough in to be worth correcting: the machine has other work now and
    # the bar the page is drawing is running ahead of the encode.
    runs._runs["s#1"].told -= runs._DRIFT_SECONDS
    runs.progress("s#1", "/a.mkv", done=40.0, speed=2.0)
    assert subscription.take(0) == {notify.PROGRESS}


def test_a_stop_is_published_at_once(subscription, clean_registry):
    runs.open_run("s#1", runs.SWEEP)
    subscription.take(0)
    assert runs.stop("s#1")
    # Not held to the pace progress is: every other tab is showing a Stop
    # button that is no longer worth pressing.
    assert subscription.take(0) == {notify.RUNS}


def test_a_pause_and_a_resume_are_published(subscription, clean_registry):
    try:
        assert runs.pause("marc")
        assert subscription.take(0) == {notify.RUNS}
        assert runs.resume("marc")
        assert subscription.take(0) == {notify.RUNS}
    finally:
        runs._running.set()
