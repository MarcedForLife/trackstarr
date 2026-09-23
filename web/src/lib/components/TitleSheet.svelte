<script module lang="ts">
	import type { RunMode } from '$lib/library';
	import type { Run } from '$lib/runs';

	/** Running one title from inside its sheet. The page owns the run and the
	 * floating bar that shows it; a page with nowhere to run passes no runner. */
	export type Runner = {
		// False where REWRITE_MODE latches to report only. Process still shows and
		// explains when pressed.
		mayRewrite: boolean;
		// Why a run cannot start now, or empty.
		refuses: string;
		// Which press is under way, so only that one says so.
		busy: '' | RunMode;
		starting: boolean;
		// The service's words when it refused the last press.
		error: string;
		// The run this sheet started, while it is going. The pair stays dead until
		// it ends, rather than offering a second run over the first.
		run: Run | null;
		onrefresh: () => void;
		onrun: (id: string, mode: RunMode) => void;
		onfile?: (path: string, mode: RunMode) => Promise<void>;
	};
</script>

<script lang="ts">
	import Spinner from '$lib/components/Spinner.svelte';
	import { onDestroy } from 'svelte';
	import PathDetails from './PathDetails.svelte';
	import FileVariants from './FileVariants.svelte';
	import { fileGroups } from '$lib/variants';
	import TitleFile from './TitleFile.svelte';
	import { type Editing } from './FileAccount.svelte';
	import { atFront } from '$lib/queue';
	import { since, ticking } from '$lib/clock.svelte';
	import Count from '$lib/components/Count.svelte';
	import PauseMenu from '$lib/components/PauseMenu.svelte';
	import QueuePlace from '$lib/components/QueuePlace.svelte';
	import { page } from '$app/state';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import FileProgress from '$lib/components/FileProgress.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import PosterArt from '$lib/components/PosterArt.svelte';
	import Sheet, { SLIDE } from '$lib/components/Sheet.svelte';
	import { MARKS, type MarkName } from '$lib/connections';
	import { button, control, radius, subtle } from '$lib/controls';
	import { display } from '$lib/display.svelte';
	import { tiltField } from '$lib/field';
	import { named, titled } from '$lib/format';
	import { forTitle, pausedFor } from '$lib/pauses';
	import {
		coverUrl,
		initials,
		kindName,
		verdictLabel,
		verdictText,
		type Card,
		type LibraryFile,
		type Listed,
		type TitleServer
	} from '$lib/library';
	import { fade } from 'svelte/transition';
	import { moving, rowFade, swapLeave } from '$lib/motion.svelte';
	import { overlay } from '$lib/overlay';
	import { summarise, type Outcome, type Summary } from '$lib/retag';
	import { whenNear } from '$lib/reveal';
	import { type Season } from '$lib/seasons';
	import { TitleBrowsing, FILE_PAGE, type Opening } from '$lib/title-browsing.svelte';
	import { TitleWorkController } from '$lib/title-work.svelte';
	import { getSettings } from '$lib/settings';

	// One title, with what each file is and what a rewrite would leave. Owns
	// presentation and the two-phase close, coordinating browsing and work when
	// a second poster is tapped. A caller keeps a `bind:this` and calls open().

	// `onshut` lets a caller polling for the sheet stop.
	let { runner, onshut }: { runner?: Runner; onshut?: () => void } = $props();

	const browsing = new TitleBrowsing();
	const opened = $derived(browsing.opened);
	const detail = $derived(browsing.detail);
	const loading = $derived(browsing.loading);
	const failure = $derived(browsing.failure);
	const loadingMore = $derived(browsing.loadingMore);
	const moreError = $derived(browsing.moreError);
	const refreshError = $derived(browsing.refreshError);
	// Presentation outlives browsing ownership during the closing slide.
	let up = $state(false);
	let emptying: ReturnType<typeof setTimeout> | null = null;
	// Remount menus on every opening; late actions must not update a new session.
	let sheetSession = $state(0);
	const actions = new TitleWorkController();

	const art = $derived(opened ? coverUrl(opened.id) : '');

	// The header's poster is one card in a field of its own, on the grid's
	// spread, so it leans as it did under the pointer there. Alone, a thumb on
	// the file list below must not move it: half a width past its box.
	const HERO_MARGIN = 0.5;

	// Where to watch this title. Null while the media servers are being asked, so
	// the buttons stand greyed in the row rather than landing under the thumb.
	const links = $derived(browsing.links);

	// Held in more than one instance: each file and folder then says whose it is.
	const sourced = $derived((detail?.folders.length ?? 0) > 1);

	// Whether this title is being left alone for now, and the choices for
	// putting it that way. Owned here rather than passed in: the sheet opens
	// from two pages and already fetches for itself.
	const admin = $derived(page.data.user?.role === 'admin');
	const pauses = $derived(actions.pauses);
	const pauseBusy = $derived(actions.pauseBusy);
	const pauseError = $derived(actions.pauseError);
	// The detail names the kind too, for a title opened without a card behind
	// it. Its verdict word beats the card's, which is as old as the tap.
	const kind = $derived(opened?.kind ?? detail?.kind ?? '');
	const verdict = $derived(detail?.state ?? opened?.state);
	const verdictSummary = $derived.by(() => {
		if (!verdict) return '';
		const total = detail?.total ?? opened?.files ?? 0;
		const counts = detail ? detail.counts : opened?.counts;
		const count = counts?.[verdict];
		if (total > 1 && count && (verdict === 'pending' || verdict === 'failed')) {
			return `${count.toLocaleString()} ${verdictLabel(verdict).toLowerCase()}`;
		}
		return verdictLabel(verdict);
	});
	const manyFiles = $derived(kind === 'series' || (detail?.total ?? opened?.files ?? 0) > 1);
	const pause = $derived(
		opened
			? (forTitle(pauses, opened.id, detail?.folders) ??
					(!manyFiles ? pauses.find((item) => item.path === detail?.files[0]?.path) : undefined))
			: undefined
	);

	// The track whose language and flags are open for editing: its file and its
	// stream index there. One at a time, inline under its row. Admins only,
	// since it writes to the file.
	let editing = $state<{ path: string; stream: number } | null>(null);
	// The row button that opened it, so closing hands focus back rather than
	// dropping it on the body: everything outside the sheet is inert, and the
	// next Tab would start over at Close.
	let opener: HTMLElement | null = null;
	// Escape closes it ahead of the sheet; no history entry, since a form inside
	// a sheet should not cost a back press.
	const editor = overlay({
		close: () => {
			editing = null;
			if (up && opener?.isConnected) opener.focus();
			opener = null;
		}
	});
	// The language names for its menu, fetched the first time one opens.
	let languages = $state<Record<string, string> | null>(null);
	// What the last edit came to, under the row it was made from, until the
	// next one or the sheet goes.
	let retagged = $state<({ path: string; stream: number } & Summary) | null>(null);

	const files = $derived(browsing.reading.renderedGroups);
	const grouped = $derived(browsing.grouped);
	const groups = $derived(browsing.groups);
	const rows = $derived(grouped ? [] : groups.slice(0, files));
	const more = () => browsing.more();
	const loadMore = () => browsing.loadMore();
	const reload = () => browsing.reload();
	const isOpen = (season: Season, at: number) => browsing.isOpen(season, at);
	const fold = (season: Season, at: number) => browsing.fold(season, at);

	// The history entry the sheet stands on. One entry however many posters are
	// tapped in a row.
	const held = overlay({ name: 'sheet', close: shut });
	const work = $derived(actions.work);
	const workError = $derived(actions.workError);
	const workBusy = $derived(actions.workBusy);
	const queued = $derived(work?.queued ?? []);
	const activeWork = $derived(work?.active ?? []);
	const involved = $derived(queued.length > 0 || activeWork.length > 0);
	// Whether the queue has answered for this title. The header and controls
	// wait on it.
	const known = $derived(!!work || !!workError);
	// A file asked to stop. Not its run winding up, which leaves the encode going.
	const stoppingWork = $derived(activeWork.some((item) => item.skipped));
	// When the reading arrived, so the bars glide on from it between polls.
	const workSeen = $derived(actions.workSeen);
	const workAge = $derived(since(workSeen));
	$effect(() => (activeWork.length ? ticking() : undefined));

	// The header names the title, so a bar needs only what tells one file from
	// its siblings.
	function worked(path: string): string {
		return manyFiles ? named(path).episode || titled(path) : '';
	}

	const processing = $derived(
		new Set(activeWork.filter((item) => item.stage !== 'waiting').map((item) => item.path)).size
	);
	const workerWaiting = $derived(
		new Set(activeWork.filter((item) => item.stage === 'waiting').map((item) => item.path)).size
	);
	const queueCount = $derived(new Set(queued.map((item) => item.path)).size);
	const queuePosition = $derived(
		queued.length ? Math.min(...queued.map((item) => item.position)) : 0
	);
	// Already at the head, where Prioritise would move nothing.
	const front = $derived(atFront(queued));
	// The nearest file's place for a series as for a film: how soon anything
	// here goes, which is what a reorder moves. A series adds how many files
	// are spread down the queue, with its noun, so the two are never read for
	// each other. Each number is a figure of its own, keyed, so a change rolls
	// rather than redraws the line. Brass for work under way; queued is a
	// state, and its place lights itself at the head.
	type Part = {
		key: string;
		before?: string;
		value?: number;
		place?: number;
		after?: string;
		tone: string;
	};
	const live = 'text-accent';
	const workStatus = $derived.by((): Part[] => {
		if (stoppingWork) return [{ key: 'stopping', before: 'Stopping…', tone: live }];
		const parts: Part[] = [];
		if (manyFiles) {
			if (processing)
				parts.push({ key: 'processing', value: processing, after: ' processing', tone: live });
			if (workerWaiting) {
				parts.push({
					key: 'worker',
					value: workerWaiting,
					after: ' waiting for a worker',
					tone: live
				});
			}
		} else if (processing) {
			return [{ key: 'processing', before: 'Processing', tone: live }];
		} else if (workerWaiting) {
			return [{ key: 'worker', before: 'Waiting for a worker', tone: live }];
		}
		if (queueCount) {
			parts.push({ key: 'place', before: 'Queued ', place: queuePosition, tone: 'text-dim' });
			if (manyFiles) {
				const noun = queueCount === 1 ? ' file' : ' files';
				parts.push({ key: 'count', value: queueCount, after: noun, tone: 'text-dim' });
			}
		}
		// The separators ride on the parts: a template drops a space at a
		// block's edge.
		return parts.map((part, i) => (i ? { ...part, before: ` · ${part.before ?? ''}` } : part));
	});

	// Settings or a newer version may have made the verdict stale. Not said of
	// a title a run already holds, which is being judged again as it goes.
	const staleVerdict = $derived(
		!!detail &&
			!detail.current &&
			!workStatus.length &&
			!!verdict &&
			verdict !== 'unchecked' &&
			verdict !== 'missing'
	);

	const queueAct = (action: 'top' | 'skip') => actions.queueAct(action, runner?.onrefresh);
	const keep = (seconds: number) => actions.keep(seconds);

	// The row's Skip or Cancel under way, held until the work and the sheet's
	// run are both gone. Keeps its label and Prioritise so the row holds still.
	let cancelling = $state<{ label: string; prioritise: boolean } | null>(null);
	$effect(() => {
		if (!involved && !runner?.run && !runner?.starting) cancelling = null;
	});
	async function cancel() {
		cancelling = {
			label: activeWork.length ? 'Cancelling…' : 'Skipping…',
			prioritise: showPrioritise
		};
		await queueAct('skip');
		if (pauseError) cancelling = null;
	}

	// Which buttons the control row holds.
	const rowState = $derived(!known ? 'reading' : involved || cancelling ? 'working' : 'idle');
	const showPrioritise = $derived(cancelling ? cancelling.prioritise : queued.length > 0 && !front);
	const release = () => actions.release(pause);

	onDestroy(() => {
		up = false;
		actions.dispose();
		browsing.dispose();
		if (emptying !== null) clearTimeout(emptying);
	});

	// Movies render one card with a variant selector, regardless of file count.
	// Reserving one placeholder per file makes the sheet open at full height,
	// then reverse its opening slide when those variants collapse into one row.
	const waiting = $derived(kind === 'movie' ? 1 : Math.min(opened?.files || 1, FILE_PAGE));

	export async function open(card: Opening) {
		sheetSession++;
		cancelling = null;
		// Another poster tapped while the last slides out: the sheet stays up.
		if (emptying !== null) {
			clearTimeout(emptying);
			emptying = null;
		}
		held.raise();
		up = true;
		const loaded = browsing.open(card);
		actions.open(browsing.session!);
		editor.lower();
		retagged = null;
		await loaded;
	}

	// Narrowing, not a cast: a name with no mark keeps its button and loses its
	// logo.
	function mark(name: string): MarkName | null {
		return MARKS.find((known) => known === name) ?? null;
	}

	// One link to open the title elsewhere, at its own width and never squeezed:
	// the row pans where they do not fit. The padding is the hover ground only,
	// so the row pulls it back off the left; see the block.
	const link = `${subtle} relative !px-2 flex-none`;

	// The services the header offers, whether they have been asked yet or not.
	const offered = $derived(links ?? detail?.servers ?? []);

	// Equal shares at every width, as the idle row has. Longer series labels wrap
	// within their share rather than taking it from the others.
	const cell = $derived(
		runner && rowState !== 'working'
			? 'w-full'
			: 'min-w-0 w-full !px-2 leading-tight !whitespace-normal sm:!px-3.5'
	);

	// Every close goes through the entry, or the next back would raise the sheet
	// again. The entry going is what shuts it; see $lib/overlay.
	export function close() {
		held.lower();
	}

	function shut() {
		up = false;
		actions.close();
		browsing.close();
		editor.lower();
		retagged = null;
		emptying = setTimeout(() => {
			emptying = null;
			browsing.clear();
		}, SLIDE);
		onshut?.();
	}

	/** The card behind the header, from a caller that has a fresher one than the
	 * tap did, or one where the tap had none. */
	export function fill(card: Card) {
		browsing.fill(card);
	}

	$effect(() => {
		if (editing && detail && !detail.files.some((file) => file.path === editing?.path))
			editor.lower();
	});

	// Whether the box has buttons, not just a line saying what stands: a reader
	// who cannot act still sees a pause.
	const acting = $derived(!!runner || admin);

	function edit(file: LibraryFile, row: Listed, pressed: HTMLElement) {
		const at = editing;
		if ((at?.path === file.path && at.stream === row.stream) || row.stream === null) {
			editor.lower();
			return;
		}
		editing = { path: file.path, stream: row.stream };
		opener = pressed;
		editor.raise();
		if (!languages) {
			// A menu without names is still a menu of codes.
			getSettings()
				.then((snapshot) => (languages = snapshot.languages))
				.catch(() => (languages = {}));
		}
	}

	// Something changed. The editor stays up on its own when nothing did.
	function completion() {
		const session = browsing.session;
		const target = editing;
		return (file: LibraryFile, row: Listed, outcomes: Outcome[]) => {
			if (
				!session?.current() ||
				!target ||
				editing !== target ||
				!detail?.files.some((item) => item.path === target.path) ||
				row.stream === null
			)
				return;
			retagged = { path: file.path, stream: row.stream, ...summarise(outcomes) };
			editor.lower();
			// The rows around it are a verdict out of date.
			void session.reload();
		};
	}

	// What went wrong, whichever button caused it. One line, since only one
	// press is ever in flight.
	const alarm = $derived(pauseError || runner?.error || '');

	// Null for a reader who cannot edit tags, which is what keeps every row shut.
	const tagEditing = $derived<Editing | null>(
		admin
			? {
					at: editing,
					told: retagged,
					languages,
					open: edit,
					done: completion(),
					cancel: () => editor.lower()
				}
			: null
	);
