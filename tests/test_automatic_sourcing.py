"""The source_audio rule imports missing audio from another variant on its own."""

import subprocess
from dataclasses import replace

import pytest

from conftest import configured_arr, set_config, set_langs, set_layouts, set_rules
from trackstarr import (
    alignment,
    audio_sources,
    executor,
    jobs,
    library,
    lifecycle,
    processing,
    runs,
    sweep,
    sweep_cache,
)
from trackstarr.alignment import AlignmentResult
from trackstarr.executor import Cancel
from trackstarr.inventory import Variant
from trackstarr.library import Source, Title
from trackstarr.media import IMPORTED_TAG, probe, stream_lang, tag_value
from trackstarr.planner import FileRevision, TimingMapping
from trackstarr.policy import Policy
from trackstarr.processing import Job, process
from trackstarr.status import Status
from trackstarr.sweep_cache import Verdict


def connection(instance_id: str, path, file_id: int):
    """A connection listing one file of the film."""
    held = replace(configured_arr("radarr"), instance_id=instance_id)
    held.files = lambda item_id: [{"id": file_id, "path": str(path)}]
    return held


@pytest.fixture
def variants(tmp_path, monkeypatch):
    """A film in two connections, the target lacking the French the other holds."""
    set_config(
        WORK_DIR=str(tmp_path / "work"),
        REWRITE_MODE="imports",
        ALLOWED_EXTS=frozenset({".mkv"}),
    )
    set_rules(**dict.fromkeys(dict(Policy.from_config().rule_modes), "never"))
    set_rules(source_audio="always")
    set_layouts()
    set_langs("eng", "fre")
    target = tmp_path / "movies" / "Film" / "Film.mkv"
    source = tmp_path / "movies-4k" / "Film" / "Film.mkv"
    for path in (target, source):
        path.parent.mkdir(parents=True)
    title = Title(
        "arr:radarr:1",
        "Film",
        (
            Source(str(target.parent), connection("radarr", target, 5), 1),
            Source(str(source.parent), connection("radarr-4k", source, 9), 3),
        ),
        "movie",
        provider_id="603",
    )
    monkeypatch.setattr(library, "title_for", lambda path: title)
    monkeypatch.setattr(library, "stored_entries", dict)
    audio_sources.reset()
    yield target, source
    audio_sources.reset()
    assert not executor._source_reads
    assert not executor._preparing
    assert not list((tmp_path / "work").glob("*"))


def audio_langs(path) -> list[str | None]:
    return [
        stream_lang(stream)
        for stream in probe(str(path))["streams"]
        if stream["codec_type"] == "audio"
    ]


