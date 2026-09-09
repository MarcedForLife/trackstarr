# Refactor plan

Five findings from a review on 2026-09-09, all landed. What each one turned out
to be is below.

Branch `feature/web-ui`, commits `cca398b` (this doc) onward.

| #   | What                                                | Where                      |
| --- | --------------------------------------------------- | -------------------------- |
| 1   | Extract the duplicated run-precondition preamble     | `api.py`                   |
| 2   | Split the poster cache out of the library            | `library.py`               |
| 3   | Split the per-file log buffer out of the registry    | `runs.py`                  |
| 4   | Cut the tests' reach into module privates            | `tests/`                   |
| 5   | Stop reloading config to apply a settings write      | `config.py`, `settings.py` |

## Running the gate

```
.venv/bin/ruff check src tests
.venv/bin/mypy
.venv/bin/python -m pytest --cov -q
```

Coverage is `fail_under = 100`, so a line arriving without a test fails the
build. A new branch of code takes a test or an explicit `# pragma: no cover`
with a reason.

The suite refuses to start without ffmpeg 8.1 or newer on PATH. `conftest.py`
detects it by writing a per-stream MP4 title and reading it back, since
distribution builds are usually older and back-port unpredictably. A machine
whose ffmpeg is too old gets a `UsageError` and no tests at all, which reads
like a broken checkout and is not one. See the CI workflow for the build it
installs.

## Conventions this work settled on

Public `reset()` puts a module back to how a fresh process finds it. Public
`forget()` drops a memo. Promote a private only when it is the covered unit
sitting inside an uncovered thread loop, never to give a test a way in.

A setting is read through `config.current()`. Take the snapshot once where
several are read together, since a save swaps the whole thing between two calls.

## What landed

### 1. The run-precondition preamble was written twice

`_start_sweep` and `_recheck_titles` both opened with the same eighteen lines:
read the body, validate `mode`, refuse with 409 while paused, refuse with 409
naming the run when `runs.cache_holder()` answers.

Two helpers rather than one. `_run_mode` returns the mode or None with its 400
served, and `_walk_refused` answers whether a walk can start now. Two, because
`_recheck_titles` parses its ids between the two checks and a single helper
would have moved that below them. A malformed body should be answered before
the service reports itself busy, so the ids check stayed where it was.

No test changed.

### 2. `library.py` held two unrelated things

The poster half is now `covers.py`, 186 lines, and `library.py` is 782, down
from 943. The dependency runs one way, covers imports library and never the
reverse, through a new `library.known()` that hands back the `Shelf`. That
replaced two private reads the old code did in place, and `selected()` goes
through it now as well.

One behaviour moved. `library.forget()` used to clear the missing-poster record
and cannot reach it any more, so `covers.forget()` is its own call beside
`links.forget()` in `_update_settings`. The other three callers of
`library.forget()` do not need it, since a ratings refresh and a verdict clear
say nothing about posters.

The tests split the same way, and `media`, `movie`, `stub_arrs`, `cache` and
`pending` moved to `conftest.py` so both files can reach them.

### 3. `runs.py` held three concerns

The per-file log buffer is now `runlog.py`, 94 lines, and `runs.py` is 744, down
from 830. It already ran on a lock of its own because a log handler waiting on
the registry lock would deadlock the first caller that logs while holding it.
That comment is now the new module's opening docstring.

The seam is the two calls it always was, `runlog.attach()` from `begin()` and
`runlog.detach()` from `finish()`, both public now.

| was | now |
| --- | --- |
| `runs.capture_logs()` | `runlog.capture()` |
| `runs.forget_logs()` | `runlog.forget()` |
| `runs.lines()` | `runlog.lines()` |
| `runs._hold()` / `_release()` | `runlog.attach()` / `detach()` |

`attach` and `detach` rather than reusing `hold` and `release`, because
`runs.hold()` already means waiting out a pause.

The pause latch stayed in `runs.py`. It reads `_lock` alongside the registry and
splitting it would buy a second lock ordering to get right.

### 4. The tests reached into module privates 219 times

The count is 112 now, and the rest are there on purpose. Working through them
showed the original finding had two different things in it.

**State soldering** is the one that hurts. A test reaching into module state to
clear it or read it back binds every test in the suite to how one module happens
to hold its data, and `conftest.py` did it in three autouse fixtures, so it
bound all 1355. `jobs`, `runs`, `users` and `media_server` each have a public
`reset()` now, `ratings.forget()` drops its retry backoff as well as its table,
and the assertions that read a memo to see whether it had been dropped observe a
refetch instead. `jobs` went from 57 reach-ins to none, `media_server` from 8,
`ratings` from 5, `library` from 3, `runs` from 37 to 4.

**Calling a private helper of the module under test** is not the same thing.
`executor._verify`, `sweep._judge`, `config._bool` and the rest are ordinary unit
tests of a unit that happens to be spelt with an underscore. Promoting them would
grow four modules' public surface for callers that do not exist, which trades one
smell for a worse one. They stay. So do reads of a private constant such as
`runs._UPCOMING`, which is a test parameterised off the value under test, not
state that can leak.

Four functions were promoted, each because it was the covered unit sitting inside
an uncovered thread loop: `jobs.handle`, `jobs.park`, `jobs.recheck_parked` and
`jobs.retire`.

Two remainders need clock injection rather than an accessor, and are not worth it
on their own. `test_notify.py` backdates a run's `told` stamp to get past the
notifier's rate limit, and `test_links.py` backdates a `_found` entry to expire
it. Both would fall out of making time injectable, if that is ever worth doing.

`config` kept every one of its reach-ins for item 5, so that the snapshot could
decide what the public reading surface was rather than a `reset()` being bolted
on first and rewritten after.

### 5. A settings write reloaded the module under its readers

`settings._write()` called `importlib.reload(config)`, which re-executed the
module body and rebound 54 attributes one at a time while the sweep and the
webhook workers read them. `config` now reads both sources into one frozen
`Settings` that `current()` hands out; a save builds another with `load()` and
`apply()` swaps the pointer. `Policy.from_config()` takes it once and reads its
twelve fields off that, so a fingerprint can no longer describe a state nobody
saved. `importlib.reload` appears nowhere.

The parsers moved onto `_Source`, one read of the environment and the file,
which collects its own problems instead of appending to module lists. Every
name parsed from those two sources is on the snapshot; `STATE_DIR` and
`LOG_LEVEL` are environment-only and stay module constants.

`_timezone()` split in two. The parse returns the zone name, and `apply()` is
what writes `TZ` and calls `tzset()`: once per swap, rather than once per parse
from inside the reload, with other threads formatting log lines.

`tracks.resolved_layouts`, `downmixed_layouts`, `removed_layouts` and
`resolved_langs` take their entries, and `policy.resolved_modes` takes the
stated modes, so `from_config` hands all five one snapshot's values rather than
each of them reading their own. `tracks` no longer imports `config` at all.

The tests say what the settings are through `conftest.set_config`, which
installs a snapshot; the autouse fixture's `config.reset()` undoes it. That is
207 calls in place of `monkeypatch.setattr(config, ...)`, and it retires the
`_no_services` fixture, the `_LOAD_ERRORS` one, and every
`importlib.reload(config)` the suite used to need to make a setting take effect.
`test_config.py`'s parser tests build a `_Source` and read `problems` off it.
