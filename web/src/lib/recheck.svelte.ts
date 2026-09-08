// A re-check, and everything the library page needs to know while one runs:
// which run is watched, whether it was started from the bar or a title's sheet,
// whether another may start, and what the last one came to. A run already
// under way when the page loads is adopted, since it is writing this grid's
// verdicts. Which posters are ticked is the grid's business.

import { onDestroy } from 'svelte';
import type { Landed, Snapshot } from '$lib/activity.svelte';
import { refusalText } from '$lib/api';
import { count, getEvents, LOOKBACK, type Event } from '$lib/events';
import { runTitles, type Card, type RunMode } from '$lib/library';
import { stopRun, type Run } from '$lib/runs';
import { told } from '$lib/stream';

// The three paces this asks the snapshot for: a progress bar, a control that
// must stay honest about whether a run may start, and a tab with no reason of
// its own that must still notice a sweep starting next door.
const BUSY_MS = 2000;
const IDLE_MS = 15000;
const WATCH_MS = 30000;

// How long a freshly started run may be missing from snapshots before that
// reads as it having ended. A start answers before the run is registered.
const WARMING_MS = 5000;

type Options = {
	/** Whether a selection or an open sheet is showing the run controls, which
	 * need to know whether a run may start. */
	asking: () => boolean;
	/** A run has written its last verdict: read the shelf again. */
	onwritten: () => void;
	/** The watched run has ended and left a receipt; the page decides where it
	 * shows. */
	ondone: () => void;
};

export class Recheck {
	/** Whether REWRITE_MODE lets this install rewrite; when false the page must
	 * not offer one. */
	mayRewrite = $state(false);
	paused = $state(false);
	/** A sweep or somebody else's re-check. The service allows one walk at a
	 * time, so the controls say so up front. */
	otherRun = $state<Run | null>(null);
	/** The run being watched, as the last snapshot had it. */
	running = $state<Run | null>(null);
	/** What the run came to, from its history summary rather than the last
	 * snapshot, which may have caught it part way through. */
	summary = $state<Event | null>(null);
	/** The service's words when it turns a press down, kept until the next. */
	refusal = $state('');
	/** Which of the two runs a press has started, so only that one says so. */
	started = $state<'' | RunMode>('');
	/** A stop already sent, which the next snapshot has yet to confirm. */
	stopping = $state(false);

	// The run being watched. Seeded from the load and adopted as snapshots land,
	// which is what makes the bar survive a reload.
	#watching = $state<string | null>(null);
	// When it was picked up, for the warming window.
	#watchedAt = Date.now();
	#starting = $state(false);
	// Whether the watched run was started from a title's sheet, which decides
	// where its progress and result show.
	#fromSheet = $state(false);
	// What the summary event does not carry: the run's label and whether it
	// rewrote. Taken off the snapshot, since the service decides both.
	#ranLabel = $state('');
	#ranDry = $state(true);
	#options: Options;
	#snapshot: Snapshot;

	constructor(snapshot: Snapshot, options: Options) {
		this.#snapshot = snapshot;
		this.#options = options;
		const activity = snapshot.current;
		this.mayRewrite = activity.may_rewrite;
		this.paused = activity.paused;
		this.#watching = activity.runs.find((run) => run.kind === 'recheck')?.id ?? null;
		// On this class's own terms, or the adopted run would be its own rival
		// until the first look.
		this.otherRun = activity.runs.find((run) => this.#rival(run)) ?? null;
		onDestroy(
			snapshot.watch({
				pace: () => this.#pace(),
				saw: (landed) => this.#saw(landed)
			})
		);
	}

	/** A lost connection, the snapshot's own: every reader on the page lost the
	 * same one. */
	get offline(): string {
		return this.#snapshot.offline;
	}

	/** Anything the service is running that this page did not start. */
	#rival(run: Run): boolean {
		return run.id !== this.#watching && (run.kind === 'sweep' || run.kind === 'recheck');
	}

