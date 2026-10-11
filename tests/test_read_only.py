"""Read-only connections: their files are probed and planned, never changed."""

import json
import urllib.error

import pytest

from conftest import configured_arr, needed_plan, read_events, set_config
from trackstarr import catalogue, config, jobs, library, processing, retag, settings
from trackstarr.arr import client_for
from trackstarr.executor import Outcome
from trackstarr.processing import Job, ProcessResult, process
from trackstarr.status import Status
from trackstarr.sweep import sweep

FOUR_K = "/data/media/movies-4k"
DUNE = f"{FOUR_K}/Dune (2021)/Dune (2021).mkv"
ARRIVAL = "/data/media/movies/Arrival (2016)/Arrival (2016).mkv"


class Listing:
    """One *arr's title list, counting the asks."""

    def __init__(self, *folders: str, down: bool = False) -> None:
        self.folders = list(folders)
        self.down = down
        self.asked = 0

    def all_items(self) -> list[dict]:
        self.asked += 1
        if self.down:
            raise urllib.error.URLError("connection refused")
        return [{"id": number, "path": folder} for number, folder in enumerate(self.folders)]


@pytest.fixture
def four_k(monkeypatch):
    """A read-only Radarr 4K listing Dune's folder."""
    listing = Listing(f"{FOUR_K}/Dune (2021)")
    set_config(RADARR_4K_URL="http://4k", RADARR_4K_API_KEY="key", RADARR_4K_READ_ONLY=True)
    monkeypatch.setattr(catalogue, "client_for", lambda instance: listing)
    return listing


@pytest.fixture
def clock(monkeypatch):
    """The catalogue's monotonic clock, moved by hand."""
    now = [1000.0]
    monkeypatch.setattr(catalogue.time, "monotonic", lambda: now[0])
    return now


def test_the_setting_reads_from_the_file_and_defaults_off(settings_state):
    settings_state.mkdir(parents=True, exist_ok=True)
    (settings_state / "settings.json").write_text(
        json.dumps({"RADARR_4K_URL": "http://4k", "RADARR_4K_READ_ONLY": True})
    )
    config.apply(config.load())
    assert config.current().arr_instance("radarr-4k").read_only
    assert not config.current().arr_instance("radarr").read_only


def test_a_misspelt_switch_is_a_problem_not_a_writable_library(settings_state, monkeypatch):
    monkeypatch.setenv("SONARR_READ_ONLY", "sometimes")
    config.apply(config.load())
    assert "SONARR_READ_ONLY='sometimes' is neither true nor false" in config.errors()


def test_the_settings_api_round_trips_the_switch(settings_state):
    created = {
        "id": "radarr-4k",
        "create": True,
        "values": {"url": "http://4k", "read_only": True},
    }
    assert settings.update({"arr_instances": [created]}) == []
    assert config.current().arr_instance("radarr-4k").read_only
    (snapshot,) = [
        entry for entry in settings.snapshot()["arr_instances"] if entry["id"] == "radarr-4k"
    ]
    assert snapshot["fields"]["read_only"] == {
        "value": True,
        "env": False,
        "env_name": "RADARR_4K_READ_ONLY",
    }
    assert settings.update({"RADARR_READ_ONLY": True}) == []
    assert config.current().arr_instance("radarr").read_only
    refused = {"id": "radarr", "values": {"read_only": "yes"}}
    assert "must be true, false or null" in settings.update({"arr_instances": [refused]})[0]


def test_an_install_without_a_read_only_connection_lists_nothing(monkeypatch):
    monkeypatch.setattr(
        catalogue, "client_for", lambda instance: pytest.fail("nothing needs listing")
    )
    set_config(RADARR_URL="http://arr", RADARR_API_KEY="key")
    assert catalogue.read_only(DUNE) is None


def test_a_read_only_connection_claims_only_its_own_folders(four_k):
    assert catalogue.read_only(DUNE) == "Radarr 4k is read-only"
    assert catalogue.read_only(ARRIVAL) is None
    # A sibling whose name starts the same is not inside the folder.
    assert catalogue.read_only(f"{FOUR_K}/Dune (2021) Extras/x.mkv") is None


def test_the_connection_that_delivered_a_file_claims_it_before_any_listing(four_k):
    """An import lands before the next listing shows its folder."""
    assert catalogue.read_only(f"{FOUR_K}/Arrival (2016)/x.mkv", "radarr-4k") == (
        "Radarr 4k is read-only"
    )


def test_a_name_labels_the_refusal(four_k):
    set_config(RADARR_4K_NAME="UHD")
    assert catalogue.read_only(DUNE) == "UHD is read-only"


def test_a_listing_is_reused_until_it_expires(four_k, clock):
    catalogue.read_only(DUNE)
    catalogue.read_only(ARRIVAL)
    assert four_k.asked == 1
    clock[0] += catalogue.LISTING_TTL
    catalogue.read_only(ARRIVAL)
    assert four_k.asked == 2


def test_an_edited_connection_is_listed_afresh(four_k):
    catalogue.read_only(DUNE)
    set_config(RADARR_4K_URL="http://elsewhere")
    catalogue.read_only(DUNE)
    assert four_k.asked == 2


def test_a_connection_that_never_answered_refuses_every_file(four_k, clock):
    four_k.down = True
    assert catalogue.read_only(ARRIVAL) == "Radarr 4k is read-only and could not be listed"
    # Not asked again on every file while it is down.
    catalogue.read_only(ARRIVAL)
    assert four_k.asked == 1
    four_k.down = False
    clock[0] += catalogue.RETRY_SECONDS
    assert catalogue.read_only(ARRIVAL) is None