def make_media(path, lang: str, seconds: int, tone: int) -> None:
    """A test pattern with one main audio track in ``lang``."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=32x32:rate=10:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration={seconds}",
            "-c:v",
            "ffv1",
            "-c:a",
            "flac",
            "-metadata:s:a:0",
            f"language={lang}",
            str(path),
        ],
        check=True,
    )


def life(path, seconds: int, *, trim: float = 0.0, lang: str = "eng", tone: int = 440):
    """Non-repeating pictures, so timing can be measured, with one tagged tone."""
    # Seeded, so the target and its shifted variant share every frame.
    picture = (
        "life=s=160x90:rate=24:ratio=0.3:mold=5:seed=7:death_color=#203040:life_color=#f0c040"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"{picture},format=yuv420p,trim=duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate=48000:duration={seconds}",
            "-filter_complex",
            (
                f"[0:v]trim=start={trim},setpts=PTS-STARTPTS[v];"
                f"[1:a]atrim=start={trim},asetpts=PTS-STARTPTS[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "ffv1",
            "-c:a",
            "flac",
            "-metadata:s:a:0",
            f"language={lang}",
            str(path),
        ],
        check=True,
    )


def test_a_variant_on_the_same_timeline_supplies_its_audio_unmeasured(variants, monkeypatch):
    target, source = variants
    make_media(target, "eng", 6, 440)
    make_media(source, "fre", 6, 880)
    monkeypatch.setattr(alignment, "analyse", lambda *args: pytest.fail("measured"))
    found = process(Job(str(target), "eng"), dry_run=True)
    assert found.status is Status.PENDING
    assert found.plan is not None and found.plan.rules == {"source_audio"}
    (english, french) = found.plan.sourcing["requirements"]
    assert english == {"lang": "eng", "state": "satisfied"}
    assert french["state"] == "ready" and french["track"]["instance_id"] == "radarr-4k"
    assert french["mapping"] == {"scale": "1", "offset_us": 0} and not french["measured"]

    done = process(Job(str(target), "eng"), dry_run=False)
    assert done.status is Status.MODIFIED, done.detail
    assert audio_langs(target) == ["eng", "fre"]
    imported = probe(str(target))["streams"][2]
    assert tag_value(imported, IMPORTED_TAG) == "scale=1 offset_us=0"
    assert done.became is not None
    assert done.became.verdict.sourcing["completeness"] == "complete"
    assert "imported" in done.became.verdict.tracks[2]["flags"]


def test_alongside_imports_only_beside_another_rules_rewrite(variants):
    target, source = variants
    make_media(target, "eng", 6, 440)
    make_media(source, "fre", 6, 880)
    set_rules(source_audio="alongside")
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.CONFORM
    assert result.plan is not None
    assert result.plan.sourcing["requirements"][1]["state"] == "ready"
    assert audio_langs(target) == ["eng"]

    set_rules(order="always")
    reordered = target.with_name("Reordered.mkv")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(target),
            "-map",
            "0:a",
            "-map",
            "0:v",
            "-c",
            "copy",
            str(reordered),
        ],
        check=True,
    )
    reordered.replace(target)
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.MODIFIED, result.detail
    assert result.plan is not None and "source_audio" in result.plan.incidental_rules
    assert audio_langs(target) == ["eng", "fre"]


def test_a_shifted_variant_is_measured_by_picture_before_its_audio_moves(variants):
    target, source = variants
    life(target, 16)
    life(source, 16, trim=1.25, lang="fre", tone=880)
    found = process(Job(str(target), "eng"), dry_run=True)
    assert found.status is Status.PENDING
    assert found.plan is not None
    waiting = found.plan.sourcing["requirements"][1]
    assert waiting["state"] == "analysis_required"
    assert found.plan.reasons == [f"measure timing to import fre audio from {source}"]

    done = process(Job(str(target), "eng"), dry_run=False)
    assert done.status is Status.MODIFIED, done.detail
    assert done.plan is not None
    ready = done.plan.sourcing["requirements"][1]
    assert ready["state"] == "ready" and ready["measured"]
    assert ready["mapping"]["scale"] == "1"
    assert abs(ready["mapping"]["offset_us"] - 1_250_000) < 2_000
    (record,) = done.plan.sourcing["alignments"]
    assert record["status"] == "accepted" and record["source"]["path"] == str(source)
    imported = probe(str(target))["streams"][2]
    assert stream_lang(imported) == "fre"
    assert abs(float(imported["start_time"]) - 1.25) < 0.05


def test_a_stored_measurement_is_reused_rather_than_measured_again(variants, monkeypatch):
    target, source = variants
    life(target, 16)
    life(source, 16, trim=1.25, lang="fre", tone=880)
    measured = AlignmentResult(
        FileRevision.of(str(target)),
        FileRevision.of(str(source)),
        alignment.ACCEPTED,
        "mapping_confirmed",
        TimingMapping(offset_us=1_250_000),
    )
    stored = {str(target): {"sourcing": {"alignments": [alignment.record(measured)]}}}
    monkeypatch.setattr(library, "stored_entries", lambda: stored)
    monkeypatch.setattr(alignment, "analyse", lambda *args: pytest.fail("measured again"))
    found = process(Job(str(target), "eng"), dry_run=True)
    assert found.plan is not None
    assert found.plan.sourcing["requirements"][1]["state"] == "ready"
    assert found.plan.rules == {"source_audio"}


def test_a_refused_measurement_leaves_the_file_for_review(variants, monkeypatch):
    target, source = variants
    life(target, 16)
    life(source, 16, trim=1.25, lang="fre", tone=880)

    def refused(target_revision, source_revision, cancel):
        return AlignmentResult(
            target_revision, source_revision, alignment.NEEDS_REVIEW, "contradicting_region"
        )

    monkeypatch.setattr(alignment, "analyse", refused)
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.CONFORM
    assert result.plan is not None
    review = result.plan.sourcing["requirements"][1]
    assert review == {
        "lang": "fre",
        "state": "needs_review",
        "reason": "part of the source contradicts the timing",
    }
    assert result.plan.sourcing["completeness"] == "incomplete"
    assert audio_langs(target) == ["eng"]


def test_a_video_only_target_takes_its_only_audio_from_a_variant(variants):
    target, source = variants
    make_media(source, "eng", 6, 880)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:v", "-c", "copy", str(target)],
        check=True,
    )
    set_langs("eng")
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.MODIFIED, result.detail
    assert audio_langs(target) == ["eng"]


def test_a_video_only_target_waiting_on_a_measurement_is_pending_not_skipped(variants):
    target, source = variants
    life(source, 16, trim=1.25, tone=880)
    life(target, 16)
    silent = target.with_name("Silent.mkv")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(target), "-map", "0:v", "-c", "copy", str(silent)],
        check=True,
    )
    silent.replace(target)
    set_langs("eng")
    result = process(Job(str(target), "eng"), dry_run=True)
    assert result.status is Status.PENDING
    assert result.plan is not None and result.plan.skip is None


def test_an_import_the_rules_refuse_becomes_a_review(variants, monkeypatch):
    target, source = variants
    make_media(target, "eng", 6, 440)
    make_media(source, "fre", 6, 880)
    set_langs("eng", "fre", "ger")

    def refusing(job, policy, inputs, *, explicit):
        raise ValueError("source stream 1 would be removed or replaced by the current rules")

    monkeypatch.setattr(processing, "_plan_imports", refusing)
    result = process(Job(str(target), "eng"), dry_run=True)
    assert result.status is Status.CONFORM
    assert result.plan is not None
    refused = result.plan.sourcing["requirements"][1]
    assert refused["state"] == "needs_review"
    assert (
        refused["reason"] == "source stream 1 would be removed or replaced by the current rules"
    )
    # The refused source stays named, so a review knows what was tried.
    assert refused["track"]["path"] == str(source)


def two_languages(path) -> None:
    """A variant holding French at stream 1 and German at stream 2."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=10:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=6",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "1:a",
            "-c:v",
            "ffv1",
            "-c:a",
            "flac",
            "-metadata:s:a:0",
            "language=fre",
            "-metadata:s:a:1",
            "language=ger",
            str(path),
        ],
        check=True,
    )


