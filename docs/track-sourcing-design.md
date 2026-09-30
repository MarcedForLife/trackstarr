# Audio sourcing between variants

Proposed design for [issue #7](https://github.com/MarcedForLife/trackstarr/issues/7).
Reviewed against repository revision `69fbc85`. This is an implementation plan, not
a description of shipped behaviour. The responsibility boundary is agreed. The
configuration names, contracts and release scope below are proposals.

## Purpose and boundary

Trackstarr currently processes each variant independently. A preferred video
release may lack a requested dub even when another connected library contains it.
This feature adds audio from those available variants and aligns it to the target.

Source and target are roles within one copy operation. A variant can supply a
French track to another variant and receive an English track from it. Connections
and files have no permanent source or target classification.

Trackstarr owns matching, track selection, alignment, planning and validated
publication. The surrounding pipeline owns acquisition, upgrades, source retention
and deletion. Trackstarr does not request downloads, manage arr instances or retain
audio or visual samples for future upgrades. An upgraded target with no source
remains visibly incomplete until another source becomes available.

Temporary analysis samples and prepared audio live only in a job workspace and
are removed on completion, cancellation or orphan recovery. Persisted state
contains metadata, alignment measurements and processing history only. Deleting
that state never deletes library media.

Reaching an arr upgrade ceiling may inform an external source retention policy.
Trackstarr neither interprets that ceiling nor declares a source safe to delete.
A successful copy reports what happened to one target revision.

## Initial release

The first usable release includes automatic discovery of missing audio languages,
measured alignment, manual source selection and timing adjustment, and reassessment
after imports or replacements. Constant offsets and constant speed differences
are required. Automatic repair of different cuts or discontinuous timelines is
deferred unless the evaluation demonstrates reliable support within the same
contracts. Such files must produce a review outcome, not a speculative copy.

Automatic sourcing adds one suitable main audio source per missing language.
Existing layout rules may retain it or derive the requested downmixes. It does not
upgrade an already present dub based on codec, bitrate or channel count. Subtitle
sourcing, cross-title copying, assembling several episode files and persistent
manual overrides across future releases are outside this release.

MKV is the initial sourcing output. An MP4/M4V target requires the existing remux
rule to permit conversion. Sources may use any container the selected tools can
read. The target's video, chapters, attachments and existing tracks remain subject
to its ordinary rules. Sourcing itself never substitutes source video or chapters.

## Existing foundations and gaps

| Area | Current implementation | Required change |
| --- | --- | --- |
| Library | Titles merge by provider identity. Episode variants are grouped by filename for display. | Authoritative file and episode identities for processing. |
| Planning | `Plan` and `OutStream` describe one input. Rules are pure after probing. | Explicit input references and an independent sourcing assessment. |
| Execution | One FFmpeg input, staged validation and replacement. | Prepared source tracks as further inputs, with source revalidation. |
| Cache | A verdict depends on target stat information and policy. | Record the source inventory fingerprint and alignment records in the target's verdict. |
| Scheduling | Discovery and work share an ordered queue. Imports deduplicate by path. | Analysis handoff, affected-target admission and a rerun for an import that arrives during active work. |
| Concurrency | Scheduler and observation fences coordinate one process. Flock slots limit rewrites across processes. Tag edits refuse a file this process is rewriting. | Source reads join that tag-edit guard. No new cross-process lock. |
| Status | No required rewrite can be reported as conforming. | Requested-track completeness independent of rewrite outcome. |

Starting points are [the planner](../src/trackstarr/planner.py),
[processing orchestration](../src/trackstarr/processing.py),
[library projection](../src/trackstarr/library.py) and
[the shared scheduler](../src/trackstarr/work.py).

## Architecture

![Proposed sourcing architecture](diagrams/audio-sourcing-architecture.png)

The implementation keeps decision logic pure and puts filesystem, arr and process
operations at explicit boundaries. Immutable dataclasses carry observations and
decisions between components. A small `Protocol` defines the alignment adapter.
There is one production adapter initially, with no plugin registry or engine
inheritance hierarchy.

| Component | Responsibility | Must not own |
| --- | --- | --- |
| Media catalogue | Resolve content identity and produce immutable file inventories. | UI rendering, rule decisions or rewriting. |
| Sourcing policy | Determine missing languages, eligible candidates and deterministic preference. | Probing, subprocesses or scheduling. |
| Alignment adapter | Measure relationships between two file timelines and report evidence. | Track rules or publication. |
| Processing coordinator | Collect facts, schedule analysis and compose a complete plan. | Tool-specific matching algorithms. |
| Planner | Turn target streams and accepted source tracks into output operations. | Library discovery or external API calls. |
| Executor | Prepare audio, render, validate and publish one target. | Source selection or acquisition. |
| Library/API projection | Present cached assessments, plans and history. | Expensive analysis during a GET request. |

Proposed modules are `catalogue.py`, `sourcing.py` and `alignment.py`, alongside
extensions to the existing planner, command renderer, executor and coordinator. The backend adapter belongs beside
`alignment.py` when it contains enough implementation to justify a separate file.
Extract catalogue identity code from library presentation rather than importing
`library.py` into the planner. Existing local-only calls retain their behaviour.

## Configuration and policy

Each arr connection gains an ordered `AUDIO_SOURCES` list of connections whose
variants may supply missing tracks. Entries must have the same arr type. The list
may include the connection itself when it contains several matching variants.
Reciprocal lists are valid. UI connection order does not choose a target or imply
copy permission. Every writable variant with sourcing enabled is evaluated as a
target independently.

An independent `PROCESS_MEDIA` setting defaults to true and controls mutation of
that connection's files. False permits indexing, probing and use as a source, but
prevents rewrites and retagging through every entry point. This supports libraries
mounted read-only without assigning them a special sourcing role. A permission
change is checked again at execution and publication.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RULE_SOURCE_AUDIO` | `never` | Existing `never`, `alongside`, `always` rule semantics. |
| `SOURCE_AUDIO_LANGUAGES` | Empty | Languages explicitly required through sourcing. Supports `original`. |
| `<ARR>_PROCESS_MEDIA` | `true` | Whether Trackstarr may modify this connection's files. |
| `<ARR>_AUDIO_SOURCES` | Empty | Ordered source connection IDs for this target connection. |

`<ARR>` follows existing instance names, for example `RADARR_4K`.
Settings use the existing file, environment override and vocabulary mechanisms.
Sourced languages must be included in the target's retained languages after
resolution. Statically conflicting settings are rejected when saved. At assessment
time, a candidate is rejected if layout policy would leave no retained output in
the requested language.
An unresolved `original` produces an explicit unknown requirement.

Enabling sourcing does not turn every existing `LANGUAGES` entry into a requirement.
With `never`, there is no automatic sourcing obligation. Manual copying remains
available subject to target rules and execution permissions. With `alongside`,
missing languages are reported, but analysis and copying occur automatically only
when an independent rule already requires a rewrite. Imported tracks and their
derived downmixes cannot bootstrap an `alongside` rewrite.

## Identity and inventory

`ContentKey` is independent of paths and connection-local IDs. A movie uses a
namespaced TMDB identity. An episode uses a namespaced TVDB series identity and
authoritative episode identity or a consistent numbering mapping from Sonarr.
Numeric provider IDs always carry the provider and media kind.

Arr file endpoints supply file ownership and episode membership. Connection-local
movie, series and episode IDs remain API locators only. Where episode identities
cannot be reconciled confidently across instances, automatic sourcing waits for
review. Filename matching remains a presentation fallback, not write authority.
Multi-episode files are initially excluded from automatic sourcing. Known edition
conflicts are rejected before analysis. Shared title identity alone does not prove
matching cuts.

An inventory records files, owning connections, content keys, probe summaries and
availability. Missing files differ from an unavailable mount or failed arr listing.
An unavailable source instance never produces a claim that the language does not
exist. Another readable, eligible source may still satisfy the requirement.

Shared roots with ambiguous ownership, target/source path aliases and files sharing
an inode are excluded from automatic copying until the conflict is resolved.
Mutation permission is checked by filesystem ownership resolution, not just by the
connection ID provided by a caller. Reading a hardlinked source is allowed.
Existing hardlink protection continues to govern writes to targets.

## Domain contracts

| Value | Essential fields and invariants |
| --- | --- |
| `FileRevision` | Canonical path, device/inode, size, modification and change timestamps, plus probe digest when available. Snapshot identity, not a content hash. |
| `MediaFile` | Content key, owning connection and local file ID, revision and immutable track facts. |
| `TrackRef` | File revision plus stream index and track identity facts. An index alone is never durable. |
| `AudioRequirement` | Resolved language, main-audio role and applicable target policy. |
| `Candidate` | Requirement, source track, preference and eligibility reasons. |
| `AlignmentResult` | Both revisions, backend/version/settings digest, mapping, evidence, supported operations and structured result reason. |
| `SourcingAssessment` | Per-requirement resolution, inventory fingerprint and selected or rejected candidates. |
| `PlanInput` | Target revision as input zero. Each further input is one prepared source track with its `TrackRef`, mapping and preparation operation. |
| `OutStream` | Input and stream reference, copy/encode operation and output metadata. Timing belongs to preparation. |

`AlignmentResult` separates accepted, review-required, unsupported and technical
failure results. Absence of a source is decided before calling the adapter.
The adapter's confidence number is evidence, not permission to publish.
Trackstarr applies acceptance policy to coverage, consistency and residual error.

The existing `OutStream.src` can remain a compatibility accessor during migration,
but input zero must not be implicitly assumed in metadata mapping, change counts,
generated-track detection or history. Input references are serialised explicitly
in the API. Frozen records hold shared inputs, while each planner pass constructs
its own output collection.

## Candidate selection

Requirements are evaluated against the tracks that would survive target policy,
not just the raw probe. A commentary or described-audio track does not satisfy a
main-dub requirement, even when it is retained. Unknown language tags do not
satisfy a requested language and are not guessed from an instance name.

Candidates must match content identity, language and main-audio role. The planner
also checks that the resulting outputs can satisfy the requirement under layout
and container rules. Known incompatible editions are excluded. Source preference
follows configured connection order, then existing audio source preferences where
applicable. Codec and channel count do not resolve ambiguity between distinct dubs.
Several plausible recordings with no explicit preference require selection.

Probe eligible candidates cheaply first, then analyse them in preference order.
A rejected candidate may fall through to the next one. An unresolved technical
failure is recorded separately from a content mismatch. Stop when an accepted
candidate satisfies the requirement. Several selected tracks from one source share
the same video alignment analysis, with per-track timestamp offsets preserved.

Selection only considers tracks already present in a stable observed file. It
never recursively asks another variant to acquire a missing track first. Reciprocal
copy permissions therefore create no job dependencies. A source can be rewritten
by its own independent job, since preparation revalidates what it read. Imported-track
provenance remains visible, and native tracks are preferred when candidates are
otherwise equivalent. Presence-based requirements prevent tracks circulating
indefinitely between variants.

The first release uses Trackstarr's existing language normalisation. Distinct
regional dubs that collapse to the same language must be exposed as ambiguous
choices, not silently treated as equivalent recordings. Full regional-language
requirements need a separate extension of language policy and metadata handling.

## Alignment and backend evaluation

Equal duration or frame rate does not authorise copying. Frame rate is a hint for
candidate transforms, and measured correspondence must confirm any speed change.
Audio correlation across different dubs may be unreliable, so visual matching is
the preferred starting point. The [evaluation report](alignment-evaluation.md)
records the candidates, their pinned revisions and results.

A backend must meet accuracy, footprint, interface and licensing requirements
together. The application has one Python runtime dependency, so a large scientific
stack or a maintained fork disqualifies a candidate unless that constraint is
reconsidered. The backend exposes analysis and structured evidence without its own
workflow, downloader or muxing, supports cancellation and machine-readable output,
and packages for Alpine without runtime downloads. A subprocess is preferred where
it isolates dependencies and failures without complicating the adapter.

The licence must keep Trackstarr [MIT-licensed](../LICENSE) and its published
images redistributable, which excludes proprietary, source-available-only and
non-commercial engines. Running an engine as a subprocess does not by itself settle
licence compatibility. Phase 1 records the terms of the engine, its transitive
dependencies and bundled binaries for the actual packaging method.

FFmpeg's existing visual matching is evaluated before adding another application.
A Trackstarr-owned matcher over FFmpeg fingerprints needs a recorded adoption
decision weighing accuracy against maintenance cost. If no candidate meets every
requirement, phase 1 reports that outcome rather than relaxing the requirements.

The adapter implements `analyse(target, source, settings, workspace, cancel)` and
returns an `AlignmentResult`. It measures only, and preparation applies the mapping.
An upstream tool works only inside the job workspace and never receives the live
target as an output path. If extracting an analysis interface requires maintained
upstream changes, phase 1 records that cost before selecting it.

### Timing model

The first mapping is `target_time = scale * source_time + offset`, with rational
scale and integer time units. Both times refer to a documented container timeline,
including stream start times. A positive offset places source content later in the
target. Scale greater than one lengthens source playback. FFmpeg tempo would be
`1 / scale`, not `scale`.

Acceptance requires distinct matching regions spread through the programme,
including its beginning and end, monotonic ordering, sufficient coverage and
bounded residual error. Repeated titles, black frames and low-information scenes
do not independently establish a match. Validation uses held-out regions rather
than only the anchors that fitted the mapping.

An internal policy defines tolerances in milliseconds, coverage and supported
scale range. Their shipped values and rationale are outputs of the evaluation
corpus. A similarity score alone is never labelled a probability. Until that gate
passes, automatic sourcing remains disabled.

Preparation applies the mapping to each selected source track inside the job
workspace. An offset that container timestamps can represent keeps the encoded
audio. A speed change or sample-accurate trim decodes to a lossless intermediate,
which the render encodes once with the encoder and bitrate `AUDIO_LAYOUTS` sets
for its layout. Silence padding is permitted only for verified edge alignment,
never to invent missing programme audio. A layout with no encoder, or immersive
audio that retiming would flatten, produces a review result. The plan shows copy,
trim, padding and encoding for every sourced track.

Discontinuities produce `needs_review` in the initial adapter contract. A future
piecewise mapping must identify every segment and unmatched interval explicitly.
It cannot silently bridge a missing scene.

### Evaluation gate

Use generated fixtures with known timing and a small authorised real-media corpus.
Include same and different dubs, logos, credit differences, genuine speed changes,
frame-rate metadata differences without speed changes, alternate cuts, cropping,
dark/repeated scenes and multi-channel audio. Record false acceptance, refusal,
timing error at independent points, runtime, disk reads, peak workspace size and
memory on HDD and SSD where available.

Every supported synthetic transform must recover the expected mapping within the
declared tolerance. Known wrong-cut and ambiguous examples must never be accepted
automatically. A real-media listening and picture review must confirm the accepted
examples. Corpus success does not establish universal correctness. Publish the
supported envelope and measured limitations with the chosen adapter.

## Planning and rendering

Processing first probes the target and builds a local assessment. Missing language
requirements then drive candidate discovery and, when appropriate, analysis.
Accepted source tracks enter the pure planner as facts before audio layout
selection. Local cleanup, sourcing and downmixing compose into one target rewrite.

Probe facts remain available when mutation is disabled or a hardlink prevents
rewriting. Move write eligibility checks out of fact collection where necessary,
so a protected file can still be assessed or used as a source.

Sourcing is one more rule in the planner's deciding and alongside passes. Both
passes receive identical immutable observations and run no subprocesses. With
`always`, analysis precedes planning. With `alongside`, it follows a local plan
that has already decided to rewrite.

The plan names every sourced track with its mapping and preparation, and lists
unmet requirements that do not block independent cleanup. The renderer takes the
target as input zero and each prepared track as a further input. Target chapters
and container metadata keep input-zero ownership. Default-track policy applies
explicitly, so copied default flags cannot create several defaults. Generated-track
tags continue identifying Trackstarr downmixes, and sourced-track provenance is
recorded separately.

## Completeness and execution status

Keep the existing execution `Status` vocabulary. Add a separate sourcing assessment
to plans and verdicts. A target may have local cleanup pending while also waiting
for a source. Sourcing failure must not replace all other file information.

| Requirement state | Meaning | Automatic work |
| --- | --- | --- |
| `satisfied` | A suitable track exists in the current target. | None. |
| `missing_source` | Complete inventory found no eligible source. | Wait for inventory or policy change. |
| `unavailable` | Required inventory or media cannot currently be read. | Bounded retry after recovery. |
| `analysis_required` | At least one candidate needs alignment measurement. | Queue analysis if execution mode permits. |
| `ready` | Accepted, current evidence supports a copy. | Queue the planned rewrite if permitted. |
| `needs_review` | Ambiguous source, alignment or unsupported transform. | Explicit selection or adjustment. |
| `failed` | An attempted analysis or preparation failed technically. | Bounded retry keyed to the attempt. |

Completeness is `complete`, `incomplete`, `unknown` or `not_requested`. It is
derived from requirement states. `ready` still means incomplete until publication
and re-probing confirm the track. Missing sources and review outcomes consume no
rewrite-failure attempts and never keep a run open indefinitely.

File and title projections must not label incomplete or unknown targets simply
as passed. The library can show “No cleanup needed · French audio missing”. A
partial success, such as French added while German has no source, records the
successful rewrite and preserves the German requirement.

## Scheduling and dependency invalidation

Use the existing scheduler and admission lifecycle. Cheap inventory/probe work
uses the probe lane. Alignment, preparation and rendering use the work lane and
machine-wide work slots. The run keeps its queue rank and cancellation semantics
through handoffs. Analysis must not monopolise probe workers or run while the
scheduler condition lock is held.

Import handlers only queue the delivery. Workers resolve identities, refresh
inventory and admit affected targets as import work. Currently a second import
for a path already in flight is dropped, which would lose a source that arrives
while its target is processed. Instead it schedules one rerun after the active
job, however many imports arrive meanwhile.

| Change | Reassessment |
| --- | --- |
| Target import or upgrade | Assess the new target revision against configured sources. |
| Source import, replacement, deletion or track edit | Reassess affected target content keys, including unchanged target files. |
| Rename | Refresh locators, revalidate revisions and invalidate path-bound analysis. |
| Processing permission, source list or language policy change | Invalidate affected assessments and queued plan permissions. |
| Connection recovery or root remount | Refresh unknown inventory and reconsider waiting targets. |
| Successful target rewrite | Reprobe the result, then close requirements from observed tracks. |

Jobs never wait on another job while holding a work slot. Queued imports do not
survive a restart today, and sourcing work matches that. Sweeps refresh source
inventories before evaluating target cache hits. Each target verdict records a
fingerprint of the source inventory and availability it was judged against, so
a missed webhook or a restart still invalidates an unchanged target. Library GET
requests remain cached reads. If durable imports become a requirement, they belong
to every import, persisted as parked jobs are, rather than to a sourcing-only store.

`REWRITE_MODE=report` permits bounded queued analysis when explicitly requested,
but forbids media publication. Ordinary report sweeps may stop at
`analysis_required` to avoid unexpectedly analysing an entire library.
Under `imports`, a source import may cause a related existing target to be rewritten
as import-triggered work. Sweep work reports changes unless mode is `all`. Explicit apply permission remains scoped to
the selected targets and is not inherited by other affected variants. Target
pauses block automatic analysis and publication.
Pauses retain their mutation meaning. A paused variant can still supply audio to
another target. `PROCESS_MEDIA=false` likewise prevents writes without preventing
reads. Removing a connection from `AUDIO_SOURCES` excludes it from automatic reads.

## Metadata persistence and recovery

The verdict format gains the sourcing assessment, the source inventory
fingerprint and bounded alignment records. A record holds numeric anchors, the
mapping and reasons, keyed by source revision, backend version and analysis
settings. It contains no audio, images or fingerprints that stand in for retained
media. Records live in the target's verdict, so a target change discards them with
the rest of its entry, and sourcing adds no separate state file or lock. Older
entries remain displayable but are stale for sourcing until reassessed.
Installations with sourcing disabled keep current cache behaviour, and probe facts
stay reusable when only source availability changes.

Publication remains authoritative even if later telemetry fails. On restart,
re-probing the target determines whether copying already completed. Provenance
tags assist diagnosis, but absence of a history entry never authorises a duplicate.

Attempt identity includes the target and source revisions, selected tracks, policy,
backend and mapping. Technical retries use bounded backoff. Source replacement,
backend changes or explicit reanalysis permit a new attempt. An unchanged rejected
mapping is reused as a review result rather than recomputed on every sweep.

## Source reads and publication

Only analysis and preparation read a source. The render reads the target and the
prepared tracks. Analysis and preparation register the source path with the
in-process guard that tag edits already check, so this process never edits a
header under an active read.
Rewrites publish by rename, so a concurrent rewrite of a source leaves an open read
intact. Afterwards, each read compares the source revision with the one it planned
against. A change, including an in-place edit by another process, discards the
result and replans. A missing source does the same, and a fresh local-only plan can
still proceed. No cross-process source lock is needed, and read-only source mounts
need no lock files.

The target keeps its existing rewrite slot, observation fence and pre-publication
checks. At execution entry, validate permissions, policy, pause state, the target
revision and alignment dependencies, and check them again before publication.
Workspaces are unique, space is checked before large writes, and cancellation
terminates subprocess groups before cleanup. Startup orphan cleanup covers analysis
and preparation workspaces.

Validation checks the target video properties and preservation operations,
chapters, expected stream identities/counts, language/disposition metadata, audio
channel layouts, stream start/end timing and decoding of imported audio samples.
The measured alignment evidence is checked independently of container duration.
No output is published merely because FFmpeg exited successfully.

Trackstarr publishes through its existing staged replacement path and refreshes
only the owning target arr and relevant media servers. Source inputs are not
modified by that copy operation. External arr processes do not coordinate with
Trackstarr. Stat revalidation
detects observed replacements but cannot provide an atomic compare-and-swap against
an external rename in the final publication window. Document this existing limit
and test observable upgrade races without claiming a filesystem guarantee the
pipeline does not provide.

## API, CLI and user experience

Connection settings expose processing permission and ordered source lists. Rule
settings show the sourced-language subset alongside retained-language preferences.
A library with processing disabled remains browsable, with processing and tag-edit
actions disabled and an explanation of its mutation permission.

File details show each requested language, its state and reason. Ready plans name
the source variant and track, offset, speed adjustment and encoding consequences.
The existing variant selector provides navigation, while a source picker presents
eligible tracks with language, role, layout and release context. Queue stages
include analysing alignment and preparing audio. Progress is indeterminate where
the backend supplies no defensible estimate.

Proposed admin commands are `POST /api/library/source-audio/analyse` and
`POST /api/library/source-audio/apply`. They return existing run identities with
HTTP 202. GET file/title projections expose assessment and analysis results.
Analyse accepts a target locator and optional candidate selection. Apply accepts
the reviewed assessment digest, exact track references and an optional manual
mapping. It accepts only tracks the catalogue resolves, never a shell command or
file path.

Manual adjustment supports a signed offset and an optional second anchor pair to
derive scale. The UI states which file plays earlier. It previews copy, trim,
padding and encoding operations before submission. Manual approval can replace a
low-confidence alignment decision, but cannot bypass path ownership, revision,
container, preservation or cancellation checks. Changed inputs return a stale
selection conflict. Approval expires with the exact input revisions and policy.
Manual copying cannot create a track that normal rules would immediately remove.

CLI `plan` and `fix` use the same coordinator for connected files. Planning remains
non-mutating and reports when analysis is required. Add an explicit analysis option
and source/offset/anchor arguments for manual use through the same application
commands as the API. The CLI also accepts an explicit source file, which pairs an
unmanaged file or serves an installation with a single arr connection. Otherwise
unmanaged file processing keeps its local behaviour. A connection's mutation
permission overrides a CLI write request. Source and target labels appear within
the copy operation only.

## Integration output

Extend existing authenticated file/title APIs and history with stable reason codes,
content identity, assessment digest, input revisions and missing languages.
Events include `audio_sourcing_changed` and successful sourcing details on the
existing rewrite event. Emit assessment changes on transitions, not every sweep.
Persist the current result before sending the existing UI invalidation signal.

External automation can poll current state or consume history. Events may be
duplicated or missed, so consumers reconcile against current assessments and
their digests. An event cannot promise future track availability or deletion safety.
No new outbound webhook delivery system or Maintainerr-specific integration is
required for this feature.

## Implementation sequence

Phase 1 is underway. The [evaluation report](alignment-evaluation.md),
[visual comparison](visual-alignment-comparison.md) and
[research harness](../tools/alignment/README.md) record the corpus, candidates and
results. No backend is selected, and no production code or container dependency
has changed.

### Continuation checkpoint

Work is on `feature/audio-track-sourcing`. No candidate qualifies yet. The
fingerprint prototypes recover every valid synthetic mapping except crop, but send
each short Dark control to review for lack of distributed evidence. AVSync's
isolated core finds too few anchors and needs about 340 MiB of dependencies. The
Dark and Eternaut fixtures each derive from one release, and no independently
released pair is available yet.

Phases 2 and 3 need no alignment backend and can start now. Manual copy from an
explicit source file needs no second arr connection, so a single-connection
installation can test preparation, rendering and publication end to end.

The next matcher experiment replaces per-probe decisions with consensus. The
current prototypes discard any probe with a close alternative, so dark or
repetitive regions lack evidence even when their best matches agree. Instead, each
plausible scale lets every close match vote for an offset. Plausible scales are 1,
1001/1000, 25/24, 25025/24000 and their inverses, which cover common film speed
changes. Repeated and static content spreads its votes, while a true mapping
concentrates them. Held-out regions then check the winning mapping, each confirming
it, contradicting it or carrying too little picture detail to judge. Acceptance
needs no contradicting region and confirmed evidence near both ends and across a
minimum share of the programme. Indexing target frames by the per-frame words
FFmpeg already exports would let the search cover whole episodes instead of
120-second samples. Crop remains unresolved.

Temporary fixtures may disappear between sessions. The harness README has
reproduction steps. Library originals remain read-only inputs.

### Phase gates

Each phase leaves sourcing disabled by default. Backend selection and acceptance
limits gate automatic publication only. Phases 2 to 4 need no backend and proceed
while phase 1 is open.

| Phase | Work | Exit evidence |
| --- | --- | --- |
| 1. Research and evaluate alignment engines | Research suitable libraries and engines beyond the initial candidates. Compare dependency footprint, integration interfaces and licence compatibility, build the timing corpus, benchmark pinned candidates and recommend one adapter. | Written selection report with dependency/container size, a minimal adapter proof of concept, accuracy and resource results, real-media review, packaging evidence and a licence inventory with redistribution obligations. The selected engine meets the footprint, interface and MIT-compatible licensing requirements. |
| 2. Generalise execution | Add explicit input and track references, source preparation and revalidation, and source metadata mapping. Keep old plans working. | Existing suite passes, local-only output is unchanged and synthetic multi-input rendering preserves target content. |
| 3. Manual copy | CLI copy from an explicit source file with chosen tracks and a signed offset or anchor pair. | A real copy passes listening and picture review. Changed inputs are refused. |
| 4. Identity, permissions and assessments | Extract catalogue facts, add file/episode identity, processing permission, source lists, requirements, selection and dependency-aware verdicts. | Cross-instance identity tests, ambiguous episodes excluded, every mutation entry point respects write permissions, and missing/unavailable/review states cause no rewrite loops. |
| 5. Integrate analysis | Connect the chosen adapter through queued work and cancellation. Measured mappings appear as suggestions for review before automatic publication is enabled. | Deterministic accepted/refused cases and cancellation/restart tests pass. |
| 6. Integrate reconciliation | Source-triggered target work, coalesced reruns, inventory fingerprints in sweeps, and mode/pause handling. | Late source, missed event, active-work and upgrade scenarios converge correctly. |
| 7. Complete product surface | Settings, source picker, manual timing in the UI, history, integration fields and demo fixtures. | Shared contracts pass, stale selections are refused and review flows work through the UI. |
| 8. Release validation | Real-library report run, explicit copies, then automatic sourcing within the supported envelope. Document behaviour and limitations. | Acceptance matrix below passes with measured resource limits. |

Do not combine the catalogue extraction, stream-reference migration and automatic
activation into one unreviewable change. The stream-reference migration must
demonstrate unchanged local-only output before source support is switched on.

## Acceptance matrix

| Scenario | Required result |
| --- | --- |
| Sourcing disabled on an existing installation | Same plans and output as before, no extra arr inventory or alignment work. |
| Target already has the requested dub | No copy, even if a preferred source later appears. |
| Two variants each contain a language the other needs | Independent copy operations can complete in both directions without recursive jobs or repeated copying. |
| A paused or read-only variant contains needed audio | It remains available as a source, while writes to it stay blocked. |
| Target first, source later | Missing state becomes a queued copy without modifying the target to trigger discovery. |
| Source first, target later | Target import discovers and uses the source. |
| Equal durations with shifted internal content | No automatic approval based on duration. |
| Verified offset or speed difference | Correct mapping and output timing, with explicit encoding consequences. |
| Different cuts or unreliable correspondence | Review state, target preserved. |
| Several plausible recordings | Explicit selection required unless configured preference resolves the choice. |
| One language available, another missing | Available dub added, remaining requirement stays visible. |
| Source absent after target upgrade | Missing-source state, no download request and no retained-media fallback. |
| Source deleted after a successful copy | Existing target remains satisfied. |
| Source mount or arr unavailable | Unknown/unavailable assessment, not a false empty inventory. |
| Source removed, replaced or retagged during analysis or preparation | Result discarded and the target replanned, no partially adjusted target. |
| Target upgraded while work runs | Detected replacement prevents publication over it. Final external rename race is documented. |
| Same stream indexes in two inputs | Correct source streams and metadata appear in output. |
| Hardlinked source and protected target | Source readable, target write deferred according to existing policy. |
| Tag edit requested on a source being read | Refused in this process. Revalidation catches an edit from another process. |
| Source import while its target is processed | One rerun reassesses the target afterwards. |
| Missed webhook or restart | Inventory reconciliation repairs stale assessments within the allowed rewrite mode. |
| Cancellation, tool crash, disk full or malformed report | Target survives, temporary media is cleaned, failure is bounded and visible. |
| Restart after publication but before history recording | Reprobe observes the copied track, no duplicate insertion. |
| `alongside`, report mode or pause | Sourcing obeys the documented trigger and publication gates. |
| Repeated sweeps after success or rejection | No duplicate tracks, repeated encoding or endless analysis. |
| Old verdict/state formats | Safe migration or reassessment, no guessed execution authority. |

Unit tests cover pure selection, timing arithmetic, policy passes and dependency
fingerprints. Integration tests generate small media with known timestamps and
verify actual FFmpeg output. Concurrency tests coordinate processes with barriers
and explicit events rather than timing sleeps. Contract tests cover backend/API/
frontend fixtures and the static demo. Manual real-media review complements these
tests because synthetic timing fixtures cannot establish cross-dub matching quality.

Required repository checks remain `uv run pytest --cov`, Ruff check/format and
mypy, plus web check, tests, lint, production build and demo build. CI/container
checks must include the pinned adapter and target architecture support. Record
backend failures and exclusions as supported-envelope limits rather than weakening
acceptance checks to make the corpus pass.

## Decisions to close during phase 1

The backend, numerical acceptance thresholds and supported platform builds require
measured evidence. The proposed per-connection mutation permission, separate
required-language list, initial offset/linear scope and encoding retimed tracks
with the layout's configured encoder can be reviewed directly from this plan. None
of those choices changes the agreed ownership of acquisition, source deletion or
media retention.