	/** Whether anything is rewriting the verdicts the grid draws. */
	walking = $derived(!!this.#watching || !!this.otherRun);

	/** Started but not yet in a snapshot, or the controls would come back for a
	 * second, long enough to press again. */
	warming = $derived(!!this.#watching && !this.running);

	/** Whether the press that started the run being watched came from a sheet. */
	get fromSheet(): boolean {
		return this.#fromSheet;
	}

	// Anything a new re-check would have to wait for, the page's own run
	// included.
	#inTheWay = $derived(this.otherRun ?? this.running);

	/** Why a run cannot start now, or nothing. One place, so every control
	 * refuses in the same words. */
	refuses = $derived(
		this.paused
			? 'Processing is paused. Resume it first.'
			: this.#inTheWay
				? `A ${this.#inTheWay.kind === 'sweep' ? 'sweep' : 're-check'} is running. Stop it first.`
				: ''
	);

	/** Which press the bar shows as going. Only where the run was started from
	 * says so. */
	busy = $derived<'' | RunMode>(this.#fromSheet ? '' : this.started);

	/** Everything a title's sheet shows and does about running that title, as
	 * one prop. Whether to offer it at all is the page's call. */
	runner = $derived({
		mayRewrite: this.mayRewrite,
		refuses: this.refuses,
		busy: this.#fromSheet ? this.started : '',
		starting: this.#fromSheet && (this.#starting || this.warming),
		error: this.#fromSheet ? this.refusal : '',
		run: this.#fromSheet ? this.running : null,
		stopping: this.stopping,
		done: this.#fromSheet ? this.line('this title') : '',
		onrun: (card: Card, mode: RunMode) => this.runOne(card, mode),
		onstop: () => this.stop()
	});

	// What the last run came to: files looked at, and the three verdicts worth a
	// word. The rest are already on the posters in colour.
	#outcome = $derived.by(() => {
		if (!this.summary || this.running) return '';
		const counts = this.summary.counts ?? {};
		const notes = [
			counts['modified'] ? `${counts['modified'].toLocaleString()} rewritten` : '',
			counts['pending'] ? `${counts['pending'].toLocaleString()} pending` : '',
			counts['failed'] ? `${counts['failed'].toLocaleString()} failed` : ''
		].filter(Boolean);
		const looked = count(this.summary.files, 'file');
		return notes.length ? `${looked} · ${notes.join(' · ')}` : `${looked} · nothing to change`;
	});

	/** The receipt both readouts use. The noun is the caller's fallback for a
	 * run that started and finished between two polls, leaving no label. */
	line(noun: string): string {
		if (!this.#outcome) return '';
		return `${this.#ranDry ? 'Planned' : 'Processed'} ${this.#ranLabel || noun} · ${this.#outcome}`;
	}

	/** Ask the service now. */
	prod() {
		this.#snapshot.prod();
	}

	/** Judge these titles again, from the bar over the grid. */
	start(ids: string[], mode: RunMode) {
		return this.#launch(ids, false, mode);
	}

	/** One title from inside its own sheet. The sheet stays open and its panel
	 * becomes the progress bar; the selection is untouched. */
	runOne(card: Card, mode: RunMode) {
		return this.#launch([card.id], true, mode);
	}

	async stop() {
		if (!this.#watching) return;
		this.stopping = true;
		try {
			await stopRun(this.#watching);
			this.refusal = '';
		} catch (error) {
			// Usually the run finished between the look and the press. Shown in
			// the service's words rather than silently.
			this.refusal = refusalText(error);
		} finally {
			this.stopping = false;
		}
	}

	/** The sheet closed. A running run carries on in the bar; a result the sheet
	 * already showed is not shown again. */
	sheetShut() {
		if (this.#fromSheet && !this.running) {
			this.summary = null;
			this.#fromSheet = false;
		}
	}

	// Both ways of starting come through here; which one decides only where the
	// progress and result show.
	async #launch(ids: string[], fromSheet: boolean, mode: RunMode) {
		if (!ids.length) return;
		this.#starting = true;
		this.started = mode;
		this.refusal = '';
		this.summary = null;
		this.#fromSheet = fromSheet;
		try {
			const answer = await runTitles(ids, mode);
			this.#watching = answer.run;
			this.#watchedAt = Date.now();
			// Fetch the snapshot now rather than at the next idle look; `warming`
			// covers the gap.
			this.#snapshot.prod();
		} catch (error) {
			this.refusal = refusalText(error);
		} finally {
			this.#starting = false;
			this.started = '';
		}
	}

	// Read as the chain re-arms, not watched: a reactive read would rebuild the
	// timer on every answer.
	#pace(): number {
		if (this.#watching) return told(BUSY_MS);
		// A run next door is what the controls are waiting on the end of.
		if (this.otherRun || this.#options.asking()) return told(IDLE_MS);
		return told(WATCH_MS);
	}

	// What this page's run is doing, off the snapshot the page's readers share.
	async #saw({ now: activity }: Landed) {
		this.mayRewrite = activity.may_rewrite;
		this.paused = activity.paused;
		let mine = this.#watching ? activity.runs.find((run) => run.id === this.#watching) : undefined;
		// A re-check nobody here started. It is writing this grid's verdicts, so
		// the bar shows it.
		if (!this.#watching) {
			const loose = activity.runs.find((run) => run.kind === 'recheck');
			if (loose) {
				this.#watching = loose.id;
				this.#watchedAt = Date.now();
				mine = loose;
			}
		}
		const before = this.otherRun;
		this.otherRun = activity.runs.find((run) => this.#rival(run)) ?? null;
		// A rival run just ended, having written the grid's verdicts.
		if (before && !this.otherRun) this.#options.onwritten();
		// Gone from the registry means finished, unless it was only just started
		// and no snapshot knows about it yet.
		if (this.#watching && !mine && (this.running || Date.now() - this.#watchedAt > WARMING_MS)) {
			const ended = this.#watching;
			this.#watching = null;
			this.running = null;
			// Both at once: whatever shows the run holds a dead progress bar until
			// one of them lands.
			this.#options.onwritten();
			const receipt = await this.#ranTo(ended);
			// A snapshot landing while the history was read may have adopted a run
			// since, and the last one's receipt is not that one's.
			if (this.#watching) return;
			this.summary = receipt;
			if (receipt) this.#options.ondone();
			return;
		}
		if (mine) {
			this.running = mine;
			this.#ranLabel = mine.label;
			this.#ranDry = mine.dry_run;
		}
	}

	/** The summary a run left in the history, or null if it cannot be found. */
	async #ranTo(run: string): Promise<Event | null> {
		try {
			const page = await getEvents(fetch, LOOKBACK);
			return page.events.find((entry) => entry.event === 'recheck' && entry.run === run) ?? null;
		} catch {
			// The posters are the real answer; this line is commentary.
			return null;
		}
	}
}