def test_imports_the_rules_refuse_are_dropped_one_by_one(variants, monkeypatch):
    target, source = variants
    make_media(target, "eng", 6, 440)
    two_languages(source)
    set_langs("eng", "fre", "ger")
    real = processing._plan_imports

    def refusing_german(job, policy, inputs, *, explicit):
        if any(item.stream_index == 2 for item in inputs):
            raise ValueError("the German track is refused")
        return real(job, policy, inputs, explicit=explicit)

    monkeypatch.setattr(processing, "_plan_imports", refusing_german)
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.MODIFIED, result.detail
    assert audio_langs(target) == ["eng", "fre"]
    assert result.plan is not None
    states = [item["state"] for item in result.plan.sourcing["requirements"]]
    assert states == ["satisfied", "ready", "needs_review"]


def test_measuring_waits_for_a_rewrite_slot_and_says_so(variants, monkeypatch):
    target, _ = variants
    stages = []
    monkeypatch.setattr(runs, "stage", lambda run, path, name, total=0.0: stages.append(name))
    monkeypatch.setattr(runs, "settle", lambda run, path: stages.append("settled"))
    measured = AlignmentResult(
        FileRevision("/t", 1, 2, 3, 4, 5), FileRevision("/s", 1, 3, 3, 4, 5), "accepted", ""
    )
    monkeypatch.setattr(alignment, "analyse", lambda *args: measured)
    measure = processing._measurer(Job(str(target), run="run"), None)
    assert measure(measured.target, measured.source) is measured
    assert stages == [runs.WAITING, runs.ANALYSING, "settled"]


def test_a_skip_before_the_slot_stops_the_measurement(variants, monkeypatch):
    target, _ = variants
    cancel = Cancel(str(target))
    cancel.ask()
    monkeypatch.setattr(alignment, "analyse", lambda *args: pytest.fail("measured"))
    measure = processing._measurer(Job(str(target)), cancel)
    with pytest.raises(InterruptedError):
        measure(FileRevision("/t", 1, 2, 3, 4, 5), FileRevision("/s", 1, 3, 3, 4, 5))


def stored_requirements(path) -> list[dict]:
    entry = sweep_cache.read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    return entry[str(path)]["sourcing"]["requirements"]


