<script lang="ts">
	import { resolve } from '$app/paths';
	import DirList from '$lib/components/DirList.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, field, noteBox, picker } from '$lib/controls';
	import { provideSettings, SettingsDraft } from '$lib/draft.svelte';
	import { ago } from '$lib/events';
	import { clearVerdicts, refreshRatings } from '$lib/library';
	import { PRESETS, checkSweep, runLabel, type ScheduleCheck } from '$lib/sweep';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readOnly = $derived(data.user?.role !== 'admin');

	// The walk order is the setting's order, so a reorder must read as a change.
	// svelte-ignore state_referenced_locally
	const settings = new SettingsDraft(data.snapshot.settings, {
		ordered: new Set(['MEDIA_DIRS']),
		readOnly: () => readOnly
	});
	// The rows below name a setting and read the rest off the draft.
	provideSettings(settings);
	// Both records are mutated in place, so these read the live objects after a
	// save.
	const draft = settings.draft;
	const baseline = settings.baseline;
	const envLocked = (name: string) => settings.envLocked(name);

	const schedule = $derived(((draft.SWEEP_AT as string) ?? '').trim());
	const scheduled = $derived(!!schedule);

	// The saved zone: the check must answer on the one the scheduler will obey.
	const zone = $derived(((baseline.TZ?.value as string) ?? '').trim());

	// Empty when the expression is no preset, which the picker shows as Custom.
	const preset = $derived(PRESETS.some((option) => option.value === schedule) ? schedule : '');

	// So toggling the schedule off and on keeps the expression.
	let remembered = $state(((draft.SWEEP_AT as string) ?? '').trim() || PRESETS[0].value);

	function setScheduled(on: boolean) {
		if (on) {
			draft.SWEEP_AT = remembered;
		} else {
			remembered = schedule || remembered;
			draft.SWEEP_AT = '';
		}
	}

	// What the scheduled sweep does is General's mode setting. Shown here, since
	// it is what anyone reads this page for.
	const mode = $derived(((baseline.REWRITE_MODE?.value as string) ?? 'imports').trim());

	const MODE_NOTE: Record<string, string> = {
		report: 'plans every file and writes /config/pending.tsv, rewriting nothing',
		imports: 'plans every file and writes /config/pending.tsv, rewriting nothing',
		all: 'rewrites what it finds'
	};

	// Admin-only: a viewer would collect a 403 per keystroke.
	let checked = $state<ScheduleCheck | null>(null);
	let checking = $state(false);
	let asked = 0;

	async function runCheck(at: string, paths: string[], tz: string) {
		const mine = ++asked;
		checking = true;
		try {
			const result = await checkSweep(at, paths, tz);
			// A slower earlier answer must not land on top of a newer one.
			if (mine === asked) checked = result;
		} catch {
			if (mine === asked) checked = null;
		} finally {
			if (mine === asked) checking = false;
		}
	}

	// Only these three reach the check. The directories come off the draft, with
	// blank rows already taken out.
	const question = $derived({
		at: schedule,
		dirs: (draft.MEDIA_DIRS as string[]) ?? [],
		tz: zone
	});

	$effect(() => {
		const { at, dirs: paths, tz } = question;
		if (readOnly) return;
		// Debounced: the answer is only worth having once typing pauses.
		const timer = setTimeout(() => runCheck(at, paths, tz), 400);
		return () => clearTimeout(timer);
	});

	const answers = $derived(new Map((checked?.dirs ?? []).map((entry) => [entry.path, entry])));

	// How many titles carry an IMDb score and when the table was rebuilt, seeded
	// like the verdict count. Null when the service could not be asked.
	let scores = $derived(data.ratings);
	let fetching = $state(false);
	// What the last press came to, kept until the next.
	let fetchNote = $state('');

	// The saved switch: the fetch acts on what the service is running.
	const scoresOn = $derived(!!baseline.IMDB_RATINGS?.value);
	const scoresPending = $derived(!!draft.IMDB_RATINGS !== scoresOn);

	const scoresNote = $derived.by(() => {
		if (scoresPending) return 'Save the switch above first.';
		if (!scoresOn) return 'Switch the scores on to fetch them.';
		if (!scores) return 'The score count is not known yet.';
		if (!scores.fetched) return 'Nothing fetched yet. The next daily pass will do it.';
		const when = ago(new Date(scores.fetched * 1000).toISOString());
		return `${scores.scored.toLocaleString()} titles scored, last fetched ${when}. Refreshed once a day on its own.`;
	});

	async function fetchScores() {
		fetching = true;
		fetchNote = '';
		try {
			const result = await refreshRatings();
			scores = result;
			fetchNote = `${result.scored.toLocaleString()} of ${result.titles.toLocaleString()} titles have a score.`;
		} catch (err) {
			fetchNote = refusalText(err);
		} finally {
			fetching = false;
		}
	}

	// How many verdicts are on file. The clear button answers with what it
	// dropped.
	let stored = $derived(data.library?.swept ?? 0);
	// Pressed once arms it, again does it: one tap is too little between a thumb
	// and a full re-probe.
	let arming = $state(false);
	let clearing = $state(false);
	// What the service said, kept until the next press: a message that leaves
	// after three seconds was never read.
	let cleared = $state('');

	function disarm() {
		arming = false;
	}

	async function clear() {
		if (!arming) {
			cleared = '';
			arming = true;
			return;
		}
		clearing = true;
		try {
			const { dropped } = await clearVerdicts();
			stored = 0;
			cleared = dropped
				? `${dropped.toLocaleString()} verdicts cleared. The next sweep works them out again.`
				: 'There was nothing stored to clear.';
		} catch (err) {
			cleared = refusalText(err);
		} finally {
			clearing = false;
			arming = false;
		}
	}

	// What the service made of the typed expression, about the entry rather than
	// the setting.
	const id = $props.id();
	const cronNote = `${id}-cron`;
