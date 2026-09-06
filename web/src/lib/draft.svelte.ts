// The draft every settings page edits: the snapshot it started from, the clone
// being typed into, what a save would send, and the leave guard.

import { beforeNavigate } from '$app/navigation';
import { changes as diff, cloneValues } from '$lib/draft';
import { SettingsError, saveSettings, type Setting, type SettingValue } from '$lib/settings';

export type Changes = Record<string, SettingValue | null>;

type Options = {
	// Names whose written order matters, so a reorder reads as a change.
	ordered?: Set<string>;
	// A viewer sees every page and saves none.
	readOnly?: () => boolean;
	// Names the page sends as null beyond what the diff finds: a credential is
	// never echoed back, so a cleared one is invisible to the diff.
	dropping?: () => string[];
};

// Both records keep their identity, so a page can hold a reference across a
// save.
function refill<T>(target: Record<string, T>, values: Record<string, T>) {
	for (const name of Object.keys(target)) {
		if (!(name in values)) delete target[name];
	}
	for (const [name, value] of Object.entries(values)) {
		target[name] = value;
	}
}

export class SettingsDraft {
	// The snapshot the page arrived with, and the clone it edits. An invalidation
	// mid-edit must not clobber what is being typed.
	baseline: Record<string, Setting> = $state({});
	draft: Record<string, SettingValue> = $state({});
	problems = $state<string[]>([]);
	busy = $state(false);
	// What the last save or discard answered, for a reader who cannot see the
	// bar leave. Cleared as a save starts so a repeat is still a change a screen
	// reader announces. See SaveBar.
	answered = $state('');

	#options: Options;
	#rebuilds: ((saved: Changes | null) => void)[] = [];

	changes: Changes = $derived.by(() => {
		const out = diff(this.draft, this.baseline, this.#options.ordered);
		for (const name of this.#options.dropping?.() ?? []) out[name] = null;
		return out;
	});

	count = $derived(this.#readOnly ? 0 : Object.keys(this.changes).length);

	constructor(settings: Record<string, Setting>, options: Options = {}) {
		this.#options = options;
		this.reset(settings);
		this.#guard();
	}

	get #readOnly(): boolean {
		return this.#options.readOnly?.() ?? false;
	}

	envLocked(name: string): boolean {
		return this.#readOnly || !!this.baseline[name]?.env;
	}

	desc(name: string, fallback: string): string {
		return this.baseline[name]?.env
			? `Set by ${name} in the environment, which wins over anything saved here.`
			: fallback;
	}

	/** Start again from a fresh snapshot, dropping whatever the draft held. */
	reset(settings: Record<string, Setting>) {
		refill(this.baseline, settings);
		refill(this.draft, cloneValues(settings));
		this.problems = [];
		this.answered = '';
		this.#rebuilt(null);
	}

	/**
	 * Called whenever the draft is replaced wholesale, for rows a page keeps
	 * beside it, such as half-filled path pairs. `saved` is what a save sent,
	 * null after a discard. Returns the unsubscribe, for a component that
	 * unmounts.
	 */
	onreset(rebuild: (saved: Changes | null) => void): () => void {
		this.#rebuilds.push(rebuild);
		return () => {
			this.#rebuilds = this.#rebuilds.filter((entry) => entry !== rebuild);
		};
	}

	async save() {
		const sent = { ...this.changes };
		this.busy = true;
		this.problems = [];
		this.answered = '';
		try {
			const snapshot = await saveSettings(sent);
			refill(this.baseline, snapshot.settings);
			// Only the names that went, so a keystroke during the save survives.
			const fresh = cloneValues(snapshot.settings);
			for (const [name, value] of Object.entries(fresh)) {
				if (name in sent || !(name in this.draft)) this.draft[name] = value;
			}
			for (const name of Object.keys(sent)) {
				if (!(name in fresh)) delete this.draft[name];
			}
			this.#rebuilt(sent);
			this.answered = 'Settings saved.';
		} catch (error) {
			this.problems =
				error instanceof SettingsError ? error.problems : ['Could not save the settings.'];
		} finally {
			this.busy = false;
		}
	}

	discard() {
		refill(this.draft, cloneValues(this.baseline));
		this.problems = [];
		this.answered = 'Changes discarded.';
		this.#rebuilt(null);
	}

	#rebuilt(saved: Changes | null) {
		for (const rebuild of this.#rebuilds) rebuild(saved);
	}

	// From the constructor, since `beforeNavigate` only works during init.
	#guard() {
		beforeNavigate((navigation) => {
			if (!this.count) return;
			const what = this.count === 1 ? 'One unsaved change' : `${this.count} unsaved changes`;
			// A tab closing gets the browser's own dialog: `confirm()` during an
			// unload is ignored, and cancelling triggers it.
			if (navigation.type === 'leave') {
				navigation.cancel();
				return;
			}
			if (!confirm(`${what} will be lost. Leave the page?`)) navigation.cancel();
		});
	}
}