def test_a_sweep_looks_again_once_a_variant_can_supply_the_audio(variants, monkeypatch):
    target, source = variants
    make_media(target, "eng", 6, 440)
    make_media(source, "fre", 6, 880)
    set_config(MEDIA_DIRS=[str(target.parent.parent)])
    title = library.title_for(str(target))
    assert title is not None
    listed: list[dict] = []
    monkeypatch.setattr(title.sources[1].arr, "files", lambda item_id: listed)
    sweep.sweep(dry_run=True)
    assert stored_requirements(target)[1]["state"] == "missing_source"

    # The target is unchanged on disk, so only the variants can say it is stale.
    listed.append({"id": 9, "path": str(source)})
    audio_sources.forget()
    sweep.sweep(dry_run=True)
    assert stored_requirements(target)[1]["state"] == "ready"

    monkeypatch.setattr(sweep, "process", lambda *args, **kwargs: pytest.fail("re-probed"))
    sweep.sweep(dry_run=True)


def waiting_variant(target) -> audio_sources.Affected:
    title = library.title_for(str(target))
    assert title is not None
    return audio_sources.Affected(Variant(str(target), "radarr", 1, 5, ()), title)


def test_an_import_queues_the_variants_waiting_on_it(variants, monkeypatch):
    target, source = variants
    monkeypatch.setattr(audio_sources, "affected", lambda path: [waiting_variant(target)])
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: queued.append(job) or True)
    lifecycle.open_run("delivery", runs.IMPORT)
    jobs._reassess_variants(Job(str(source), run="delivery"))
    (job,) = queued
    assert (job.path, job.instance_id, job.item_id, job.run) == (
        str(target),
        "radarr",
        1,
        "delivery",
    )
    assert job.lang == library.title_for(str(target)).lang


def test_an_import_with_nobody_waiting_queues_nothing(variants, monkeypatch):
    _, source = variants
    monkeypatch.setattr(audio_sources, "affected", lambda path: [])
    monkeypatch.setattr(jobs, "enqueue", lambda job: pytest.fail("queued"))
    jobs._reassess_variants(Job(str(source)))
    set_rules(source_audio="never")
    monkeypatch.setattr(audio_sources, "affected", lambda path: pytest.fail("looked"))
    jobs._reassess_variants(Job(str(source)))


def test_a_failed_look_for_waiting_variants_leaves_the_import_alone(
    variants, monkeypatch, caplog
):
    _, source = variants

    def broken(path):
        raise RuntimeError("listing exploded")

    monkeypatch.setattr(audio_sources, "affected", broken)
    jobs._reassess_variants(Job(str(source)))
    assert "could not look for variants waiting on" in caplog.text


def test_a_stopping_process_refuses_to_queue_a_waiting_variant(variants, monkeypatch, caplog):
    target, _ = variants

    def closed(*args, **kwargs):
        raise ValueError("scheduler is closed")

    monkeypatch.setattr(lifecycle, "open_run", closed)
    with caplog.at_level("INFO"):
        assert not jobs._requeue(Job(str(target), run="delivery"))
    assert "scheduler is closed" in caplog.text


def test_a_file_the_rules_skip_asks_nothing_of_its_variants(variants, monkeypatch):
    target, _ = variants
    monkeypatch.setattr(
        audio_sources, "assess", lambda *args, **kwargs: pytest.fail("assessed")
    )
    policy = Policy.from_config()
    skipped = processing.plan_job(Job(str(target.with_suffix(".avi"))), policy)
    assert skipped.skip and skipped.skip.startswith("container .avi")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-metadata:s:a:0",
            "language=eng",
            str(target),
        ],
        check=True,
    )
    assert processing.plan_job(Job(str(target)), policy).skip == "no video stream"


def test_a_rule_with_no_languages_to_ask_for_leaves_the_plan_local(variants):
    target, _ = variants
    make_media(target, "eng", 6, 440)
    set_langs()
    plan = processing.plan_job(Job(str(target), "eng"), Policy.from_config())
    assert plan.sourcing == {}


def test_a_waiting_variant_already_queued_is_not_announced_again(variants, monkeypatch):
    target, source = variants
    monkeypatch.setattr(audio_sources, "affected", lambda path: [waiting_variant(target)])
    monkeypatch.setattr(jobs, "enqueue", lambda job: False)
    jobs._reassess_variants(Job(str(source)))
    assert not jobs._requeue(Job(str(target)))


