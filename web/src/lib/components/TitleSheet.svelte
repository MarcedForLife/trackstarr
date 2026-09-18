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
	};
</script>

<script lang="ts">
	import { onDestroy } from 'svelte';
	import TitleFile from './TitleFile.svelte';
	import { type Editing } from './FileAccount.svelte';
	import { poll } from '$lib/poll';
	import { atFront, getTitleWork, queueAction, type TitleWork } from '$lib/queue';
	import { mark as now, since, ticking } from '$lib/clock.svelte';
	import Count from '$lib/components/Count.svelte';
	import PauseMenu from '$lib/components/PauseMenu.svelte';
	import QueuePlace from '$lib/components/QueuePlace.svelte';
	import { page } from '$app/state';
	import { SvelteSet } from 'svelte/reactivity';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import FileProgress from '$lib/components/FileProgress.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import PosterArt from '$lib/components/PosterArt.svelte';
	import Sheet, { SLIDE } from '$lib/components/Sheet.svelte';
	import { refusalText } from '$lib/api';
	import { MARKS, type MarkName } from '$lib/connections';
	import { button, control, radius, subtle } from '$lib/controls';
	import { display } from '$lib/display.svelte';
	import { tiltField } from '$lib/field';
	import { duration, named, titled } from '$lib/format';
	import {
		forTitle,
		getPauses,
		resume as resumePause,
		place as placePause,
		type Pause
	} from '$lib/pauses';
	import {
		coverUrl,
		getLinks,
		getTitle,
		initials,
		kindName,
		verdictLabel,
		verdictText,
		type Card,
		type LibraryFile,
		type Listed,
		type TitleDetail,
		type TitleLink,
		type TitleServer
	} from '$lib/library';
	import { moving } from '$lib/motion.svelte';
	import { overlay } from '$lib/overlay';
	import { summarise, type Outcome, type Summary } from '$lib/retag';
	import { whenNear } from '$lib/reveal';
	import { seasons, type Season } from '$lib/seasons';
	import { skipFile } from '$lib/runs';
	import { getSettings } from '$lib/settings';

	// One title, with what each file is and what a rewrite would leave. Owns
	// being open: the fetch, the two-phase close, and a second poster tapped
	// while the first slides out. A caller keeps a `bind:this` and calls open().

	// What the caller had when it opened the sheet. The library pages hand over
	// the grid's card; a run or queue row knows only which title its file belongs
	// to, and the rest arrives with the detail.
	type Opening = Pick<Card, 'id' | 'name'> & Partial<Card>;

	// `onshut` lets a caller polling for the sheet stop.
	let { runner, onshut }: { runner?: Runner; onshut?: () => void } = $props();

	// The open title, as much of it as the caller had.
	let opened = $state<Opening | null>(null);
	let detail = $state<TitleDetail | null>(null);
	// Whether the sheet is up, apart from what it shows: the panel slides down at
	// once and the title goes when it has finished.
	let up = $state(false);
	let emptying: ReturnType<typeof setTimeout> | null = null;
	let loading = $state(false);
	let failure = $state('');

	const art = $derived(opened ? coverUrl(opened.id) : '');

	// The header's poster is one card in a field of its own, so the poster that
	// was tapped arrives leaning and lit the way it left the grid: the grid's
	// own spread, so under the pointer it leans exactly as it did there, with
	// no neighbours. Alone, it should also answer only a pointer on it or just
	// past it, or a thumb on the file list below would move it: half a card
	// width beyond its box.
	const HERO_MARGIN = 0.5;

	// Where to watch this title. Null while the media servers are being asked, so
	// the buttons stand greyed in the row rather than landing under the thumb.
	let links = $state<TitleLink[] | null>(null);

	// Whether this title is being left alone for now, and the choices for
	// putting it that way. Owned here rather than passed in: the sheet opens
	// from two pages and already fetches for itself.
	const admin = $derived(page.data.user?.role === 'admin');
	let pauses = $state<Pause[]>([]);
	let pauseBusy = $state('');
	let pauseError = $state('');
	// The detail names the kind too, for a title opened without a card behind
	// it. Its verdict word beats the card's, which is as old as the tap.
	const kind = $derived(opened?.kind ?? detail?.kind ?? '');
	const verdict = $derived(detail?.state ?? opened?.state);
	const manyFiles = $derived(kind === 'series' || (detail?.total ?? opened?.files ?? 0) > 1);
	const pause = $derived(
		opened
			? (forTitle(pauses, opened.id) ??
					(!manyFiles ? pauses.find((item) => item.path === detail?.files[0]?.path) : undefined))
			: undefined
	);
	const left = $derived(pause?.seconds ? `${duration(pause.seconds)} left` : 'until resumed');

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

	// A long series is two hundred files with a track list each.
	const FILE_PAGE = 12;
	let files = $state(FILE_PAGE);

	// A series by season, latest first; the page cap then runs inside whichever
	// are open, since a shut season costs nothing.
	const grouped = $derived(detail ? seasons(detail.files) : null);
	const rows = $derived(grouped ? [] : (detail?.files.slice(0, files) ?? []));

	function more() {
		files = Math.min(files + FILE_PAGE, detail?.files.length ?? 0);
	}

	// Which seasons are open, or null while the latest one alone is.
	let unfolded = $state<SvelteSet<string> | null>(null);

	function isOpen(season: Season, at: number): boolean {
		return unfolded ? unfolded.has(season.label) : at === 0;
	}

	function fold(season: Season, at: number) {
		const open = unfolded ?? new SvelteSet(grouped?.length ? [grouped[0].label] : []);
		if (isOpen(season, at)) open.delete(season.label);
		else open.add(season.label);
		unfolded = open;
	}

	// The history entry the sheet stands on. One entry however many posters are
	// tapped in a row.
	const held = overlay({ name: 'sheet', close: shut });
	let work = $state<TitleWork>();
	let workError = $state('');
	let workBusy = $state(false);
	let workTicket = 0;
	const queued = $derived(work?.queued ?? []);
	const activeWork = $derived(work?.active ?? []);
	const involved = $derived(queued.length > 0 || activeWork.length > 0);
	// Whether the queue has answered for this title, one way or the other. What
	// the header and the controls say depends on it, so they hold until it has.
	const known = $derived(!!work || !!workError);
	// A file asked to stop. Not its run winding up, which leaves the encode going.
	const stoppingWork = $derived(activeWork.some((item) => item.skipped));
	// When the reading arrived, so the bars glide on from it between polls.
	let workSeen = $state(0);
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

	async function queueAct(action: 'top' | 'skip') {
		workBusy = true;
		pauseError = '';
		try {
			if (action === 'skip') await cancelWork();
			else await queueAction('top', queued);
		} catch (error) {
			pauseError = refusalText(error);
		} finally {
			workBusy = false;
			await readWork();
			runner?.onrefresh();
			workPoll.now();
		}
	}

	async function cancelWork() {
		const running = [...activeWork];
		if (queued.length) await queueAction('skip', queued);
		for (const item of running) await skipFile(item.run, item.path);
	}

	async function readWork() {
		const id = opened?.id;
		if (!up || !id || workBusy) return;
		const ticket = ++workTicket;
		try {
			const answer = await getTitleWork(id);
			// The queue letting go is the verdict changing. Read the new word
			// first, so the header goes from Processing to it rather than through
			// the one it had.
			if (involved && !answer.active.length && !answer.queued.length) await reload();
			if (up && !workBusy && opened?.id === id && ticket === workTicket) {
				work = answer;
				workSeen = now();
				pauses = answer.pauses;
				workError = '';
			}
		} catch {
			if (up && opened?.id === id && ticket === workTicket)
				workError = 'Could not refresh queue status.';
		}
	}
	const workPoll = poll({
		ask: readWork,
		ready: () => up && !workBusy,
		pace: () => (stoppingWork ? 1000 : 5000),
		gap: 1000,
		kinds: ['runs', 'progress']
	});
	onDestroy(workPoll.stop);

	// A sweep in another process re-judges files too. Only when told: the
	// verdicts change under nothing else.
	const detailPoll = poll({
		ask: reload,
		ready: () => up && !!detail && !loading,
		gap: 2000,
		kinds: ['library']
	});
	onDestroy(detailPoll.stop);

	// Placeholder rows while the verdicts load, so the sheet opens at the height
	// it is about to need.
	const waiting = $derived(Math.min(opened?.files || 1, FILE_PAGE));

	export async function open(card: Opening) {
		// Another poster tapped while the last slides out: the sheet stays up.
		if (emptying !== null) {
			clearTimeout(emptying);
			emptying = null;
		}
		held.raise();
		up = true;
		opened = card;
		work = undefined;
		workError = '';
		workTicket++;
		workPoll.now();
		// A change heard while shut is about the last title, or one open() reads.
		detailPoll.mark();
		detail = null;
		files = FILE_PAGE;
		unfolded = null;
		failure = '';
		links = null;
		pauses = [];
		editor.lower();
		retagged = null;
		pauseError = '';
		loading = true;
		// Alongside the verdicts: neither should wait on the other.
		find(card.id);
		lookUpPauses();
		try {
			const found = await getTitle(card.id);
			// A second tap while the first was under way.
			if (opened?.id === card.id) detail = found;
		} catch {
			failure = 'Could not read that title.';
		} finally {
			loading = false;
		}
	}

	// A media server that will not answer is a button not drawn.
	async function find(id: string) {
		let found: TitleLink[];
		try {
			found = await getLinks(id);
		} catch {
			found = [];
		}
		if (opened?.id === id) links = found;
	}

	// Quietly: a title that cannot be read for pauses still shows its files, and
	// the row is simply not offered.
	async function lookUpPauses() {
		try {
			pauses = await getPauses();
		} catch {
			pauses = [];
		}
	}

	async function keep(seconds: number) {
		if (!opened) return;
		pauseBusy = String(seconds);
		workBusy = true;
		pauseError = '';
		try {
			pauses = await placePause({ ids: [opened.id] }, seconds);
			await cancelWork();
		} finally {
			pauseBusy = '';
			workBusy = false;
			await readWork();
		}
	}

	async function release() {
		if (!opened) return;
		pauseBusy = 'resume';
		workBusy = true;
		pauseError = '';
		try {
			pauses = await resumePause(
				pause && !pause.title ? { paths: [pause.path] } : { ids: [opened.id] }
			);
		} catch (error) {
			pauseError = refusalText(error);
		} finally {
			pauseBusy = '';
			workBusy = false;
			await readWork();
		}
	}

	// Narrowing, not a cast: a name with no mark keeps its button and loses its
	// logo.
	function mark(name: string): MarkName | null {
		return MARKS.find((known) => known === name) ?? null;
	}

	// One link to open the title elsewhere, at its own width and never squeezed:
	// the row pans where they do not fit. The padding is the hover ground only,
	// so the row pulls it back off the left; see the block.
	const link = `${subtle} !px-2 flex-none`;

	// The services the header offers, whether they have been asked yet or not.
	const offered = $derived(links ?? detail?.servers ?? []);

	// Equal shares at every width, as the idle row has. Longer series labels wrap
	// within their share rather than taking it from the others.
	const cell = $derived(
		runner && !involved
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
		editor.lower();
		retagged = null;
		onshut?.();
		emptying = setTimeout(() => {
			emptying = null;
			opened = null;
			detail = null;
			links = null;
		}, SLIDE);
	}

	/** The card behind the header, from a caller that has a fresher one than the
	 * tap did, or one where the tap had none. */
	export function fill(card: Card) {
		if (opened?.id === card.id) opened = card;
	}

	// Re-read the title after something rewrote its verdicts. Silent, unlike
	// open(): skeletons would lose the reader's place.
	async function reload() {
		const showing = opened;
		if (!showing) return;
		try {
			const found = await getTitle(showing.id);
			if (opened?.id === showing.id) detail = found;
		} catch {
			// The rows on screen are a run out of date, which beats an error.
		}
	}

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
	function retaggedTo(file: LibraryFile, row: Listed, outcomes: Outcome[]) {
		if (row.stream === null) return;
		retagged = { path: file.path, stream: row.stream, ...summarise(outcomes) };
		editor.lower();
		// The rows around it are a verdict out of date.
		reload();
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
					done: retaggedTo,
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
				     this taller, which would make it wider, which would wrap a third. The
				     field's one card is the box inside; the cover fades in over its tile
				     as a grid card's does. Keyed on the cover, so a second title tapped
				     brings a fresh card rather than a picture swapped under a fade. -->
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
									>{pause ? 'Paused' : verdictLabel(verdict!)}</span
								>{/if}
						{:else}{@render holding('w-24')}{/if}
					</p>
					<p class="mt-0.5 font-mono text-[11px] break-all text-faint">
						{#if detail}{detail.folder}{:else}{@render holding('w-52')}{/if}
					</p>
				</div>

				<!-- Where to watch it and where it is managed. A row under the path at
				     every width, beside the cover, rather than a column whose height the
				     cover had to answer to. Pulled left by the first mark's own padding, so the marks
				     start where the name and the path do. A server still being asked holds its place greyed, so a
				     late answer does not shove everything up under the thumb. The mark
				     leads on its dark tile, and the label drops to the name; "Open in"
				     stays for a screen reader. -->
				{#if offered.length}
					<div class="col-start-2 -ml-2 flex gap-2 overflow-x-auto">
						{#each offered as server (server.server)}
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
			{#if acting || pause || involved}
				<div
					role="group"
					aria-label="Title controls"
					class="mt-4 rounded-xl border border-line bg-sunken p-3"
				>
					{#if acting && !known}
						<!-- Held at the row's height until the queue has answered, since which
						     buttons the row holds turns on it: the idle pair landing first and
						     giving way to Prioritise and Skip read as a flash. As many as the
						     idle row has, which is what most titles come to. -->
						<div class="grid auto-cols-fr grid-flow-col gap-3" aria-hidden="true">
							{#each { length: runner ? 3 : 1 }, at (at)}
								<span class={`${control} ${radius} border border-line bg-raised`}></span>
							{/each}
						</div>
					{:else}
						<!-- What each worker has. The page's floating bar carries the run
						     itself, so the panel keeps to this title at every stage. -->
						{#if activeWork.length}
							<div class={`space-y-2.5 ${acting ? 'mb-3' : ''}`}>
								{#each activeWork as file (file.path)}
									<FileProgress {file} age={workAge} caption={worked(file.path)} />
								{/each}
							</div>
						{/if}
						{#if acting}
							<!-- One row at every width; stacked, the panel pushed the rows below
							     the fold. Equal columns, since the buttons are one decision and
							     a narrower Pause read as the lesser of them. -->
							<div
								class={runner && !involved
									? 'grid grid-cols-3 items-center gap-3'
									: 'grid auto-cols-fr grid-flow-col items-center gap-2 sm:gap-3'}
							>
								{#if runner && !involved}
									<RunButtons
										columns
										mayRewrite={runner.mayRewrite}
										refuses={runner.refuses}
										disabled={runner.starting || !!runner.run || workBusy || !!workError}
										busy={runner.busy}
										onrun={(mode) => opened && runner.onrun(opened.id, mode)}
									/>
								{/if}
								{#if admin && involved}
									{#if queued.length && !front}<button
											class={`${cell} ${button}`}
											disabled={workBusy || !!workError || stoppingWork}
											onclick={() => queueAct('top')}
											>{manyFiles ? 'Prioritise all' : 'Prioritise'}</button
										>{/if}
									<button
										class={`${cell} ${button} text-danger`}
										disabled={workBusy || !!workError || stoppingWork}
										onclick={() => queueAct('skip')}
										>{activeWork.length
											? manyFiles
												? 'Cancel all'
												: 'Cancel'
											: manyFiles
												? 'Skip all'
												: 'Skip'}</button
									>
								{/if}
								{#if admin && pause}
									<button
										onclick={release}
										disabled={!!pauseBusy || workBusy || !!workError}
										class={`${cell} ${button}`}
									>
										{pauseBusy === 'resume' ? 'Resuming…' : 'Resume'}
									</button>
								{:else if admin}
									{#key opened.id}
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
								{/if}
							</div>
						{/if}
					{/if}

					<!-- What stands, and what came of the last press. A pause is a state,
					     so it shows alongside; the rest is one line, the loudest first. -->
					{#if pause || alarm}
						<div class={`flex flex-col gap-1 text-[12px] ${acting ? 'mt-2.5' : ''}`}>
							{#if pause}
								<!-- Not who placed it: on a library one household runs, the name is
								     always the reader's own. -->
								<p class="text-dim">{['Paused', left, pause.reason].filter(Boolean).join(' · ')}</p>
							{/if}
							{#if alarm}
								<p role="alert" class="text-danger">{alarm}</p>
							{/if}
						</div>
					{/if}
				</div>
			{/if}

			{#if failure}
				<p class="mt-5 text-sm text-danger">{failure}</p>
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
					No sweep has walked this title's files yet, so there is nothing to compare.
				</p>
			{:else}
				{#if detail.total > detail.files.length}
					<p class="mt-4 text-[12px] text-faint">
						Showing {detail.files.length} of {detail.total} files, Failed and Pending first.
					</p>
				{/if}

				{#if grouped}
					{#each grouped as season, at (season.label)}
						{@render seasonBlock(season, at)}
					{/each}
				{:else}
					<ul class="mt-5 flex flex-col gap-4">
						{#each rows as file (file.path)}
							{@render card(file)}
						{/each}
					</ul>
					{#if files < detail.files.length}
						<div use:whenNear={more} class="h-px"></div>
					{/if}
				{/if}
			{/if}
		</div>
	{/if}
	{#if workError}<p role="alert" class="px-4 pb-3 text-[12px] text-danger">
			{workError} <button class="min-h-8 underline" onclick={() => workPoll.now()}>Retry</button>
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
{#snippet card(file: LibraryFile)}
	<TitleFile
		{file}
		{manyFiles}
		{admin}
		{work}
		unavailable={!!workError}
		bind:busy={workBusy}
		onchanged={readWork}
		editing={tagEditing}
		siblings={detail?.files ?? []}
		series={kind === 'series'}
	/>
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
				<ul class="flex flex-col gap-4">
					{#each season.files.slice(0, files) as file (file.path)}
						{@render card(file)}
					{/each}
				</ul>
				{#if files < season.files.length}
					<div use:whenNear={more} class="h-px"></div>
				{/if}
			{/snippet}
		</Disclosure>
	</section>
{/snippet}
