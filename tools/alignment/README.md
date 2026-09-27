# Alignment evaluation

Phase 1 research tools, independent of the Trackstarr runtime. Python 3.14,
FFmpeg and ffprobe must already be installed. Generated media stays outside Git.

```sh
python tools/alignment/corpus.py /tmp/alignment-corpus
uv run pytest tools/alignment -q
```

The corpus contains twelve 60-second cases with known mappings, including offsets,
speed changes, frame-rate conversion without speed change, cropping, multichannel
audio, unrelated audio, a deleted scene, reversed video and black/silent content.
The 50 ms scoring tolerance is an experimental test bound. It is not a production
acceptance policy. Generated chirps and test patterns cannot establish dub accuracy.

## RedSync proof of concept

Download and extract the Linux x64 asset from
[RedSync v0.2.2](https://github.com/720pixel/RedSync/releases/tag/v0.2.2) into a
temporary directory. The runner verifies the binary's SHA-256 before execution.
The archive hash is
`329439b05c9ec9093702cd3e22e56b36187056255f1a2d95b732b47baf08cf06`.

```sh
python tools/alignment/evaluate.py /tmp/alignment-corpus /tmp/RedSync /tmp/alignment-report.json
```

This calls `sync --dry-run --json`. RedSync's reference is Trackstarr's target,
and RedSync's target is Trackstarr's source. The mapping is
`target_time = scale * source_time + offset`, with positive offset delaying the
source. No media output is expected. Reports and corpus directories must be new.
Cases can set `target_audio_stream` and `source_audio_stream` to absolute stream
indexes from ffprobe. Omit them to use each file's first audio stream.

Each run has a disposable workspace. Timeouts and interruption kill its process
group. Fixture hashes are checked before and after analysis. Resource counters
include wall time, the largest child-process RSS and filesystem I/O blocks on
Linux. RSS is not simultaneous process-tree memory. Cached reads can report zero
I/O blocks. Workspace size at exit is not peak workspace size.

`mapping_recovered` means that the proposed mapping matches independent ground
truth timestamps. It grants no publication permission. `unsafe_linear_proposal`
means that the engine returned a linear mapping for a known negative case.
Nonzero exit codes remain `backend_error_or_refusal` because this upstream
interface does not distinguish them structurally. Raw stdout and stderr are kept
in the local report. No tools are downloaded by these scripts.

## Missing German audio from an episode

```sh
python tools/alignment/episode.py /path/to/Dark.mkv /tmp/dark-pair
python tools/alignment/evaluate.py /tmp/dark-pair /tmp/RedSync /tmp/dark-report.json
```

The episode must contain German audio. The builder copies
the first video stream into both variants, keeps non-German audio in the target
and German audio in the source. It recognises `de`, `deu` and `ger`. Chapters and
global metadata are copied. Subtitles, attachments and additional video streams
are omitted from these analysis fixtures. The original is hashed before and after
preparation. Files are stream-copied with their timestamps preserved.

If the episode has only German audio, the target has video only. This is a valid
missing-audio fixture for a visual matcher. RedSync's audio matcher requires an
audio track in both files and will return an error for that pair.

This tests the missing-dub setup without arr. Copying the dub back through
Trackstarr requires the later planner/executor phases. The pair alone tests a
shared timeline, so independent releases and real-media review remain necessary.

The manifest records the original path locally. Keep real-media manifests and
reports outside Git. [Initial findings](../../docs/alignment-evaluation.md) record
the current selection status and remaining work.

For Spanish audio, use `--language-tags es,spa`. The Eternaut S01E01 has Spanish
and English stereo and surround tracks in the tested library. Removing Spanish
produces an English-only target suitable for the cross-dub analysis experiment.

## Resuming on this host

Existing disposable fixtures are `/tmp/trackstarr-alignment-corpus-v2`,
`/tmp/trackstarr-eternaut-pair` and `/tmp/trackstarr-dark-pair`. The pinned binary
is `/tmp/trackstarr-alignment-research/bin/RedSync`. These paths are optional
caches, not checked-in inputs. Each episode manifest records its original path
under the TV library mount and the fixture hashes. Check available disk space
before regenerating full episodes, since each pair copies the video twice.

The Eternaut manifest has two cases. Stereo uses absolute stream index 1 in both
files, and surround uses index 2. Regeneration creates a first-audio case by
default, so add the explicit stream selections to reproduce both recorded runs.

Local validation after the initial implementation passed 27 evaluation tests,
Ruff and mypy. The unchanged application passed 2,162 tests with 100% coverage
and 17 skips. Web checks, 494 tests, lint and production/demo builds passed.
Alpine image builds and a full transitive licence audit have not been run for an
alignment backend.
