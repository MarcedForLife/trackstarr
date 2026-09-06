<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import ActivityFeed from '$lib/components/ActivityFeed.svelte';
	import LibraryStrip from '$lib/components/LibraryStrip.svelte';
	import Page from '$lib/components/Page.svelte';
	import ServicePanel from '$lib/components/ServicePanel.svelte';
	import TitleSheet from '$lib/components/TitleSheet.svelte';
	import { getEvents, LOOKBACK } from '$lib/events';
	import { poll } from '$lib/poll';
	import type { PageProps } from './$types';

	// Three sections for three questions: what the service is doing, what the
	// library has come to, what has happened lately. The page holds what more
	// than one needs: the history and the sheet a poster opens.
	let { data }: PageProps = $props();

	const admin = $derived(page.data.user?.role === 'admin');

	// Read at load and whenever a run leaves the snapshot, since the lines worth
	// showing are written as things finish.
	let recent = $derived(data.recent);

	// The last sweep to finish. Newest first, so the first found is the latest.
	const swept = $derived(recent.find((entry) => entry.event === 'sweep'));
	// Not repeated in the feed, or it reads as two sweeps.
	const feed = $derived(recent.filter((entry) => entry !== swept));

	// How often the feed is re-read under a run.
	const FEED_MS = 10000;

	// Quietly: a failed history read is not worth an alarm.
	async function reread() {
		try {
			recent = (await getEvents(fetch, LOOKBACK)).events;
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
	let sheet: TitleSheet;
	let strip = $state<LibraryStrip>();

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
	<ServicePanel
		seed={data.activity}
		{admin}
		{recent}
		{swept}
		onmoved={moved}
		onpressed={history.now}
	/>

	<!-- One column at every width: side by side the two read as half-empty boxes
	     and the shelf was cut to five posters. -->
	<div class="mt-6 flex flex-col gap-6 lg:gap-8">
		<LibraryStrip bind:this={strip} seed={data.library} onopen={(chosen) => sheet.open(chosen)} />
		<ActivityFeed entries={feed} />
	</div>
</Page>

<TitleSheet bind:this={sheet} />
