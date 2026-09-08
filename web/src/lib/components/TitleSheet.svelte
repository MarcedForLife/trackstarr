<script lang="ts">
	import { page } from '$app/state';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import Sheet, { SLIDE } from '$lib/components/Sheet.svelte';
	import { refusalText } from '$lib/api';
	import { MARKS, type MarkName } from '$lib/connections';
	import { arrival, coverShow, type Arrival } from '$lib/covers';
	import { button } from '$lib/controls';
	import { ago } from '$lib/events';
	import { duration } from '$lib/format';
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
		describe,
		getLinks,
		getTitle,
		kindName,
		listing,
		rate,
		size,
		verdictLabel,
		verdictText,
		type Card,
		type Modified,
		type Row,
		type RunMode,
		type TitleDetail,
		type TitleLink,
		type Track,
		type Why
	} from '$lib/library';
	import { overlay } from '$lib/overlay';
	import { whenNear } from '$lib/reveal';
	import type { Run } from '$lib/runs';

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

	// A long series is two hundred files with a track list each.
	const FILE_PAGE = 12;
	let files = $state(FILE_PAGE);

	const rows = $derived(detail?.files.slice(0, files) ?? []);

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
		failure = '';
		links = null;
		holds = [];
		// Through the stack, or a menu left up over the last title would still be
		// answering Escape.
		chooser.lower();
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

	// What went wrong, whichever button caused it. One line, since only one
	// press is ever in flight.
	const alarm = $derived(holdError || runner?.error || '');

	const KIND_LETTER: Record<string, string> = {
		video: 'V',
		audio: 'A',
		subtitle: 'S',
		attachment: 'F'
	};

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
													class="flex h-10 w-full items-center rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-50"
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

				<ul class="mt-5 flex flex-col gap-4">
					{#each rows as file (file.path)}
						{@const shown = listing(file)}
						<li class="rounded-xl border border-line bg-sunken p-3">
							<div class="flex items-baseline gap-2">
								<p class="min-w-0 flex-1 truncate text-[13px] font-medium" title={file.name}>
									{file.name}
								</p>
								<span
									class={`flex-none text-[11px] font-semibold ${verdictText[file.status] ?? 'text-dim'}`}
								>
									{verdictLabel(file.status)}
								</span>
							</div>
							<!-- The whole of what a rewrite of ours left behind: when, and what
							     it cost. What it changed is the two columns below, where any
							     track moved, and the history page where none did. A flex row
							     rather than a run of text: whitespace between two blocks is the
							     one thing a template cannot be held to. -->
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
								<!-- One list, the whole width, in the order the file ends up in.
								     A rewrite copies far more than it touches, so two columns were
								     mostly the same list twice. -->
								<div class="mt-3">
									{@render heading(shown.label)}
									{@render list(shown.rows)}
								</div>
							{/if}

							{#if file.why.skip}
								<!-- The skip first, or the reasons read as a rewrite that never
								     comes. Labelled with the file's own verdict, since an
								     unsupported container is a skip in the plan. -->
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
					{/each}
				</ul>
				{#if files < detail.files.length}
					<div
						use:whenNear={() => (files = Math.min(files + FILE_PAGE, detail?.files.length ?? 0))}
						class="h-px"
					></div>
				{/if}
			{/if}
		</div>
	{/if}
</Sheet>

<!-- What the list's numbering is of, over it. -->
{#snippet heading(text: string)}
	<p class="pb-1 text-[10.5px] font-semibold tracking-wider text-faint uppercase">{text}</p>
{/snippet}

<!-- The file's tracks, one row each. The number is the place the track takes in
     the file the rewrite leaves; a dropped row has none and is struck through,
     a generated one is accented. Title and flags go on a second line under the
     track they belong to, indented by the grid rather than by a guessed width. -->
{#snippet list(shown: Row[])}
	<ul class="flex flex-col gap-1">
		{#each shown as row, at (at)}
			{@const track = row.track}
			{@const gone = row.state === 'dropped'}
			{@const fresh = row.state === 'added'}
			<li
				class={`grid grid-cols-[auto_1fr] gap-x-1.5 font-mono text-[11px] ${
					gone ? 'text-faint' : fresh ? 'text-accent' : 'text-dim'
				}`}
			>
				<span class="whitespace-pre text-faint">{place(row)} {KIND_LETTER[track.kind] ?? '·'}</span>
				<span class="flex min-w-0 items-baseline gap-1.5">
					<span
						class={`min-w-0 truncate ${gone ? 'line-through' : ''} ${fresh ? 'font-semibold' : ''}`}
					>
						{describe(track)}
					</span>
					{#if fresh}
						<span class="flex-none text-[10px] tracking-wide">NEW</span>
					{/if}
					{#if track.bitrate}
						<span class="ml-auto flex-none pl-2 text-faint">{rate(track.bitrate)}</span>
					{/if}
				</span>
				{#if track.title || badges(track).length}
					<span class="col-start-2 flex min-w-0 items-baseline gap-1.5 text-faint">
						<span class="min-w-0 truncate">{track.title}</span>
						{#each badges(track) as flag (flag)}
							<span class="flex-none rounded border border-line px-1 text-[10px]">{flag}</span>
						{/each}
					</span>
				{/if}
			</li>
		{/each}
	</ul>
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
