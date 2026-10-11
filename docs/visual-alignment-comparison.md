# Visual alignment comparison

Neither original candidate qualifies for selection. The FFmpeg fingerprint
prototype recovers most synthetic mappings but cannot establish sufficient evidence
on the short Dark control. AVSync's isolated visual stage supplies too few anchors
and still requires a large scientific stack. A later
[consensus matcher](#consensus-follow-up) over the same fingerprints passes both
short corpora except crop, whole same-release episodes and held-out re-encoded
episodes. No backend is selected and phase 1 remains open.

## Scope and implementation

Both candidates use the existing twelve synthetic cases and four 30-second Dark
controls. These include offsets, speed changes and reversed picture. The Dark
files share one release. No independent-release or whole-episode accuracy claim
follows from this comparison. Library originals were not accessed.

[The runner](../tools/alignment/compare_visual.py) passes only input paths and
backend settings to a disposable worker. Ground truth stays in the parent and
scores proposals after analysis. Input hashes are checked before and after each
corpus run. Timeouts kill the process group, and workspaces are removed afterward.
Reports retain numeric evidence, not images or fingerprints. No result authorises
publication.

The experiment refuses inputs longer than 120 seconds. Parameters were fixed
before benchmarking. They are not calibrated production acceptance limits.

## FFmpeg fingerprints

FFmpeg exports MPEG-7 frame signatures after changing the time base to microseconds,
without resetting input timestamps. The prototype compares the 380 ternary values
using two integer bit masks. It searches every target frame for up to twelve source
probes in each of eight time regions. Distant alternatives with similar scores
make a probe ambiguous.

Even regions supply the linear least-squares fit. Odd regions supply validation,
with independent full-timeline searches. Validation never refits the mapping.
The report includes every matched anchor, distance, alternative distance, region,
residual and match fraction. Missing regions, inconsistent timing and unsupported
scales require review. Repeated-content rejection also has a deterministic test
with duplicated fingerprints, beyond the periodic video patterns in the corpus.

| Control | Result |
| --- | --- |
| Identity, trim, leading black, surround, different audio | Mapping recovered with zero scored error. |
| Speed, speed plus trim, frame-rate-only conversion | Mapping recovered, maximum scored error below 3 ms. |
| Crop | Review, no sufficiently close matches. |
| Internal cut | Review, residuals contradict one linear mapping. |
| Reversed picture | Review, fitted scale is negative. |
| Black/silence | Review, no usable correspondence evidence. |
| Dark identity, trim and speed | Review, ambiguous or missing regions. Validation residuals also exceed 50 ms. |
| Dark reversed picture | Review, ambiguous regions and negative fitted scale. |

[Synthetic results](../tools/alignment/results/fingerprints-synthetic.json) and
[Dark results](../tools/alignment/results/fingerprints-dark.json) retain the
measurements. The synthetic speed fixtures include duplicate presentation
timestamps. The parser permits ties, rejects decreasing timestamps and requires
a nonzero timeline span.

## Temporal-context follow-up

The separate `temporal` engine compares five fingerprints across 800 ms. Every
source probe searches the full target timeline at eleven local scales from 0.9
to 1.1. The mean fingerprint distance ranks candidates. Source windows remain
inside their fit or validation region, and missing context counts against the
region's match fraction. Existing acceptance limits are unchanged.

[Synthetic results](../tools/alignment/results/temporal-synthetic.json) recover
the same eight valid mappings as the single-frame baseline. Maximum ground-truth
error is 4.4 ms. Crop and all three negative cases remain review-required.

[Dark results](../tools/alignment/results/temporal-dark.json) remain review-required
for all four cases. Maximum validation residuals for identity, trim and speed
fall from 78.5, 52.5 and 93.1 ms to 36.4, 6.8 and 24.5 ms respectively. Each valid
case still has one region with no accepted anchors and other regions below the
required match fraction. Reversed picture supplies only eight accepted anchors.

Deterministic tests show that context can distinguish repeated frames with different
neighbours. Fully repeated sequences and static scenes remain ambiguous. Tests
also cover offset and speed, timestamp gaps, reversed picture, internal cuts and
held-out timing errors. No fit prediction narrows the validation search.

This bounded experiment improves some timing measurements without resolving the
Dark coverage failure or crop tolerance. It adds no dependencies and does not
justify selecting the matcher. Independent-release controls are still missing.
The available Dark and Eternaut fixtures each derive from one release. Evaluation
needs two independently released copies of the same content and separately
reviewed timestamp correspondences. This comparison is pending because the user
has no suitable pair available.

## Consensus follow-up

[The consensus engine](../tools/alignment/consensus.py) replaces per-probe
decisions with a vote. Each source frame votes for every target frame within 8 bits
of its best match, at most one per 500 ms stretch, and abstains when more than
eight tie. Votes are cast at seven fixed scales (identity, and 1001/1000, 25/24
and 25025/24000 with their inverses) and counted per 20 ms offset bin. Two folds each
fit on alternate regions and check the other half, so every region is checked by a
mapping it did not vote for.

A checked frame confirms the mapping when the predicted frame is its distinct
match, and contradicts it when it matches better anywhere else. Three consecutive
contradictions mark the region as contradicting, which catches a one-frame cut.
Timing uses only exact anchors, frames that also beat their neighbours by the
ambiguity margin. Other confirmations count toward coverage. The distance,
ambiguity, span and 50 ms residual limits are the baseline's.

Probes are up to 100 evenly spaced source frames per region. Only probes vote and
receive the full check. Every other held-out frame is checked at its predicted
position, with a full-timeline scan only when that fails, so a cut between probes
still forms a contradicting run. A full scan costs about 130 ns per frame pair, so
the planned word index was unnecessary. Sampling left the short-corpus outcomes
unchanged.

The design changed twice against the synthetic corpus before the Dark run. The
first run let neighbouring frames vote, so a slightly wrong scale won. The second
found the true mapping everywhere but refused it, because testsrc2 repeats every
12 seconds and each repeat drew 85% of the winner's votes. Voting then adopted the
baseline's ambiguity margin. The Dark controls ran once with that design and
[all went to review](../tools/alignment/results/consensus-dark-heldout.json).
Identity, trim and speed confirmed seven of eight regions without contradictions,
but one-frame ties in slow scenes pushed residuals to 69 to 82 ms. Exact anchors
were added in response, so the passing Dark result below is not held out.

| Control | Result |
| --- | --- |
| Synthetic valid mappings except crop | Recovered, maximum ground-truth error 1.8 ms. Every region confirmed. |
| Synthetic crop | Review, no votes. |
| Synthetic internal cut, reversed picture, black/silence | Review. |
| Dark identity, trim and speed | Recovered, maximum error 2.5 ms. 244 exact anchors, seven of eight regions confirmed. |
| Dark reversed picture | Review, insufficient votes. |

[Synthetic results](../tools/alignment/results/consensus-synthetic.json) and
[Dark results](../tools/alignment/results/consensus-dark.json) retain the fold
hypotheses, region tallies and residuals. Deterministic tests cover cuts from one
frame to two seconds, fully repeated content, static regions and neighbour ties.

### Whole episodes

Whole episodes are extracted at the samples' 320-pixel width with every decoder
thread and read as a stream. The Dark and Eternaut pairs ran once, with the design
frozen before the run. They ran before the two fixes below, which left every
lossless short-corpus result unchanged.

| Pair | Result | Wall time |
| --- | --- | --- |
| Dark S01E01, 51 minutes | Recovered exactly. Eight of eight regions confirmed, 378 exact anchors. | 36.6 min |
| The Eternaut S01E01, 45 minutes, stereo and surround cases | Both recovered exactly. Eight of eight regions confirmed, 412 exact anchors. | 28.7 and 29.7 min |

The runner-up mapping drew 6 to 15% of the winner's votes and no region
contradicted. The last Eternaut region is mostly credits, where 68 probes were
uninformative, and it still confirmed.
[Dark results](../tools/alignment/results/consensus-dark-episode.json) and
[Eternaut results](../tools/alignment/results/consensus-eternaut-episode.json)
retain the evidence.

Both pairs share one video stream, because each fixture was made by removing audio
from one file with stream copies. The runs show that the matcher scales and does
not invent ambiguity on real long content. Aligning independent releases remains
untested. Decoding two 4K HEVC streams takes almost all the time. From the scan cost,
matching should take well under a minute, but it was not timed separately.

### Re-encoded stand-ins

Every pair above shares one video stream.
[The re-encode builder](../tools/alignment/reencode.py) makes a stand-in for
another release from a pair's source: 720p through a Lanczos scaler, a contrast,
saturation and gamma change, x264 veryfast at CRF 24 and a two-second trim. A
second variant stream-copies it with timestamps scaled to PAL speed, 25/24 from
24 fps or 1001/960 from 23.976. Ground truth is the first kept frame's timestamp,
checked against frame checksums. The encodes came out at 0.5 to 1.5 Mbit/s, harsher
than typical releases.

The first two held-out titles failed. [Dark](../tools/alignment/results/consensus-dark-reencode-heldout.json)
and [The Eternaut](../tools/alignment/results/consensus-eternaut-reencode-heldout.json)
both fitted their mappings within 2 ms but went to review over false
contradictions. In Dark's opening fade-in, the grade lifted dim frames above zero
confidence on the variant side only, and the matcher skipped the target frames it
should have compared. It now compares the predicted frame whatever its confidence.
In a dim, heavily compressed Eternaut scene, matches only 2 to 7 bits closer than
the predicted frame counted as contradictions. A contradiction must now beat the
predicted frame by the ambiguity margin, as a confirmation must. Neither fix moved
a limit, and the short corpora came out identical after each.

Those two titles shaped the fixes, so Futurama S01E01 (animation) and Breaking Bad
S01E01 (live action), both at 23.976 fps, ran once as a fresh held-out test.

| Held-out case | Result |
| --- | --- |
| Futurama, trim | Recovered, 0.34 ms error, 112 exact anchors. |
| Futurama, trim and 1001/960 speed-up | Recovered, 0.85 ms error. |
| Breaking Bad, trim | Recovered, 0.06 ms error, 336 exact anchors. |
| Breaking Bad, trim and 1001/960 speed-up | Recovered, 0.45 ms error. |

Every region confirmed and none contradicted. The runner-up drew at most 26% of the
winner's votes, the most on Futurama's animation.
[Futurama](../tools/alignment/results/consensus-futurama-reencode-heldout.json) and
[Breaking Bad](../tools/alignment/results/consensus-breakingbad-reencode-heldout.json)
retain the evidence. With both fixes, the
[Dark](../tools/alignment/results/consensus-dark-reencode.json) and
[Eternaut](../tools/alignment/results/consensus-eternaut-reencode.json) stand-ins now
recover within 4 ms too, though those titles are no longer held out.

The matcher adds no dependencies but does not qualify for selection yet. A stand-in
keeps its master's frames one for one, so different edits, masters and letterboxing
remain untested. Crop still fails on the synthetic corpus.

## AVSync extraction

The wrapper loads six hash-verified functions from
[the pinned source](https://github.com/stinkybread/avsync/blob/ece9cb6e66b4b7aa7437f23e5b21574aced6cdcc/AVSync_v14.py).
Their 483 lines include scene extraction, matching and filters plus subprocess
helpers. The upstream application, audio processing, checkpoint loading and muxing
never run. No upstream source is vendored.

The host FFmpeg rejects upstream's removed `-vsync` option. A compatibility wrapper
substitutes `-fps_mode` with the same `vfr` value and raises on extraction failure.
Other visual algorithms and default thresholds remain unchanged. Headless OpenCV
replaces the GUI package. Nonzero stream starts are unsupported because upstream
does not preserve container timestamps.

[Synthetic results](../tools/alignment/results/avsync-synthetic.json) contain no
anchors. Default scene detection finds no qualifying target frames.
[Dark results](../tools/alignment/results/avsync-dark.json) contain three accurate
anchors for identity and trim, one anchor for speed, and none for reversed picture.
All require review. The identity and trim fits have insufficient temporal coverage.

The initial anchor constrains later searches to its offset and a forward-only
window. Match scores and competing candidates are discarded from the returned
anchors. A production adapter would need symmetric speed-aware search, ambiguity
evidence and independent validation. Those changes would require maintaining
modified matching code, beyond this extraction wrapper.

## Footprint and remaining evidence

[The environment inventory](../tools/alignment/results/avsync-footprint.json)
records seven installed distributions totalling 356,661,411 bytes, about 340 MiB,
on CPython 3.14 Linux x86_64 with glibc. OpenCV and NumPy remain necessary for
matching. Perceptual filtering uses Pillow and ImageHash's SciPy-based pHash.
ImageHash also declares PyWavelets, whose wavelet hash is not used here. tqdm
supplies progress reporting. This exceeds the plan's preference for a small
dependency footprint. Installed size is not an Alpine container delta.

The fingerprint prototype adds no Python packages. Trackstarr would own its XML
parser, correspondence search, ambiguity policy and validation logic. Exhaustive
search and full frame exports are bounded here by short inputs. Long-file
sampling, temporal context, crop tolerance and resource bounds remain unresolved.

Resource reports measure wall time, largest-process RSS, cached filesystem block
I/O and workspace size at exit. They do not measure simultaneous process-tree
memory, peak workspace or HDD throughput. All runs reported zero input blocks.

| Engine and corpus | Wall seconds per pair | Largest-process RSS | Largest workspace at exit |
| --- | --- | --- | --- |
| Fingerprints, synthetic | 0.97 to 1.72 | 29.3 to 30.0 MiB | 4.16 MiB |
| Fingerprints, Dark | 4.27 to 4.32 | 27.3 to 28.4 MiB | 2.05 MiB |
| Temporal context, synthetic | 1.02 to 3.12 | 30.5 to 34.6 MiB | 4.16 MiB |
| Temporal context, Dark | 4.92 to 5.02 | 27.4 to 27.7 MiB | 2.05 MiB |
| Consensus, synthetic | 1.06 to 2.42 | 31.8 to 32.7 MiB | 4.13 MiB |
| Consensus, Dark | 4.42 to 4.62 | 27.6 to 28.2 MiB | 2.02 MiB |
| Consensus, whole 4K episodes | 1,721 to 2,196 | 463 to 556 MiB | 2.2 KiB, export deleted after parsing |
| Consensus, held-out 1080p re-encodes | 98 to 394 | 135 to 194 MiB | 2.2 KiB |
| AVSync, synthetic | 0.41 to 0.47 | 66.5 to 68.1 MiB | Under 1 KiB, no scenes extracted |
| AVSync, Dark | 1.52 to 1.72 | 70.1 to 99.6 MiB | 1.45 MiB |

The inventory records package licence metadata and licence-file hashes.
Bundled native libraries still need a transitive
licence review. Alpine amd64/arm64 packaging and incremental image size are untested
for the extracted AVSync environment.

Phase 1 still needs representative independent-release controls and a solution to
crop. Keep this corpus as regression evidence.
Selection also requires the packaging, resource and real-media review evidence
in the main plan. Validation limits remain unchanged.

## Validation

The research suite passed 71 tests. The application passed 2,162 tests with 100%
coverage and 17 skips. Ruff and mypy passed, including the new research modules.
Frontend checks, 494 tests, lint and production/demo builds passed. Application
HTTP tests required loopback socket access outside the restricted sandbox.

The consensus follow-up changed only research code. The research suite then passed
98 tests, and Ruff and mypy passed on the new modules.
