"""The rows a read walks, and what keeps that walk short."""

from trackstarr import queue_view


def row(rank: int, path: str = "/data/file.mkv", expected: float = 0.0) -> queue_view.Entry:
    return queue_view.Entry(
        queue_view.Row((False, rank, ("sweep", path)), "sweep", path, expected)
    )


def test_a_file_returning_for_its_rewrite_is_read_back_in_its_old_place():
    """It keeps the rank it was found under, so it arrives before the tail."""
    queue = queue_view.RunQueue()
    for rank in range(5):
        queue.add(row(rank, f"/data/{rank}.mkv"))
    returning = row(-1, "/data/probed.mkv")
    queue.add(returning)

    assert queue.loose == [returning], "out of order, so not on the end of the block"
    assert [entry.path for entry in queue.capture().shown()] == [
        "/data/probed.mkv",
        *(f"/data/{rank}.mkv" for rank in range(5)),
    ]
    assert queue.waiting == 6


def test_rows_arriving_out_of_order_are_gathered_before_the_block_is_rebuilt():
    """A rebuild per arrival is the traversal this is here to avoid."""
    queue = queue_view.RunQueue()
    for rank in range(200):
        queue.add(row(rank * 2, f"/data/{rank:03}.mkv"))
    block = queue.block
    for rank in range(queue_view._LOOSE):
        queue.add(row(rank * 2 + 1, f"/data/between {rank:03}.mkv"))
    assert queue.block is block, "the block a reader is walking is still whole"

    queue.add(row(1, "/data/late.mkv"))
    assert queue.block is not block and not queue.loose
    assert len(queue.block) == 265
    assert [entry.path for entry in queue.capture().shown()][:3] == [
        "/data/000.mkv",
        "/data/between 000.mkv",
        "/data/late.mkv",
    ]


def test_a_block_of_claimed_rows_is_rebuilt_around_what_is_left():
    queue = queue_view.RunQueue()
    rows = [row(rank, f"/data/{rank:03}.mkv") for rank in range(100)]
    for entry in rows:
        queue.add(entry)
    for entry in rows[:82]:
        queue.drop(entry)
    assert len(queue.block) == 100, "a claim leaves its row where the others are"

    queue.drop(rows[82])
    assert len(queue.block) == 17
    assert next(entry.path for entry in queue.capture().shown()) == "/data/083.mkv"


def test_a_read_steps_past_claimed_and_skipped_rows_once():
    queue = queue_view.RunQueue()
    rows = [row(rank, f"/data/{rank}.mkv") for rank in range(6)]
    for entry in rows:
        queue.add(entry)
    queue.drop(rows[0])
    queue.hide(rows[1])

    capture = queue.capture()
    assert (queue.queued_from, queue.shown_from) == (1, 2)
    # A file taken off its run still has a queue row until a worker reaches it.
    assert next(entry.path for entry in capture.queued()) == "/data/1.mkv"
    assert next(entry.path for entry in capture.shown()) == "/data/2.mkv"
    assert (queue.waiting, queue.hidden) == (5, 1)

    queue.drop(rows[1])
    assert (queue.waiting, queue.hidden) == (4, 0)


def test_the_estimate_goes_back_to_nothing_once_the_last_file_leaves():
    """Summed and unsummed floats leave a remainder, and a remainder reads as
    a run with rewriting still in it."""
    queue = queue_view.RunQueue()
    rows = [row(rank, f"/data/{rank}.mkv", expected=0.1) for rank in range(10)]
    for entry in rows:
        queue.add(entry)
    assert queue.expected > 0
    for entry in rows:
        queue.drop(entry)

    assert (queue.expected, queue.waiting) == (0.0, 0)


def test_a_capture_outlives_the_block_being_rebuilt_under_it():
    """A read walks its rows after letting the condition go, so a rebuild
    replaces the block rather than editing the one being walked."""
    queue = queue_view.RunQueue()
    rows = [row(rank, f"/data/{rank:03}.mkv") for rank in range(100)]
    for entry in rows:
        queue.add(entry)
    capture = queue.capture()
    for entry in rows[:83]:
        queue.drop(entry)

    assert queue.block is not capture.block
    assert [entry.path for entry in capture.queued()] == [
        f"/data/{rank:03}.mkv" for rank in range(100)
    ]


def test_nothing_queued_anywhere_reads_as_an_empty_queue():
    assert list(queue_view.stream([])) == []


def test_a_capture_keeps_rows_and_skip_flags_after_claim_skip_and_append():
    queue = queue_view.RunQueue()
    first, second = row(0, "/first"), row(1, "/second")
    queue.add(first)
    queue.add(second)
    capture = queue.capture()
    before = list(capture.queued())
    queue.drop(first)
    queue.hide(second)
    queue.add(row(2, "/third"))
    assert list(capture.queued()) == before
    assert [entry.path for entry in capture.shown()] == ["/first", "/second"]
    assert [(entry.path, entry.skipped) for entry in queue.capture().queued()] == [
        ("/second", True),
        ("/third", False),
    ]


def test_skipped_head_and_returning_rank_keep_each_captures_cursor():
    queue = queue_view.RunQueue()
    entries = [row(rank, f"/{rank}") for rank in range(4)]
    for entry in entries:
        queue.add(entry)
    original = queue.capture()
    queue.hide(entries[0])
    queue.drop(entries[1])
    advanced = queue.capture()
    queue.add(row(-1, "/returning"))
    assert [entry.path for entry in original.shown()] == ["/0", "/1", "/2", "/3"]
    assert [entry.path for entry in advanced.shown()] == ["/2", "/3"]
    assert [entry.path for entry in queue.capture().shown()] == ["/returning", "/2", "/3"]


def test_out_of_order_compaction_keeps_copied_loose_rows():
    queue = queue_view.RunQueue()
    queue.add(row(1000, "/tail"))
    queue.add(row(0, "/loose"))
    captured = queue.capture()
    for rank in range(1, 66):
        queue.add(row(rank, f"/{rank}"))
    assert queue.block is not captured.block
    assert [entry.path for entry in captured.shown()] == ["/loose", "/tail"]


def test_visibility_stamps_exclude_entries_added_after_the_generation():
    entry = row(0)
    queue = queue_view.RunQueue()
    queue.add(entry)
    assert not entry.queued(0)
