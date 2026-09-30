# Alignment evaluation, phase 1

Phase 1 has started. No backend is selected and sourcing remains unimplemented.
The research harness includes a reproducible timing corpus, RedSync and FFmpeg
signature experiments, and a fixture builder for a missing dub. A
[bounded visual comparison](visual-alignment-comparison.md) now evaluates an
FFmpeg fingerprint prototype and AVSync's isolated core. Neither qualifies for
selection. No runtime dependency or container change is included.

## Candidate screening

Source revisions are recorded in [candidates.json](../tools/alignment/candidates.json).
The table distinguishes source inspections from benchmarks. Licence labels
describe upstream files, not a completed redistribution assessment.

| Engine | Interface and dependencies | Initial disposition |
| --- | --- | --- |
| [FFmpeg signature](https://ffmpeg.org/ffmpeg-filters.html#signature) | Native MPEG-7 visual matching. Present on the host and existing Alpine amd64 image. | Benchmarked without adding packages. Native report alone does not meet the alignment evidence contract. |
| [AVSync](https://github.com/stinkybread/avsync/tree/ece9cb6e66b4b7aa7437f23e5b21574aced6cdcc) | Isolated visual functions with headless OpenCV, NumPy and perceptual-hash dependencies. MIT upstream. | Benchmarked. Insufficient evidence on these controls, about 340 MiB of installed Python dependencies on the host. |
| [RedSync v0.2.2](https://github.com/720pixel/RedSync/tree/cb07566f9c48766d8f335df0caab157f5342569a) | Go binary with audio analysis through `sync --dry-run --json`. Three direct and fifteen indirect Go module declarations. MIT. | Benchmarked. Does not pass the current corpus. |
| [video-sync](https://github.com/Chaphasilor/video-sync/tree/90cb048c790648be1c037d3a2b702430e4d27110) | Visual matching inside a Node CLI with muxing and interactive prompts. Sixteen runtime package declarations. GPL-3.0. | Analysis extraction, Node footprint and redistribution review needed. |
| [audalign](https://github.com/benfmiller/audalign/tree/d87b7a93f944ee3ee436a94e2bc982118df78cac) | Python audio alignment. Pins NumPy, SciPy, matplotlib, pydub, setuptools and tqdm. MIT. | Scientific-stack footprint and old dependency pins need checking against Python 3.14. |
| [ffsubsync](https://github.com/smacke/ffsubsync/tree/de310ac6944b8260431a48ee741e7063cec49b0f) | Subtitle activity alignment, NumPy and voice-activity dependencies. MIT. | Useful comparison, but its subtitle workflow does not supply visual correspondence evidence. |
| [alass](https://github.com/kaegi/alass/tree/874f02d9577182752a0f969b6d6b98fd65bdf1fc) | Rust subtitle timing library and CLI. GPL-3.0 licence file. | Subtitle timing scope and redistribution duties need separate assessment. |
| [DubGraft](https://github.com/NightCorpse/DubGraft/tree/f48f7b9148c31322da5f5386c31e1fb874b9dc1f) | Audio-anchor analysis-only interface, NumPy and SciPy. GPL-3.0-only. | Additional candidate. Scientific-stack size, cross-dub accuracy and redistribution remain unmeasured. |

## First benchmark

[Recorded results](../tools/alignment/results/redsync-v0.2.2.json) use twelve
generated 60-second cases and FFmpeg's lossless FFV1/PCM output. No real-media
claim follows from these test patterns and chirps.

| Cases | Result |
| --- | --- |
| Identity, frame-rate-only conversion, crop, surround | Mapping recovered with zero error at the scored points. |
| Start trim of 1.25 seconds | Mapping recovered with 2 ms error. |
| Leading black, speed change, speed plus trim, different audio, internal cut | Engine error/refusal. Diagnostics report insufficient reliable anchors. |
| Reversed picture with unchanged audio | Linear mapping proposed. Visual mismatch was not detected. |
| Black picture and silence | Engine error/refusal, low audio confidence. |

These results do not qualify RedSync for automatic sourcing. The dry-run report
contains aggregate score, anchor count and residual error, but no anchor timestamps
or independent validation regions. Its measurements cannot establish the coverage
and held-out evidence required by the design. Speed correction also failed these
fixtures. This is a limitation of the tested combination, not proof that all
real-media speed changes fail.

On this Linux x64 host, cases took 0.61 to 2.07 seconds and the largest child
process used 23.6 to 26.9 MiB RSS. Input block counters were zero with cached
fixtures. These are short-fixture measurements, not HDD throughput estimates.

## FFmpeg signature experiment

The twelve synthetic cases ran on host FFmpeg `b16b5f2a01` and FFmpeg 8.1.2 in
the existing Alpine 3.24.2 amd64 image. The image required no added packages or
rebuild. [Host results](../tools/alignment/results/ffmpeg-signature-host.json) and
[Alpine results](../tools/alignment/results/ffmpeg-signature-alpine.json) retain
commands, binary hashes and raw reports. Input hashes were unchanged.

| Case | Native result |
| --- | --- |
| Identity, surround, different audio | Matched repeating picture content 24 seconds away from the known correspondence on both builds. |
| Leading black | Correct timestamp pair, target minus source was -1.5 seconds. |
| Frame-rate-only conversion | Pair differed from ground truth by 7 ms. |
| Speed change | Pair was about 24 seconds from ground truth. No timing scale was reported. |
| Speed plus trim | Host pair error was 1.75 ms. Alpine emitted no matching report. |
| Crop | Explicit no-match result on both builds. |
| Trim, internal cut, black/silence | Exit zero without a matching report on both builds. This is not a refusal or a pass. |
| Reversed picture | Both builds reported a 63-frame match. A local match cannot establish a valid whole-file mapping. |

Periodic test patterns limit conclusions about real-media accuracy. Even the
identity case demonstrates why the `whole video matching` log message cannot
authorise copying. The earlier eight-second trim smoke test recovered 1.25 seconds,
but the full-duration run above did not emit a result.

Source inspection of [the native report and XML exporter](https://github.com/FFmpeg/FFmpeg/blob/b16b5f2a01/libavfilter/vf_signature.c)
found one timestamp pair in seconds and a frame count per input pair. XML exports
each input's signatures and timestamps, not matched correspondence pairs. Debug
logs expose internal frame-index ratios, not a validated container-time mapping.
There are no distributed anchors or held-out validation results in the normal
report. The harness therefore never claims a recovered mapping.

Host runs took 0.41 to 0.97 seconds, with 174 to 186 MiB largest-process RSS.
Alpine runs took 0.46 to 1.02 seconds and 192 to 203 MiB. These cached 60-second
fixtures do not establish full-episode memory or HDD performance. ARM64 remains
untested. Native `signature` output is insufficient as the production adapter.
FFmpeg remains a possible source of fingerprints if a separately reviewed matching
implementation can meet the evidence requirements.

## Packaging and licences

The RedSync archive is 7,170,929 bytes and its executable is 17,260,728 bytes.
That executable size is not a container-size delta. Upstream embeds dovi_tool and
hdr10plus_tool and supplies their MIT notices. The Go module dependencies still
need a transitive licence inventory. RedSync can download FFmpeg when absent.
The experiment preflights FFmpeg and ffprobe, and a production integration must
make missing tools fail without network fallback.

MIT candidates require preservation of copyright and licence notices. GPL
candidates need an assessment of the actual integration and distribution method,
including corresponding-source obligations. FFmpeg, MKVToolNix and any bundled
binaries retain their own terms. No candidate is cleared for redistribution by
this report. No third-party code or binary is added to Trackstarr.

## Local media fixtures

The Eternaut S01E01 contains Spanish and English AAC stereo and E-AC-3 5.1.
The full 45-minute episode was split into an English-only target and Spanish-only
source. Both files retained the original video timeline. The original's SHA-256
was unchanged, and the fixture hashes were unchanged after analysis.

[Results](../tools/alignment/results/eternaut-redsync-v0.2.2.json) recovered scale
1 and offset 0 for both stereo and surround, with 23 reported anchors and zero
reported residual. Error at the four ground-truth timestamps was zero. Analysis
took 8.43 and 8.78 seconds, with largest-process RSS of 60.9 and 59.8 MiB.
This establishes a successful cross-dub control from one release. Independent
release alignment, listening/picture review and copying through Trackstarr remain
untested. The synthetic failures above still prevent backend selection.

The local Dark library probe found only German-tagged stereo and surround tracks
across all 26 episodes. Removing German therefore produces a video-only target,
which requires visual alignment. S01E01 was prepared this way in temporary storage.
The source retained German AAC stereo and E-AC-3 5.1. The original's SHA-256 was
unchanged. RedSync returned `reference: no audio stream`, so this fixture cannot
be evaluated by its audio matcher. Trackstarr's copy-back path is still pending.

### FFmpeg video-only control

A 30-second sample starting at 600 seconds was extracted from each Dark fixture,
scaled to 320 pixels wide and encoded as FFV1 without audio. Additional source
variants applied a 1.25-second start trim, a 25/24 playback speed and reversed
picture. Parent hashes were verified before and after extraction, and sample
hashes before and after analysis. The library original was not accessed.

[Host results](../tools/alignment/results/dark-ffmpeg-signature-host.json) and
[Alpine results](../tools/alignment/results/dark-ffmpeg-signature-alpine.json) agree.
Identity and trim exited zero without a matching report. The speed variant returned
one pair with 45.125 ms ground-truth error, without recovering a scale. Reversed
picture returned a 148-frame match and `whole video matching`. These local matches
do not establish a safe timeline. This is a short control from one release, not an
independent-release or whole-episode accuracy result.

## Remaining phase 1 work

The [bounded comparison](visual-alignment-comparison.md) is recorded. Neither
candidate qualifies. A temporal-context follow-up improves timing residuals on
valid Dark controls, but they still fail distributed-evidence requirements. The
[continuation checkpoint](track-sourcing-design.md#continuation-checkpoint) sets
the next matcher experiment. Independent-release controls and crop remain open.
Native `signature` output has not passed selection. Adopting an in-house component
still depends on measured accuracy and maintenance cost.
Establish per-anchor evidence, cancellation and failure contracts for any candidate.
Complete the transitive licence inventory and Alpine amd64/arm64 packaging checks,
including incremental image size. Measure peak workspace, simultaneous process-tree
memory and HDD/SSD reads. Current counters cover largest-process RSS and cached
filesystem block I/O only.

Independent releases, listening/picture review and encoder/layout preservation
tests remain required. Automatic discovery
for a single arr connection also needs authoritative ownership of both files.
A temporary sibling file is not automatically an arr-owned variant.
