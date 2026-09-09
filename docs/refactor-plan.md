# Refactor plan

Five findings from a review on 2026-09-09. Four have landed. Item 5 is what is
left, and it is the only one that can lose data.

Branch `feature/web-ui`, commits `cca398b` (this doc) through `221d433`.

| #   | What                                                | Where                     | State |
| --- | --------------------------------------------------- | ------------------------- | ----- |
| 1   | Extract the duplicated run-precondition preamble     | `api.py`                  | done  |
| 2   | Split the poster cache out of the library            | `library.py`              | done  |
| 3   | Split the per-file log buffer out of the registry    | `runs.py`                 | done  |
| 4   | Cut the tests' reach into module privates            | `tests/`                  | done  |
| 5   | Stop reloading config to apply a settings write      | `config.py`, `settings.py`| 2 days |

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

## 5. Reloading config to apply a settings write races the readers

`settings._write()` (`settings.py:270`) calls `importlib.reload(config)`, which
is how a save takes effect without a restart. The write path around it is
careful. It takes `_WRITE_LOCK`, validates, and rolls the whole file back on a
new problem.

The reload is not covered by any of that. It re-executes the module body,
rebinding 54 attributes one at a time, while the sweep and the webhook workers
read `config.*` freely. `Policy.from_config()` (`policy.py:204`) reads twelve of
those attributes in one expression. A call landing inside the reload window can
mix pre-save and post-save values, and the `fingerprint()` it produces then
matches no state that was ever saved. `sweep_cache` keys every verdict on that
fingerprint, so the next read finds a mismatch and re-probes the whole library.
It looks like a cache bug rather than a settings one.

The same reload runs `_timezone()` (`config.py:346`), which writes
`os.environ["TZ"]` and calls `time.tzset()` process-wide while other threads are
formatting log lines.

### The approach

Make config a frozen snapshot that readers take by reference, so a save builds a
new snapshot and swaps one pointer. `Policy.from_config()` then reads twelve
fields off one object that cannot change under it.

This was chosen over the cheap alternative, which is to hold the pause latch
across the save so no worker is mid-read. That one leaves the reload in place,
does not help the CLI, which shares the module, and is a second thing to
remember about a module that already has too many.

### Where to start

Read `config.py` top to bottom first. It is 646 lines and almost all of it is
one pattern: a module-level attribute assigned from a parser that reads the
environment, then the settings file, then a default. The parsers are `_raw`,
`_bool`, `_int`, `_list`, `_set`, `_ordered`, `_choice`, `_regex`, `_secret`,
`_path_map` and `_rule_modes`, and they are the thing that has to move onto the
snapshot.

Two callers make the shape clear. `Policy.from_config()` is the reader that
must see a consistent set. `settings.update()` is the writer, and its
`_write` / roll-back pair is where the pointer swap goes.

Design the test surface as part of it rather than after. The tests call config's
parsers directly 51 times, patch a private with `setattr` 10 more, and call
`importlib.reload(config)` 11 times to make a setting take effect. All of it
wants the same thing the snapshot wants, a public way to say what the settings
are for this read. Land that and the call sites move once.

```
grep -rhoE 'config\._[A-Za-z][A-Za-z_]*|setattr\(config, "_[A-Za-z_]+"' tests/*.py | wc -l
grep -rc 'importlib.reload(config)' tests/*.py | grep -v ':0'
```

`_timezone()` is the one parser with a side effect on the process rather than a
value, so it does not belong on a frozen snapshot. Decide where it goes early.

Done when `importlib.reload` appears nowhere in `src/`, and
`tests/test_settings.py` no longer reloads config to set up a case.

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

`config` keeps every one of its reach-ins. They belong to item 5, where the fix
is whatever public reading surface the snapshot lands on, not a `reset()` bolted
on first and rewritten after.
