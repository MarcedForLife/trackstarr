<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import ActivityFeed from '$lib/components/ActivityFeed.svelte';
	import LibraryStrip from '$lib/components/LibraryStrip.svelte';
	import Page from '$lib/components/Page.svelte';
	import ServicePanel from '$lib/components/ServicePanel.svelte';
	import TitleSheet from '$lib/components/TitleSheet.svelte';
	import { Snapshot } from '$lib/activity.svelte';
	import { getEvents, LOOKBACK } from '$lib/events';
	import type { Card } from '$lib/library';
	import { poll } from '$lib/poll';
	import { Recheck } from '$lib/recheck.svelte';
	import type { PageProps } from './$types';

	// Three sections for three questions: what the service is doing, what the
	// library has come to, what has happened lately. The page holds what more
	// than one needs: the history and the sheet a poster opens.
	let { data }: PageProps = $props();

	const admin = $derived(page.data.user?.role === 'admin');

	// Read at load and whenever a run leaves the snapshot, since the lines worth
	// showing are written as things finish. The cards come with them, for the
	// posters the feed's lines carry.
	let recent = $derived(data.recent);
	let titles = $derived(data.titles);

	// How often the feed is re-read under a run.
	const FEED_MS = 10000;

	// Quietly: a failed history read is not worth an alarm.
	async function reread() {
		try {
			const found = await getEvents(fetch, LOOKBACK);
			recent = found.events;
			titles = found.titles ?? {};
		} catch {
			/* the lines already on screen are still true */
		}
	}

	// No rhythm of its own: read when the service says so and when the panel
	// notices a run moved. The gap holds a sweep's line-a-second to a redrawable
	// pace.
	const history = poll({ ask: reread, gap: FEED_MS, kinds: ['events'] });

	onDestroy(history.stop);

	// A poster opens the library's sheet here, rather than leaving the dashboard.
	// It runs the title it holds, as it does on the library: the same sheet
	// should not offer less for having been opened from here.
	let sheet: TitleSheet;
	let strip = $state<LibraryStrip>();
	// An open sheet is a reason to keep asking whether a run may start.
	let sheetUp = $state(false);

	// One reading of the service for the page, seeded from the load so both its
	// readers have an answer before the first look: the panel draws it, the
	// re-check below watches the run a sheet starts.
	// svelte-ignore state_referenced_locally
	const snapshot = new Snapshot(data.activity);

	const recheck: Recheck = new Recheck(snapshot, {
		asking: () => sheetUp,
		// A run wrote new verdicts, so the shelf and the feed are both a run out
		// of date.
		onwritten: () => {
			strip?.look(true);
			history.now();
		},
		// The sheet shows what the run came to in its own panel, and one already
		// shut has nowhere on this page to show it.
		ondone: () => {}
	});

	function look(card: Card) {
		sheetUp = true;
		sheet.open(card);
		// Whether a run can start is the service's to answer.
		recheck.prod();
	}

	// The sheet has gone; a run it started carries on without it.
	function sheetShut() {
		sheetUp = false;
		recheck.sheetShut();
	}

	// A run moved or ended: the history and the shelf are out of date, urgently
	// for one that ended.
	function moved(ended: boolean) {
		if (ended) {
			history.now();
			strip?.look(true);
		} else {
			history.prod();
			strip?.look();
		}
	}
</script>

<Page wide title="Overview">
	<ServicePanel {snapshot} {admin} onmoved={moved} onpressed={history.now} />

	<!-- One column at every width: side by side the two read as half-empty boxes
	     and the shelf was cut to five posters. -->
	<div class="mt-6 flex flex-col gap-6 lg:gap-8">
		<LibraryStrip bind:this={strip} seed={data.library} onopen={look} />
		<ActivityFeed entries={recent} {titles} onopen={look} />
	</div>
</Page>

<!-- The library's own sheet, runs and all. -->
<TitleSheet bind:this={sheet} runner={admin ? recheck.runner : undefined} onshut={sheetShut} />