def test_the_last_answer_stands_in_while_the_connection_is_down(four_k, clock):
    catalogue.read_only(DUNE)
    four_k.down = True
    clock[0] += catalogue.LISTING_TTL
    assert catalogue.read_only(DUNE) == "Radarr 4k is read-only"
    assert catalogue.read_only(ARRIVAL) is None
    assert four_k.asked == 2


def test_a_read_only_file_is_planned_and_reported_but_never_rewritten(four_k, monkeypatch):
    monkeypatch.setattr(
        processing, "build_plan", lambda path, lang, policy=None: needed_plan(path)
    )
    monkeypatch.setattr(
        processing, "apply_plan", lambda *args, **kwargs: pytest.fail("must not rewrite")
    )
    monkeypatch.setattr(
        processing.mkvtag, "write_lang", lambda *args, **kwargs: pytest.fail("must not tag")
    )
    result = process(Job(DUNE), dry_run=False)
    assert result.status is Status.PENDING
    assert result.detail == "Radarr 4k is read-only"
    assert result.plan is not None and result.plan.needed


def test_a_writable_file_beside_it_is_still_rewritten(four_k, stub_rewrite):
    stub_rewrite(needed_plan(ARRIVAL))
    assert process(Job(ARRIVAL), dry_run=False).status is Status.MODIFIED


def test_a_connection_made_read_only_mid_rewrite_discards_the_result(four_k, monkeypatch):
    """Checked again at publication, since an encode can take an hour."""
    monkeypatch.setattr(
        processing, "build_plan", lambda path, lang, policy=None: needed_plan(path)
    )

    def apply(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        set_config(RADARR_4K_READ_ONLY=True)
        # As the executor asks it, just before the rename.
        try:
            claim()
        except InterruptedError as err:
            return Outcome.DEFERRED, f"{err}, result discarded"
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "apply_plan", apply)
    set_config(RADARR_4K_READ_ONLY=False)
    result = process(Job(DUNE), dry_run=False)
    assert result.status is Status.DEFERRED
    assert result.detail == "Radarr 4k is read-only, result discarded"


def test_an_import_from_a_read_only_connection_is_booked_pending(four_k, monkeypatch):
    monkeypatch.setattr(
        processing, "build_plan", lambda path, lang, policy=None: needed_plan(path)
    )
    monkeypatch.setattr(
        processing, "apply_plan", lambda *args, **kwargs: pytest.fail("must not rewrite")
    )
    arr = client_for(config.current().arr_instance("radarr-4k"))
    jobs.handle(Job(f"{FOUR_K}/New (2026)/New (2026).mkv", "eng", 1, arr))
    (entry,) = read_events()
    assert entry["event"] == "pending"


def test_an_applying_sweep_books_a_read_only_file_without_queueing_it(
    four_k, monkeypatch, tmp_path
):
    root = tmp_path / "library"
    (root / "Dune (2021)").mkdir(parents=True)
    (root / "Dune (2021)" / "Dune (2021).mkv").write_text("not really a video")
    set_config(MEDIA_DIRS=[str(root)])
    four_k.folders = [str(root / "Dune (2021)")]
    monkeypatch.setattr("trackstarr.sweep.all_arrs", list)
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="sweep", policy=None, cancel=None, observation=None: (
            ProcessResult(Status.PENDING if dry_run else Status.MODIFIED, None)
        ),
    )
    counts = sweep(dry_run=False)
    assert counts[Status.PENDING] == 1
    assert counts[Status.MODIFIED] == 0
    rows = (tmp_path / "state" / "pending.tsv").read_text().splitlines()[1:]
    assert "Radarr 4k is read-only" in rows[0]


def test_a_track_edit_on_a_read_only_file_is_refused(four_k, monkeypatch, tmp_path):
    folder = tmp_path / "Dune (2021)"
    folder.mkdir()
    path = folder / "Dune (2021).mkv"
    path.write_bytes(b"x")
    four_k.folders = [str(folder)]
    monkeypatch.setattr(retag, "edit_track", lambda *args: pytest.fail("must not edit"))
    result = retag.apply(str(path), 1, retag.Edit("jpn"), "operator", {})
    assert result.status is retag.Outcome.REFUSED
    assert result.detail == "Radarr 4k is read-only"


def test_the_title_sheet_says_why_a_file_is_left_unchanged(one_title):
    """From the settings and the folders already listed: a page never waits on
    an *arr to say so."""
    set_config(RADARR_READ_ONLY=True)
    detail = library.title("arr:radarr:7")
    (dune,) = detail["files"]
    assert dune["read_only"] == "Radarr is read-only"
    assert detail["folders"][0]["read_only"] == "Radarr is read-only"


def test_a_writable_title_carries_no_reason(one_title):
    detail = library.title("arr:radarr:7")
    assert "read_only" not in detail["files"][0]
    assert "read_only" not in detail["folders"][0]


def test_a_read_only_connection_sharing_the_folder_claims_its_files():
    """The first configured connection owns a shared folder, but either claim
    keeps it unchanged."""
    set_config(RADARR_4K_READ_ONLY=True)
    shared = library.Source("/movies/Dune", configured_arr(), 7, "", ("radarr-4k",))
    assert library._read_only(shared) == "Radarr 4k is read-only"
    assert library._read_only(library.Source("/home-videos")) is None
