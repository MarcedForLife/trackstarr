<script lang="ts">
	import { page } from '$app/state';
	import { SvelteSet } from 'svelte/reactivity';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import Sheet, { SLIDE } from '$lib/components/Sheet.svelte';
	import TrackEditor from '$lib/components/TrackEditor.svelte';
	import { refusalText } from '$lib/api';
	import { MARKS, type MarkName } from '$lib/connections';
	import { arrival, coverShow, type Arrival } from '$lib/covers';
	import { button } from '$lib/controls';
	import { ago } from '$lib/events';
	import { bytesFor, carriesLanguage, describe, duration, rate } from '$lib/format';
	import {
		forTitle,
		getHolds,
		lift as liftHold,
		place as placeHold,
		SPANS,
		type Hold
	} from '$lib/holds';
	import {
		coverUrl,
		getLinks,
		getTitle,
		kindName,
		listing,
		size,
		verdictLabel,
		verdictText,
		type Card,
		type LibraryFile,
		type Listed,
		type Modified,
		type Row,
		type RunMode,
		type TitleDetail,
		type TitleLink,
		type Track,
		type Why
	} from '$lib/library';
	import { overlay } from '$lib/overlay';
	import { editable, matching, summarise, type Outcome, type Summary } from '$lib/retag';
	import { whenNear } from '$lib/reveal';
	import { seasons, type Season } from '$lib/seasons';
	import type { Run } from '$lib/runs';
	import { getSettings } from '$lib/settings';

	// One title, with what each file is and what a rewrite would leave. Owns
	// being open: the fetch, the two-phase close, and a second poster tapped
	// while the first slides out. A caller keeps a `bind:this` and calls open().

	// Running this one title from inside the sheet. The library page still owns
	// the poll and the bar that shows the run once the sheet is gone; a page with
	// nowhere to run passes no runner and gets no panel.
	type Runner = {
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
		// The run this panel started, while it is going.
		run: Run | null;
		// A stop sent but not yet confirmed.
		stopping: boolean;
		// What the run came to.
		done: string;
		onrun: (card: Card, mode: RunMode) => void;
		onstop: () => void;
	};

	// `onshut` lets a caller polling for the sheet stop.
	let { runner, onshut }: { runner?: Runner; onshut?: () => void } = $props();

	// The open title: the grid's card at once for the header, then the detail.
	let opened = $state<Card | null>(null);
	let detail = $state<TitleDetail | null>(null);
	// Whether the sheet is up, apart from what it shows: the panel slides down at
	// once and the title goes when it has finished.
	let up = $state(false);
	let emptying: ReturnType<typeof setTimeout> | null = null;
	let loading = $state(false);
	let failure = $state('');

	// How the cover reached the header, so it fades in over the tile rather than
	// snapping onto it. A cover the grid has already shown arrives without a
	// fade, which is most of them: the card that was tapped wears one. Reset
	// per title, since the sheet shows a second without either going.
	let cover = $state<Arrival>('coming');
	const showing = $derived(coverShow(cover));
	const art = $derived(opened ? coverUrl(opened.id) : '');

	// Where to watch this title. Null while the media servers are being asked, so
	// the buttons stand greyed in the row rather than landing under the thumb.
	let links = $state<TitleLink[] | null>(null);

	// Whether this title is being left alone for now, and the choices for
	// putting it that way. Owned here rather than passed in: the sheet opens
	// from two pages and already fetches for itself.
	const admin = $derived(page.data.user?.role === 'admin');
	let holds = $state<Hold[]>([]);
	// The durations are showing, rather than the one button that raises them.
	let choosing = $state(false);
	let holdBusy = $state('');
	let holdError = $state('');
	const hold = $derived(opened ? forTitle(holds, opened.id) : undefined);
	const left = $derived(hold?.seconds ? `${duration(hold.seconds)} left` : 'until lifted');

	// The menu the Hold button raises. Answers Escape before the sheet under it,
	// and takes no history entry: a back press should leave the sheet, not the
	// menu over it.
	const chooser = overlay({ close: () => (choosing = false) });
	// The button and its menu, for telling a press outside the pair from one in
	// it.
	let howLong = $state<HTMLElement>();

	function askHowLong() {
		if (choosing) chooser.lower();
		else {
			choosing = true;
			chooser.raise();
		}
	}

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

	// Placeholder rows while the verdicts load, so the sheet opens at the height
	// it is about to need.
	const waiting = $derived(Math.min(opened?.files || 1, FILE_PAGE));

	export async function open(card: Card) {
		// Another poster tapped while the last slides out: the sheet stays up.
		if (emptying !== null) {
			clearTimeout(emptying);
			emptying = null;
		}
		held.raise();
		up = true;
		opened = card;
		detail = null;
		// A second title tapped brings a second cover, which has yet to arrive.
		cover = 'coming';
		files = FILE_PAGE;
		unfolded = null;
		failure = '';
		links = null;
		holds = [];
		// Through the stack, or a menu left up over the last title would still be
		// answering Escape.
		chooser.lower();
		editor.lower();
		retagged = null;
		holdError = '';
		loading = true;
		// Alongside the verdicts: neither should wait on the other.
		find(card);
		lookUpHolds();
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
	async function find(card: Card) {
		let found: TitleLink[];
		try {
			found = await getLinks(card.id);
		} catch {
			found = [];
		}
		if (opened?.id === card.id) links = found;
	}

	// Quietly: a title that cannot be read for holds still shows its files, and
	// the row is simply not offered.
	async function lookUpHolds() {
		try {
			holds = await getHolds();
		} catch {
			holds = [];
		}
	}

	async function keep(seconds: number) {
		if (!opened) return;
		holdBusy = String(seconds);
		holdError = '';
		try {
			holds = await placeHold({ ids: [opened.id] }, seconds);
			chooser.lower();
		} catch (error) {
			holdError = refusalText(error);
		} finally {
			holdBusy = '';
		}
	}

	async function release() {
		if (!opened) return;
		holdBusy = 'lift';
		holdError = '';
		try {
			holds = await liftHold({ ids: [opened.id] });
		} catch (error) {
			holdError = refusalText(error);
		} finally {
			holdBusy = '';
		}
	}

	// Narrowing, not a cast: a name with no mark keeps its button and loses its
	// logo.
	function mark(name: string): MarkName | null {
		return MARKS.find((known) => known === name) ?? null;
	}

	// A share of the line below sm, natural width from sm up, as RunButtons does.
	const spread = 'flex-1 sm:flex-none';

	// One button to open the title elsewhere: centred in the row a phone gives it,
	// left-aligned in the column from sm up so the marks line up down its edge.
	const link = `${button} ${spread} sm:justify-start`;

	// The services the header offers, whether they have been asked yet or not.
	const offered = $derived(links ?? detail?.servers ?? []);

	// From sm up the cover stands as tall as the buttons beside it and their
	// gaps: 40px each and 8px between, so three make 136 and four make 184.
	// Never shorter than three, so a title with two links keeps the poster a
	// title with three gets, and four is the most the service can offer one
	// title. Written out, since Tailwind reads its classes from the source.
	const poster = $derived(offered.length > 3 ? 'sm:h-46' : 'sm:h-34');

	// What the hold control takes of the row: a column of the grid where Plan and
	// Process are beside it, its own width where it stands alone.
	const cell = $derived(runner ? 'w-full' : 'flex-none');

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

	/** Re-read the title after a run rewrote its verdicts. Silent, unlike open():
	 * skeletons would lose the reader's place. */
	export async function reload(card?: Card) {
		const showing = opened;
		if (!showing) return;
		if (card) opened = card;
		try {
			const found = await getTitle(showing.id);
			if (opened?.id === showing.id) detail = found;
		} catch {
			// The rows on screen are a run out of date, which beats an error.
		}
	}

	// Whether the box has buttons, not just a line saying what stands: a reader
	// who cannot act still sees a hold.
	const acting = $derived(!!runner || admin);

	// The same kinds the row's own line says a language on.
	function tagged(row: Listed): boolean {
		return carriesLanguage(row.track.kind);
	}

	// Whether this row's tags can be edited: a track the file holds now, of a
	// kind the rules read tags on, in a container the service edits in place.
	function edits(file: LibraryFile, row: Listed): boolean {
		return admin && row.stream !== null && tagged(row) && editable(file);
	}

	// Why an admin's audio and subtitle rows of this file do not open: the one
	// reason a row can carry, said once under the heading for a phone and on
	// each row for a pointer.
	const MKV_ONLY = 'Tags are edited in place on .mkv files only. The remux rule converts this one.';

	function locked(file: LibraryFile, rows: Listed[]): string {
		return admin && !editable(file) && rows.some(tagged) ? MKV_ONLY : '';
	}

	function openedFor(file: LibraryFile, row: Listed): boolean {
		return editing?.path === file.path && editing.stream === row.stream;
	}

	// The track as the file has it, rather than as a plan would leave it: a plan
	// clears titles and converts subtitles, and neither is on disk yet.
	function onDisk(file: LibraryFile, row: Listed): Track {
		return file.tracks.find((track) => track.index === row.stream) ?? row.track;
	}

	function edit(file: LibraryFile, row: Listed, pressed: HTMLElement) {
		if (openedFor(file, row) || row.stream === null) {
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
	const alarm = $derived(holdError || runner?.error || '');

	const KIND_LETTER: Record<string, string> = {
		video: 'V',
		audio: 'A',
		subtitle: 'S',
		attachment: 'F'
	};

	// What a track takes of the file: its rate over the running time. Video and
	// audio only, since a subtitle is tens of kilobytes whatever the film.
	function weighs(track: Track, file: LibraryFile): number {
		if (track.kind !== 'video' && track.kind !== 'audio') return 0;
		return bytesFor(track.bitrate, file.seconds);
	}

	function badges(track: Track): string[] {
		// "generated" is said by the row's colour.
		return (track.flags ?? []).filter((flag) => flag !== 'generated' && flag !== 'default');
	}

	// The number the row leads with, padded to the width of the longest a file is
	// likely to reach so the kind letters line up beneath each other. Blank for a
	// track the rewrite drops, which is what having no place looks like.
	function place(row: Row): string {
		return (row.position === null ? '' : `[${row.position}]`).padStart(4);
	}

	// One change per line; run together they were a paragraph nobody finished.
	// `mark` says what the change does to the file and `rides` whether the rules
	// chose it, so neither has to carry the other's meaning.
	type Change = { text: string; mark: string; rides: boolean };

	// The verb a change opens with and the mark that stands for it. The planner
	// writes these strings to a form, so the first word is the verb; see
	// _record in planner.py. A clear takes a title away, which is a drop of the
	// only thing it had. The plus and minus are the pair shrank() already uses.
	const CHANGE_MARKS: [string, string][] = [
		['add', '+'],
		['drop', '−'],
		['clear', '−'],
		['regenerate', '~'],
		['replace', '~']
	];

	// A remux or a reorder is none of the three, and keeps the plain mark.
	function changeMark(text: string): string {
		const verb = text.slice(0, text.indexOf(' '));
		return CHANGE_MARKS.find(([opener]) => opener === verb)?.[1] ?? '›';
	}

	function changes(told: Why): Change[] {
		return [
			...(told.reasons ?? []).map((text) => ({ text, mark: changeMark(text), rides: false })),
			// Marked apart by the colour: these never cause a rewrite on their own.
			...(told.incidental ?? []).map((text) => ({ text, mark: changeMark(text), rides: true }))
		];
	}

	// What the rewrite did to the file's size, as the history's chips spell it.
	function shrank(rewrote: Modified): string {
		const { bytes_before: before, bytes_after: after } = rewrote;
		if (before === undefined || after === undefined) return '';
		const delta = after - before;
		return `${delta < 0 ? '−' : '+'}${size(delta)}`;
	}
</script>

<svelte:window
	onpointerdown={(event) => choosing && !howLong?.contains(event.target as Node) && chooser.lower()}
/>

<Sheet open={up} onclose={close} label={opened?.name ?? 'Title'}>
	{#if opened}
		<div class="px-4 pb-[calc(1.5rem+env(safe-area-inset-bottom))] sm:px-6">
			<div class="flex flex-wrap gap-4">
				<!-- As tall as the links beside it; see `poster`. Width follows the
				     height at a poster's 2:3. The tile holds the shape and the border
				     while the cover fades in over it, so the header does not shift or
				     flash as the picture lands. -->
				<span
					class={`relative block aspect-[2/3] h-[7.5rem] w-auto flex-none overflow-hidden rounded-lg border border-line bg-sunken ${poster}`}
				>
					<img
						src={art}
						alt=""
						onload={() => (cover = arrival(art))}
						class={`absolute inset-0 h-full w-full object-cover ${showing}`}
					/>
				</span>
				<div class="min-w-0 flex-1">
					<h2 class="text-[17px] font-semibold tracking-tight">{opened.name}</h2>
					<p class="mt-0.5 text-[12.5px] text-dim">
						{[
							opened.year,
							kindName(opened.kind),
							detail?.lang ?? opened.lang,
							// Last, and named: the one thing here about the film rather than our
							// copy of it.
							opened.rating ? `IMDb ${opened.rating.toFixed(1)}` : ''
						]
							.filter(Boolean)
							.join(' · ')}
					</p>
					<p class={`mt-2 text-[13px] font-medium ${verdictText[opened.state] ?? 'text-dim'}`}>
						{verdictLabel(opened.state)}
					</p>
					{#if detail}
						<p class="mt-0.5 font-mono text-[11px] break-all text-faint">{detail.folder}</p>
					{/if}
				</div>

				<!-- Where to watch it and where it is managed. A row under the cover on
				     a phone, a column beside the title from sm up, where the header has
				     room to spare and a row of its own cost a line. A server still being
				     asked holds its place greyed, so a late answer does not shove
				     everything up under the thumb. The mark leads on its dark tile, and
				     the label drops to the name; "Open in" stays for a screen reader. -->
				{#if offered.length}
					<div class="flex w-full flex-wrap gap-2 sm:w-auto sm:flex-col sm:flex-nowrap">
						{#each offered as server (server.server)}
							{@const found = server.url ?? ''}
							{@const logo = mark(server.server)}
							{#if found}
								<!-- Another application on another host, so no resolve(). -->
								<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
								<a href={found} target="_blank" rel="noreferrer" class={link}>
									{#if logo}<ServiceIcon name={logo} box={20} size={14} />{/if}
									<span class="sr-only">Open in </span>{server.label}
									<!-- Last on the line rather than trailing the name, so the
									     column ends on one edge as it starts on one. -->
									<span class="flex-none sm:ml-auto"><Glyph name="open" /></span>
								</a>
							{:else}
								<span class={`${link} opacity-40`} aria-hidden="true">
									{#if logo}<ServiceIcon name={logo} box={20} size={14} />{/if}
									<span class="sr-only">Open in </span>{server.label}
									<span class="flex-none sm:ml-auto"><Glyph name="open" /></span>
								</span>
							{/if}
						{/each}
					</div>
				{/if}
			</div>

			<!-- One sunken block, near the thumb, for everything here that does
			     something. Hold sits with the runs: it answers the same question, and
			     Process on a held title rewrites nothing. A run takes the row over. -->
			{#if acting || hold}
				<div class="mt-4 rounded-xl border border-line bg-sunken p-3">
					{#if runner?.run}
						<RunProgress
							run={runner.run}
							stopping={runner.stopping}
							noun="this title"
							onstop={runner.onstop}
						/>
					{:else if acting}
						<!-- One row at every width; stacked, the panel pushed the rows below
						     the fold. Three equal columns, since the three are one decision
						     and a narrower Hold read as the lesser of them. Nothing to run:
						     Hold alone, at its own width rather than a third of the row. -->
						<div class={runner ? 'grid grid-cols-3 items-center gap-3' : 'flex items-center gap-3'}>
							{#if runner}
								<RunButtons
									columns
									mayRewrite={runner.mayRewrite}
									refuses={runner.refuses}
									disabled={runner.starting}
									busy={runner.busy}
									onrun={(mode) => opened && runner.onrun(opened, mode)}
								/>
							{/if}
							{#if admin && hold}
								<button onclick={release} disabled={!!holdBusy} class={`${cell} ${button}`}>
									{holdBusy === 'lift' ? 'Lifting…' : 'Lift'}
								</button>
							{:else if admin}
								<!-- How long is the only question a hold asks, and it is asked in a
								     menu under the button rather than in the row: opened inline it
								     shoved the file list down the screen every time. -->
								<div bind:this={howLong} class={`relative ${cell}`}>
									<button
										onclick={askHowLong}
										aria-expanded={choosing}
										title="Leave this title alone. It is still judged, just not rewritten."
										class={`${cell} ${button}`}
									>
										<Glyph name="pause" />
										Hold
									</button>

									{#if choosing}
										<!-- Under the button that raised it and as wide, over the rows
										     below rather than moving them. The minimum is what the
										     longest choice needs, since a third of a phone is narrower
										     than that; there it hangs from the button's right edge. -->
										<div
											class="menu absolute top-full right-0 z-30 mt-2 w-full min-w-52 rounded-xl border border-line-strong bg-raised p-1 shadow-lg"
										>
											<p class="px-2.5 pt-1.5 pb-1 text-[11px] text-faint">Hold for</p>
											{#each SPANS as span (span.seconds)}
												<button
													onclick={() => keep(span.seconds)}
													disabled={!!holdBusy}
													class="flex h-10 w-full items-center rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-(--disabled)"
												>
													{holdBusy === String(span.seconds) ? 'Holding…' : span.label}
												</button>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/if}

					<!-- What stands, and what came of the last press. A hold is a state,
					     so it shows alongside; the rest is one line, the loudest first. -->
					{#if hold || alarm || runner?.refuses || runner?.done}
						<div class={`flex flex-col gap-1 text-[12px] ${acting ? 'mt-2.5' : ''}`}>
							{#if hold}
								<!-- Not who placed it: on a library one household runs, the name is
								     always the reader's own. -->
								<p class="text-dim">{['On hold', left, hold.reason].filter(Boolean).join(' · ')}</p>
							{/if}
							{#if alarm}
								<p role="alert" class="text-danger">{alarm}</p>
							{:else if runner?.refuses}
								<p class="text-dim">{runner.refuses}</p>
							{:else if runner?.done}
								<!-- What the last run came to, kept until the next starts. -->
								<p role="status" class="text-dim">{runner.done}</p>
							{/if}
						</div>
					{/if}
				</div>
			{/if}

			{#if failure}
				<p class="mt-5 text-sm text-danger">{failure}</p>
			{:else if loading || !detail}
				<!-- Blocks the size of the rows about to arrive, so the bottom-anchored
				     sheet rises once rather than jumping when they land. -->
				<ul class="mt-5 flex flex-col gap-4" aria-hidden="true">
					{#each { length: waiting }, at (at)}
						<li class="h-[7rem] rounded-xl border border-line bg-sunken"></li>
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
</Sheet>

<!-- One file: what it is, what a rewrite would leave, and why. -->
{#snippet card(file: LibraryFile)}
	{@const shown = listing(file)}
	<li class="rounded-xl border border-line bg-sunken p-3">
		<div class="flex items-baseline gap-2">
			<p class="min-w-0 flex-1 truncate text-[13px] font-medium" title={file.name}>
				{file.name}
			</p>
			<span class={`flex-none text-[11px] font-semibold ${verdictText[file.status] ?? 'text-dim'}`}>
				{verdictLabel(file.status)}
			</span>
		</div>
		<!-- The whole of what a rewrite of ours left behind: when, and what it
		     cost. What it changed is the two columns below, where any track moved,
		     and the history page where none did. A flex row rather than a run of
		     text: whitespace between two blocks is the one thing a template cannot
		     be held to. -->
		<p class="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-[11.5px] text-faint">
			<span>{size(file.bytes)}</span>
			{#if file.modified}
				<span aria-hidden="true">·</span>
				<span class="text-ok">Modified {ago(file.modified.at)}</span>
				{#if shrank(file.modified)}
					<span class="font-mono">{shrank(file.modified)}</span>
				{/if}
			{/if}
		</p>

		{#if shown.rows.length}
			<!-- One list, the whole width, in the order the file ends up in. A
			     rewrite copies far more than it touches, so two columns were mostly
			     the same list twice. -->
			<div class="mt-3">
				{@render heading(shown.label)}
				{#if locked(file, shown.rows)}
					<p class="mb-1.5 text-[11px] text-faint">{MKV_ONLY}</p>
				{/if}
				{@render list(shown.rows, file)}
			</div>
		{/if}

		{#if file.why.skip}
			<!-- The skip first, or the reasons read as a rewrite that never comes.
			     Labelled with the file's own verdict, since an unsupported container
			     is a skip in the plan. -->
			<p class="mt-3 text-[12px] text-dim">
				<span class="font-medium text-fg">{verdictLabel(file.status)}:</span>
				{file.why.skip}
			</p>
		{/if}
		{#if file.why.failed}
			<!-- Above the changes for the same reason as the skip. -->
			<p class="mt-3 text-[12px] text-dim">
				<span class="font-medium text-danger">Failed:</span>
				{file.why.failed}
			</p>
		{/if}
		{@render account(file.why)}
		{#if !changes(file.why).length && !file.why.skip && !file.why.failed}
			<p class="mt-3 text-[12px] text-faint">Nothing to change.</p>
		{/if}
	</li>
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

<!-- What the list's numbering is of, over it. -->
{#snippet heading(text: string)}
	<p class="pb-1 text-[10.5px] font-semibold tracking-wider text-faint uppercase">{text}</p>
{/snippet}

<!-- The file's tracks, one row each. The number is the place the track takes in
     the file the rewrite leaves; a dropped row has none and is struck through,
     a generated one is accented. Title and flags go on a second line under the
     track they belong to, indented by the grid rather than by a guessed width.
     For an admin a row the file holds is a button that opens its tags for
     editing, marked by the pencil at its end; the editor comes up inline under
     it. -->
{#snippet list(shown: Listed[], file: LibraryFile)}
	<ul class="flex flex-col gap-1">
		{#each shown as row, at (at)}
			{@const gone = row.state === 'dropped'}
			{@const fresh = row.state === 'added'}
			{@const open = openedFor(file, row)}
			{@const told =
				retagged?.path === file.path && retagged.stream === row.stream ? retagged : null}
			<li
				class={`font-mono text-[11px] ${gone ? 'text-faint' : fresh ? 'text-accent' : 'text-dim'}`}
			>
				<!-- Padded to a tappable height either way, so the two variants line
				     up: the text alone is a 17px line. -->
				{#if edits(file, row)}
					<button
						type="button"
						onclick={(event) => edit(file, row, event.currentTarget)}
						aria-expanded={open}
						class={`grid w-full grid-cols-[auto_1fr] gap-x-1.5 rounded px-1 py-1.5 text-left transition-colors hover:bg-raised ${
							open ? 'bg-raised' : ''
						}`}
					>
						{@render cells(row, file, gone, fresh, true)}
					</button>
				{:else}
					<div
						class="grid grid-cols-[auto_1fr] gap-x-1.5 px-1 py-1.5"
						title={admin && tagged(row) && !editable(file) ? MKV_ONLY : undefined}
					>
						{@render cells(row, file, gone, fresh, false)}
					</div>
				{/if}
				{#if open}
					{@const track = onDisk(file, row)}
					<div class="mt-1 font-sans">
						<TrackEditor
							{track}
							twins={matching(detail?.files ?? [], file, track)}
							{languages}
							many={opened?.kind === 'series'}
							ondone={(outcomes) => retaggedTo(file, row, outcomes)}
							oncancel={() => editor.lower()}
						/>
					</div>
				{:else if told}
					<!-- What the edit came to, where the form was: the row above already
					     reads as the file now does, and this says how many others
					     followed. -->
					<div class="px-1 pb-1 font-sans text-[12px]">
						<p role="status" class={told.problems.length ? 'text-dim' : 'text-ok'}>
							{told.line}
						</p>
						{#each told.problems as problem, at (at)}
							<p class="text-danger">{problem}</p>
						{/each}
					</div>
				{/if}
			</li>
		{/each}
	</ul>
{/snippet}

<!-- One row's cells: its place and kind, what it is, and under that its title
     and flags. `pencil` marks a row that opens. -->
{#snippet cells(row: Listed, file: LibraryFile, gone: boolean, fresh: boolean, pencil: boolean)}
	{@const track = row.track}
	{@const takes = weighs(track, file)}
	<span class="whitespace-pre text-faint">{place(row)} {KIND_LETTER[track.kind] ?? '·'}</span>
	<span class="flex min-w-0 items-baseline gap-1.5">
		<span class={`min-w-0 truncate ${gone ? 'line-through' : ''} ${fresh ? 'font-semibold' : ''}`}>
			{describe(track)}
		</span>
		{#if fresh}
			<span class="flex-none text-[10px] tracking-wide">NEW</span>
		{/if}
		<span class="ml-auto flex flex-none items-baseline gap-1.5 pl-2 text-faint">
			{#if track.bitrate}
				<span>{rate(track.bitrate)}</span>
			{/if}
			{#if takes}
				<span>{size(takes)}</span>
			{/if}
			{#if pencil}
				<Glyph name="pencil" size={11} />
			{/if}
		</span>
	</span>
	{#if track.title || badges(track).length}
		<span class="col-start-2 flex min-w-0 items-baseline gap-1.5 text-faint">
			<span class="min-w-0 truncate">{track.title}</span>
			{#each badges(track) as flag (flag)}
				<span class="flex-none rounded border border-line px-1 text-[10px]">{flag}</span>
			{/each}
		</span>
	{/if}
{/snippet}

<!-- What a rewrite of this file would do: the changes a line each, then the
     rules behind them. -->
{#snippet account(told: Why)}
	{#if changes(told).length}
		<ul class="mt-3 flex flex-col gap-1">
			{#each changes(told) as change, at (at)}
				<li class={`flex gap-1.5 text-[12px] ${change.rides ? 'text-faint' : 'text-dim'}`}>
					<!-- Marks our fonts carry; see Glyph.svelte. Fixed width so every
					     line starts on one column whichever mark it takes. -->
					<span class="w-2 flex-none text-center">{change.mark}</span>
					<span class="min-w-0">{change.text}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if told.rules?.length || told.incidental_rules?.length}
		<div class="mt-1.5 flex flex-wrap gap-1.5">
			{#each told.rules ?? [] as rule (rule)}
				<span
					class="rounded border border-line bg-accent-soft px-1.5 py-0.5 font-mono text-[10.5px] text-accent"
				>
					{rule}
				</span>
			{/each}
			{#each told.incidental_rules ?? [] as rule (rule)}
				<span class="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-faint">
					{rule}
				</span>
			{/each}
		</div>
	{/if}
{/snippet}

<style>
	/* The menu arriving from under the button that raised it. Out-cubic over a
	   short distance, as the sheet and the bar use; the global reduced-motion
	   rule in layout.css takes it away. */
	.menu {
		animation: menu-in 150ms cubic-bezier(0.33, 1, 0.68, 1);
		transform-origin: top right;
	}

	@keyframes menu-in {
		from {
			opacity: 0;
			transform: translateY(-6px) scale(0.97);
		}
	}
</style>