</script>

<Page
	eyebrow="Settings"
	title="Sweep"
	lead="The walk over the whole library, catching files that arrived without a webhook."
>
	<!-- Inert while a save is in flight, so the response cannot land on a
	     keystroke it never carried. min-w-0, or a fieldset will not shrink. -->
	<fieldset disabled={settings.busy} class="min-w-0">
		{#if readOnly}
			<p class="mt-2 text-[13px] text-faint">Viewing only. An admin can change these.</p>
		{/if}

		<section class="mt-7">
			<p class="pb-2 text-[13px] font-semibold">Schedule</p>
			<!-- The check below only answers for an admin with a schedule on. -->
			<p class="pb-1 text-[13px] text-dim">
				Read on the {zone || 'UTC'} clock, set by the
				<a href={resolve('/settings')} class="text-accent underline underline-offset-2">
					time zone under General
				</a>.
			</p>
			<SettingRow
				name="SWEEP_AT"
				label="Scheduled sweep"
				desc="Off, the library is only swept by hand with trackstarr sweep. Webhook imports are rewritten as they land regardless."
			>
				{#snippet children({ labelledBy, describedBy })}
					<Toggle
						on={scheduled}
						{labelledBy}
						{describedBy}
						disabled={envLocked('SWEEP_AT')}
						onchange={setScheduled}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				name="SWEEP_AT"
				label="Runs at"
				align="start"
				desc="A five-field cron schedule: minute, hour, day of month, month, day of week. Pick one from the list or write your own."
				nested
				dim={!scheduled}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<!-- Two controls for one setting, each named, both described by the
					     row's sentence. -->
					{@const answered =
						scheduled && !readOnly && (checked ? !checked.ok || !!checked.runs.length : checking)}
					{@const said = answered ? `${describedBy} ${cronNote}` : describedBy}
					<div
						role="group"
						aria-labelledby={labelledBy}
						aria-describedby={describedBy}
						class="flex w-full flex-col gap-2 sm:w-72"
					>
						<!-- Common schedules by name. Custom is shown, never picked. -->
						<select
							value={schedule}
							onchange={(event) => (draft.SWEEP_AT = event.currentTarget.value)}
							aria-label="Common schedules"
							aria-describedby={describedBy}
							disabled={envLocked('SWEEP_AT') || !scheduled}
							class={picker}
						>
							{#if !preset}
								<!-- Carries the expression, so picking it again is a no-op. -->
								<option value={schedule}>{schedule ? 'Custom' : ''}</option>
							{/if}
							{#each PRESETS as option (option.value)}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
						<input
							value={(draft.SWEEP_AT as string) ?? ''}
							oninput={(event) => (draft.SWEEP_AT = event.currentTarget.value)}
							placeholder="0 4 * * *"
							inputmode="text"
							autocapitalize="none"
							autocorrect="off"
							spellcheck="false"
							aria-label="Sweep schedule"
							aria-describedby={said}
							disabled={envLocked('SWEEP_AT') || !scheduled}
							class={field}
						/>
						<!-- From the service, with the scheduler's parser and clock. -->
						{#if scheduled && !readOnly}
							{#if checked && !checked.ok}
								<p id={cronNote} class={`${noteBox} text-danger`}>{checked.error}</p>
							{:else if checked?.runs.length}
								<div id={cronNote} class={`${noteBox} text-dim`}>
									<p class="text-faint">
										Next runs{checked.zone ? `, ${checked.zone}` : ''}
									</p>
									{#each checked.runs as run (run)}
										<p class="mt-0.5 font-mono">{runLabel(run)}</p>
									{/each}
								</div>
							{:else if checking}
								<p id={cronNote} class={`${noteBox} text-faint`}>Checking…</p>
							{/if}
						{/if}
					</div>
				{/snippet}
			</SettingRow>
			{#if scheduled}
				<p class={`mt-3 ${noteBox} text-dim`}>
					A scheduled sweep {MODE_NOTE[mode] ?? MODE_NOTE.imports}, which is
					<a href={resolve('/settings')} class="text-accent underline underline-offset-2">
						what gets rewritten under General
					</a>.
				</p>
			{/if}
		</section>

		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Library</p>
			<SettingRow
				name="MEDIA_DIRS"
				label="Media directories"
				align="start"
				desc="Where the sweep walks, as paths inside this container. Everything under each is judged."
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<DirList {settings} {answers} {labelledBy} {describedBy} />
				{/snippet}
			</SettingRow>
		</section>

		<!-- Here rather than Connections: timed work over the whole library, and
	     nothing to test. -->
		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Title scores</p>
			<SettingRow
				name="IMDB_RATINGS"
				label="Fetch IMDb ratings"
				desc="IMDb's ratings table, so a title's sheet can show its score. One file a day covering every rated title, licensed by IMDb for personal, non-commercial use."
			>
				{#snippet children({ labelledBy, describedBy })}
					<Toggle
						on={draft.IMDB_RATINGS as boolean}
						{labelledBy}
						{describedBy}
						disabled={envLocked('IMDB_RATINGS')}
						onchange={(on) => (draft.IMDB_RATINGS = on)}
					/>
				{/snippet}
			</SettingRow>
			<!-- For the minutes after switching it on. -->
			<SettingRow label="Fetch now" desc={scoresNote} align="start" nested dim={!scoresOn}>
				{#snippet children({ labelledBy, describedBy })}
					<div class="flex flex-col items-start gap-2 sm:items-end">
						<button
							onclick={fetchScores}
							disabled={readOnly || fetching || !scoresOn || scoresPending}
							class={button}
							aria-labelledby={labelledBy}
							aria-describedby={describedBy}
						>
							{fetching ? 'Fetching…' : 'Fetch'}
						</button>
						{#if fetchNote}
							<p class="max-w-72 text-[12.5px] text-dim sm:text-right">{fetchNote}</p>
						{/if}
					</div>
				{/snippet}
			</SettingRow>
		</section>

		<!-- Last, and not on the dashboard: the one control that throws away work. -->
		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Stored verdicts</p>
			<SettingRow
				label="Clear the sweep cache"
				desc={`What the last sweep concluded about every file it walked${
					stored ? `, which is ${stored.toLocaleString()} of them` : ''
				}. Clearing it makes the next sweep re-probe everything and empties the library until then. History, run tallies and settings are kept.`}
				align="start"
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<!-- The row's sentence carries the cost. -->
					<div
						role="group"
						aria-labelledby={labelledBy}
						aria-describedby={describedBy}
						class="flex flex-col items-start gap-2 sm:items-end"
					>
						<div class="flex items-center gap-2">
							{#if arming}
								<!-- Cancel first, so a thumb travelling right lands on it. -->
								<button onclick={disarm} disabled={clearing} class={button}>Keep them</button>
							{/if}
							<button
								onclick={clear}
								disabled={readOnly || clearing || (!stored && !arming)}
								class={danger}
							>
								{clearing
									? 'Clearing…'
									: arming
										? `Yes, clear ${stored.toLocaleString()}`
										: 'Clear'}
							</button>
						</div>
						{#if cleared}
							<p class="max-w-72 text-[12.5px] text-dim sm:text-right">{cleared}</p>
						{/if}
					</div>
				{/snippet}
			</SettingRow>
		</section>

		<SaveBar {settings} />
	</fieldset>
</Page>