def test_a_failed_measurement_is_retried_by_a_later_pass_then_given_up(variants, monkeypatch):
    target, source = variants
    life(target, 16)
    life(source, 16, trim=1.25, lang="fre", tone=880)
    attempts = []

    def failing(target_revision, source_revision, cancel):
        attempts.append(1)
        return AlignmentResult(target_revision, source_revision, alignment.FAILED, "disk full")

    monkeypatch.setattr(alignment, "analyse", failing)
    stored: dict = {}
    monkeypatch.setattr(library, "stored_entries", lambda: stored)
    for attempt in range(1, audio_sources.MAX_ATTEMPTS + 1):
        result = process(Job(str(target), "eng"), dry_run=False)
        # Nothing to import, so nothing is rewritten for the measurement.
        assert result.status is Status.CONFORM
        assert result.plan is not None
        (record,) = result.plan.sourcing["alignments"]
        assert record.get("attempts", 1) == attempt
        stored[str(target)] = {"sourcing": result.plan.sourcing}
        audio_sources.reset()
    assert len(attempts) == audio_sources.MAX_ATTEMPTS
    assert result.plan.sourcing["requirements"][1] == {
        "lang": "fre",
        "state": "failed",
        "reason": "measuring timing failed: disk full",
    }
    process(Job(str(target), "eng"), dry_run=False)
    assert len(attempts) == audio_sources.MAX_ATTEMPTS


def test_a_settled_verdict_owing_a_measurement_is_judged_again(monkeypatch):
    owed = {"requirements": [{"lang": "fre", "state": "analysis_required"}]}
    monkeypatch.setattr(audio_sources, "changed", lambda path, projection: False)
    set_rules(source_audio="always")
    assert sweep._sourcing_stale("/f.mkv", Verdict(Status.CONFORM, sourcing=owed))
    # Pending already queues the measurement, so only the variants can stale it.
    assert not sweep._sourcing_stale("/f.mkv", Verdict(Status.PENDING, sourcing=owed))
    # Alongside never measures on its own, so judging again would change nothing.
    set_rules(source_audio="alongside")
    assert not sweep._sourcing_stale("/f.mkv", Verdict(Status.CONFORM, sourcing=owed))
    settled = {"requirements": [{"lang": "fre", "state": "satisfied"}]}
    monkeypatch.setattr(audio_sources, "changed", lambda path, projection: pytest.fail("asked"))
    assert not sweep._sourcing_stale("/f.mkv", Verdict(Status.CONFORM, sourcing=settled))


def test_alongside_never_measures_for_a_file_nothing_else_rewrites(variants, monkeypatch):
    target, source = variants
    life(target, 16)
    life(source, 16, trim=1.25, lang="fre", tone=880)
    set_rules(source_audio="alongside")
    monkeypatch.setattr(alignment, "analyse", lambda *args: pytest.fail("measured"))
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.CONFORM
    assert result.plan is not None
    assert result.plan.sourcing["requirements"][1]["state"] == "analysis_required"


def test_imports_refused_only_together_keep_the_first_and_review_the_rest(
    variants, monkeypatch
):
    target, source = variants
    make_media(target, "eng", 6, 440)
    two_languages(source)
    set_langs("eng", "fre", "ger")
    real = processing._plan_imports

    def refusing_company(job, policy, inputs, *, explicit):
        if len(inputs) > 1:
            raise ValueError("these imports cannot share the file")
        return real(job, policy, inputs, explicit=explicit)

    monkeypatch.setattr(processing, "_plan_imports", refusing_company)
    result = process(Job(str(target), "eng"), dry_run=False)
    assert result.status is Status.MODIFIED, result.detail
    assert audio_langs(target) == ["eng", "fre"]
    assert result.plan is not None
    german = result.plan.sourcing["requirements"][2]
    assert (german["state"], german["reason"]) == (
        "needs_review",
        "these imports cannot share the file",
    )


def test_a_listing_asked_for_before_a_delivery_is_not_kept_after_it(variants, monkeypatch):
    target, _ = variants
    title = library.title_for(str(target))
    assert title is not None
    real = audio_sources.inventory.take

    def delivered_meanwhile(asked):
        taken = real(asked)
        audio_sources.forget()
        return taken

    monkeypatch.setattr(audio_sources.inventory, "take", delivered_meanwhile)
    audio_sources._inventory(title)
    assert not audio_sources._listings
    monkeypatch.setattr(audio_sources.inventory, "take", real)
    audio_sources._inventory(title)
    assert list(audio_sources._listings) == [title.id]