</script>

<Sheet open={up} onclose={close} label={opened?.name ?? 'Title'}>
	{#if opened}
		<div class="px-4 pb-[calc(1.5rem+env(safe-area-inset-bottom))]">
			<!-- Two columns, with the links below the text beside the poster. -->
			<div class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3">
				<!-- Stands the height of the name, the path and the links beside it, its
				     width following at a poster's 2:3, so the two columns end on one line.
				     On phones the width stays at 7rem, while the frame keeps stretching:
				     long names crop the art rather than widening it and squeezing the text.
				     The links pan rather than wrap, or a second row of them would make
				     this taller, which would make it wider, which would wrap a third.
				     Keyed on the cover, so a second title brings a fresh card rather
				     than a swap under a fade. -->
				<div
					use:tiltField={{
						active: moving,
						reach: () => display.reach,
						margin: HERO_MARGIN,
						strength: () => display.strength,
						lights: () => display.lights
					}}
					class="row-span-2 min-h-[7.5rem] w-28 flex-none self-stretch sm:aspect-[2/3] sm:min-h-34 sm:w-auto"
				>
					{#key art}
						<span class="block h-full w-full">
							<PosterArt
								src={art}
								mark={initials(opened.name)}
								box="h-full w-full"
								rounded="rounded-lg"
							/>
						</span>
					{/key}
				</div>
				<div class="min-w-0">
					<h2 class="text-[17px] font-semibold tracking-tight">{opened.name}</h2>
					<p class="mt-0.5 text-[12.5px] text-dim">
						{#if kind}
							{[
								opened.year,
								kindName(kind),
								detail?.lang ?? opened.lang,
								// Last, and named: the one thing here about the film rather than our
								// copy of it.
								opened.rating ? `IMDb ${opened.rating.toFixed(1)}` : ''
							]
								.filter(Boolean)
								.join(' · ')}
						{:else}{@render holding('w-40')}{/if}
					</p>
					<p class="mt-2 text-[13px] font-medium">
						{#if known && (workStatus.length || pause || verdict)}
							{#if workStatus.length}
								{#each workStatus as part (part.key)}<span class={part.tone}
										>{part.before}{#if part.place}<QueuePlace
												place={part.place}
												size={12}
											/>{:else if part.value !== undefined}<Count
												value={part.value}
											/>{/if}{part.after}</span
									>{/each}
							{:else}<span class={pause ? 'text-dim' : (verdictText[verdict!] ?? 'text-dim')}
									>{pause ? pausedFor(pause) : verdictSummary}</span
								>{/if}
						{:else}{@render holding('w-24')}{/if}
					</p>
					{#if staleVerdict}
						<p class="mt-1 text-[12px] text-dim">
							Settings or version changed. These details may be outdated.
						</p>
					{/if}
					<!-- Multiple locations share one row. A single path is already visible. -->
					<div class="mt-1.5 flex min-w-0 gap-1.5 overflow-x-auto">
						{#if !detail}{@render holding('w-52')}{:else if sourced}
							{#each detail.folders as held (held.folder)}
								<PathDetails path={held.folder} label={held.source} />
							{/each}
						{:else}
							<p class="font-mono text-[11px] leading-relaxed break-all text-faint select-text">
								{detail.folders[0]?.folder ?? detail.folder}
							</p>
						{/if}
					</div>
				</div>

				<!-- Where to watch it and where it is managed. A row under the path at
				     every width, beside the cover, rather than a column whose height the
				     cover had to answer to. Pulled left by the first mark's own padding, so the marks
				     start where the name and the path do. A server still being asked holds its place greyed, so a
				     late answer does not shove everything up under the thumb. The mark
				     leads on its dark tile, and the label drops to the name. "Open in"
				     stays for a screen reader. -->
				{#if offered.length}
					<div class="col-start-2 -ml-2 flex gap-2 overflow-x-auto">
						{#each offered as server (server.url ?? server.server)}
							{@const found = server.url ?? ''}
							{#if found}
								<!-- Another application on another host, so no resolve(). -->
								<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
								<a href={found} target="_blank" rel="noreferrer" class={link}>
									{@render serverFace(server)}
								</a>
							{:else}
								<span class={`${link} opacity-40`} aria-hidden="true">
									{@render serverFace(server)}
								</span>
							{/if}
						{/each}
					</div>
				{/if}
			</div>

			<!-- One sunken block, near the thumb, for everything here that does
			     something. Pause sits with the runs: it answers the same question, and
			     Process on a paused title rewrites nothing. A run takes the row over. -->
			{#if acting || involved}
				<div
					role="group"
					aria-label="Title controls"
					class="mt-4 rounded-xl border border-line bg-sunken p-3"
				>
					<!-- This title's work. The floating bar shows the run. -->
					{#if (known || !acting) && activeWork.length}
						<div class={`space-y-2.5 ${acting ? 'mb-3' : ''}`}>
							{#each activeWork as file (file.path)}
								<FileProgress {file} age={workAge} caption={worked(file.path)} />
							{/each}
						</div>
					{/if}
					{#if acting}
						<!-- Positioned for the leaving state. -->
						<div class="relative">
							{#key rowState}
								<div out:swapLeave in:fade={rowFade()}>
									{#if rowState === 'reading'}
										<!-- Placeholders at the idle row's size until the queue answers, which decides
										     the buttons. -->
										<div class="grid auto-cols-fr grid-flow-col gap-3" aria-hidden="true">
											{#each { length: runner ? 3 : 1 }, at (at)}
												<span class={`${control} ${radius} border border-line bg-raised`}></span>
											{/each}
										</div>
									{:else}
										<!-- One row of equal columns at every width. -->
										<div
											class={runner && rowState === 'idle'
												? 'grid grid-cols-3 items-center gap-3'
												: 'grid auto-cols-fr grid-flow-col items-center gap-2 sm:gap-3'}
										>
											{#if runner && rowState === 'idle'}
												<RunButtons
													columns
													mayRewrite={runner.mayRewrite}
													refuses={runner.refuses}
													disabled={runner.starting || !!runner.run || workBusy || !!workError}
													busy={runner.busy}
													onrun={(mode) => opened && runner.onrun(opened.id, mode)}
												/>
											{/if}
											{#if admin && rowState === 'working'}
												{#if showPrioritise}<button
														class={`${cell} ${button}`}
														disabled={workBusy || !!workError || stoppingWork || !!cancelling}
														onclick={() => queueAct('top')}
														>{manyFiles ? 'Prioritise all' : 'Prioritise'}</button
													>{/if}
												<button
													class={`${cell} ${button} text-danger`}
													disabled={workBusy || !!workError || stoppingWork || !!cancelling}
													aria-busy={!!cancelling}
													onclick={cancel}
												>
													<Spinner busy={!!cancelling} />
													{cancelling
														? cancelling.label
														: activeWork.length
															? manyFiles
																? 'Cancel all'
																: 'Cancel'
															: manyFiles
																? 'Skip all'
																: 'Skip'}
												</button>
											{/if}
											{#if admin}
												<div class="relative grid">
													{#if pause}
														<button
															onclick={release}
															disabled={!!pauseBusy || workBusy || !!workError}
															aria-busy={pauseBusy === 'resume'}
															class={`${cell} ${button}`}
															out:swapLeave
															in:fade={rowFade()}
														>
															<Spinner busy={pauseBusy === 'resume'} />
															{pauseBusy === 'resume' ? 'Resuming…' : 'Resume'}
														</button>
													{:else}
														<div class="grid" out:swapLeave in:fade={rowFade()}>
															{#key sheetSession}
																<PauseMenu
																	label={opened.name}
																	onchoose={keep}
																	disabled={!!pauseBusy || workBusy || !!workError}
																	hint={involved
																		? 'Stops this title’s current work. A later sweep can process it after the pause ends.'
																		: 'A later sweep can process this title after the pause ends.'}
																	class={`${cell} ${button}`}
																/>
															{/key}
														</div>
													{/if}
												</div>
											{/if}
										</div>
									{/if}
								</div>
							{/key}
						</div>
					{/if}

					{#if alarm}
						<p role="alert" class={`text-[12px] text-danger ${acting ? 'mt-2.5' : ''}`}>{alarm}</p>
					{/if}
				</div>
			{/if}

			{#if refreshError}
				<p role="status" class="mt-4 text-[12px] text-dim">
					{refreshError}
					<button type="button" class="min-h-8 underline" onclick={reload}
						>Retry verdict refresh</button
					>
				</p>
			{/if}

			{#if failure}
				<p role="alert" class="mt-5 text-sm text-danger">
					{failure}
					<button type="button" class="min-h-8 underline" onclick={() => opened && open(opened)}
						>Retry loading title</button
					>
				</p>
			{:else if loading || !detail}
				<!-- Blocks the size of the rows about to arrive, so the bottom-anchored
				     sheet rises once rather than jumping when they land. A film's card, a
			     name, a line and three tracks: short of a heavy one rather than past
			     it, since the sheet grows into its slide better than it falls back. -->
				<ul class="mt-5 flex flex-col gap-4" aria-hidden="true">
					{#each { length: waiting }, at (at)}
						<li class="h-56 rounded-xl border border-line bg-sunken"></li>
					{/each}
				</ul>
				<p class="sr-only">Reading the verdicts…</p>
			{:else if !detail.files.length}
				<p class="mt-6 text-sm text-dim">
					This title's files have not been checked yet, so there is nothing to compare.
				</p>
			{:else}
				{#if grouped}
					{#each grouped as season, at (season.label)}
						{@render seasonBlock(season, at)}
					{/each}
				{:else}
					<ul class="mt-5 flex flex-col gap-4">
						{#each rows as group (group.key)}
							<FileVariants
								selections={browsing.reading.selectedPaths}
								{group}
								disabled={workBusy}
								onchange={() => editor.lower()}
								children={card}
							/>
						{/each}
					</ul>
					{#if files < groups.length}
						<div use:whenNear={more} class="h-px"></div>
					{/if}
				{/if}

				{#if detail.total > detail.files.length}
					<p class="mt-4 text-[12px] text-faint">
						Showing {detail.files.length} of {detail.total} files.
					</p>
					<button
						type="button"
						class={`${button} mt-2`}
						disabled={loadingMore || workBusy}
						aria-busy={loadingMore}
						onclick={loadMore}
					>
						<Spinner busy={loadingMore} />
						{loadingMore ? 'Loading…' : 'Load more files'}
					</button>
					{#if moreError}<p role="alert" class="mt-2 text-[12px] text-danger">{moreError}</p>{/if}
				{/if}
			{/if}
		</div>
	{/if}
	{#if workError}<p role="alert" class="px-4 pb-3 text-[12px] text-danger">
			{workError}
			<button class="min-h-8 underline" onclick={() => actions.readWork()}>Retry</button>
		</p>{/if}
</Sheet>

<!-- One service's face, worn by the link and by the greyed stand-in a server
     still being asked holds its place with. -->
{#snippet serverFace(server: TitleServer)}
	{@const logo = mark(server.server)}
	{#if logo}<ServiceIcon name={logo} box={20} size={14} />{/if}
	<span class="sr-only">Open in </span>{server.label}
{/snippet}

<!-- A line the title's own read has still to fill in, held at its height so the
     header does not grow into it. A read that failed says so below instead. -->
{#snippet holding(width: string)}
	{#if !failure}
		<span class={`inline-block h-[0.85em] ${width} max-w-full rounded bg-line align-middle`}></span>
	{/if}
{/snippet}

<!-- One file of the title. The sheet keeps which row has its tags open, since
     Escape and focus are its to handle. -->
{#snippet card(file: LibraryFile, embedded: boolean)}
	{#key `${sheetSession}:${file.path}`}
		<TitleFile
			{embedded}
			{file}
			{manyFiles}
			{sourced}
			{admin}
			{work}
			{runner}
			unavailable={!!workError}
			busy={workBusy}
			onchanged={() => actions.readWork()}
			onaction={actions.fileAction}
			editing={tagEditing}
			siblings={detail?.files ?? []}
			series={kind === 'series'}
		/>
	{/key}
{/snippet}

<!-- One season, shut but for the latest. -->
{#snippet seasonBlock(season: Season, at: number)}
	<section class="mt-4 border-t border-line">
		<Disclosure
			id={`season-${at}`}
			open={isOpen(season, at)}
			ontoggle={() => fold(season, at)}
			class="group relative flex w-full items-center gap-3 overflow-hidden pt-3.5 pb-3 text-left"
			mark={12}
			panelClass="pb-1"
			press
		>
			{#snippet summary(chevron)}
				<span class="flex-none text-[14px] font-semibold tracking-tight">{season.label}</span>
				<span class="min-w-0 flex-1 truncate text-right text-[12px] text-dim">
					{season.files.length}
					{season.files.length === 1 ? 'file' : 'files'}
					<span aria-hidden="true">·</span>
					<span class={verdictText[season.state] ?? 'text-dim'}>{verdictLabel(season.state)}</span>
				</span>
				{@render chevron()}
			{/snippet}

			{#snippet panel()}
				{@const episodeGroups = fileGroups(season.files, kind)}
				<ul class="flex flex-col gap-4">
					{#each episodeGroups.slice(0, files) as group (group.key)}
						<FileVariants
							selections={browsing.reading.selectedPaths}
							{group}
							disabled={workBusy}
							onchange={() => editor.lower()}
							children={card}
						/>
					{/each}
				</ul>
				{#if files < episodeGroups.length}
					<div use:whenNear={more} class="h-px"></div>
				{/if}
			{/snippet}
		</Disclosure>
	</section>
{/snippet}
