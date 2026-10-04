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

## Bounded visual comparison

The fingerprint prototype uses only the existing Python and FFmpeg installations.
It exports signatures into a disposable workspace and fits alternating time regions,
then validates against regions excluded from the fit. Each region requires usable,
unambiguous correspondence. Inputs longer than 120 seconds are unsupported.

```sh
python tools/alignment/compare_visual.py run --engine fingerprints \
  --corpus /tmp/alignment-corpus --report /tmp/fingerprints.json
```

Use `--engine temporal` for the separate temporal-context experiment. It compares
five fingerprints across 800 ms, searching eleven local timing scales from 0.9
to 1.1. Source windows stay within their fit or validation region. Missing windows
count against coverage. The baseline distance, ambiguity, coverage and residual
limits remain unchanged. Run both engines on the same corpus for comparison.

`--engine consensus` runs the consensus matcher. Source frames vote for an offset
at seven fixed speed ratios, and two folds each check the regions the other
fitted. Timing uses only frames that also beat their neighbours. Its distance,
ambiguity, span and residual limits are the baseline's. No engine has passed
backend selection.

`--whole-episodes` lifts the 120-second bound for the consensus engine. It
extracts at 320 pixels wide with every decoder thread, reads the export as a stream
and deletes it after parsing. The timeout must cover decoding both files.

```sh
python tools/alignment/compare_visual.py run --engine consensus --whole-episodes \
  --timeout 7200 --corpus /tmp/trackstarr-dark-pair --report /tmp/dark-episode.json
```

Reports name the corpus, temp and home directories `<corpus>`, `<tmp>` and `~`.

`reencode.py` makes stand-ins for another release from a pair: a graded 720p x264
re-encode with a two-second trim, and a PAL-speed retime of it. The pair's source
must run at 24 or 23.976 fps.

```sh
python tools/alignment/reencode.py /tmp/trackstarr-dark-pair /tmp/dark-reencode
```

The AVSync wrapper requires a separate checkout at
`ece9cb6e66b4b7aa7437f23e5b21574aced6cdcc`. It verifies the source hash and loads
six visual functions without running the application. The wrapper translates the
removed FFmpeg `-vsync vfr` option to `-fps_mode vfr` and raises on extraction
failure. Matching thresholds and algorithms retain their upstream defaults.

```sh
uv venv /tmp/avsync-research --python 3.14
uv pip install --python /tmp/avsync-research/bin/python \
  -r tools/alignment/avsync-requirements.txt
python tools/alignment/compare_visual.py run --engine avsync \
  --python /tmp/avsync-research/bin/python \
  --avsync-source /path/to/avsync/AVSync_v14.py \
  --corpus /tmp/alignment-corpus --report /tmp/avsync.json
/tmp/avsync-research/bin/python tools/alignment/footprint.py \
  /path/to/avsync/AVSync_v14.py /tmp/avsync-footprint.json
```

Run both engines against `/tmp/trackstarr-signature-dark-sample` to reproduce the
short video-only comparison. Use a new report path each time. Run benchmarks
serially to avoid competing for resources. `--timeout` bounds each worker and its
descendants. `--ffmpeg` and `--ffprobe` select installed binaries.

Reports include code hashes, settings, fit and validation anchors, residuals and
review reasons. `proposed` only means the experimental evidence checks passed.
`mapping_recovered` additionally means independent ground truth agreed within the
corpus tolerance. Technical failures remain `backend_error` or `timeout`.
No outcome authorises publication. Workers never receive corpus ground truth.

The pinned AVSync environment contains headless OpenCV and six other distributions.
It is separate from Trackstarr's runtime. The footprint inventory records installed
bytes and licence-file hashes, not an Alpine image delta or redistribution clearance.
[Comparison findings](../../docs/visual-alignment-comparison.md) record the selection
decision and measurement limits.

## FFmpeg signature experiment

```sh
python tools/alignment/signature.py /tmp/alignment-corpus /tmp/signature-report.json
```

This uses the installed FFmpeg, verifies fixture hashes before and after analysis,
preserves input timestamps and writes media only to the null muxer. The runner
uses the existing process-group cancellation and resource measurement helpers.
`--ffmpeg /path/to/ffmpeg` selects another installed build. No downloads are made.

The native report contains one timestamp pair and a matching frame count.
`single_pair_only` does not establish offset, speed or whole-programme coverage.
Ground truth scores that pair without supplying a scale to the matcher.
`match_on_negative_case` records a local match in a known invalid whole-file pair.
`no_match` requires an explicit FFmpeg message. Exit zero without a report remains
`unrecognised_report`. None of these outcomes grants publication permission.

The same harness can run in an existing Trackstarr image. Use a new report directory
owned by the image's runtime user, and substitute the image ID being evaluated.

```sh
mkdir /tmp/signature-results
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --mount type=bind,src="$PWD/tools/alignment",dst=/research,readonly \
  --mount type=bind,src=/tmp/alignment-corpus,dst=/corpus,readonly \
  --mount type=bind,src=/tmp/signature-results,dst=/results \
  --entrypoint python3 IMAGE_ID \
  /research/signature.py /corpus /results/report.json
```

Checked-in results identify the tested host binary and existing Alpine amd64 image.
The image was not rebuilt. [Findings](../../docs/alignment-evaluation.md#ffmpeg-signature-experiment)
describe native output limitations and remaining evaluation work.

The local `/tmp/trackstarr-signature-dark-sample` corpus contains four 30-second
video-only controls. The checked-in Dark reports include its manifest. Preparation
used `-ss 600 -t 30 -map 0:v:0 -an -vf scale=320:-2 -c:v ffv1 -threads 1`
on each existing Dark fixture, with `-ss` before the input and `-t` after it.
The source variants use `trim=start=1.25,setpts=PTS-STARTPTS`, `setpts=24/25*PTS`
and `reverse`, with `-fps_mode passthrough`. Parent fixture hashes were checked
before and after preparation. Run the same harness against this sample directory.

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
