<script lang="ts">
	// One line over every page of the demo build, so nobody mistakes the sample
	// library for a real one or expects a change to outlive the tab. Reloading
	// is the reset: the state is rebuilt from the catalogue. The credits name
	// every poster and still shown, as their licences ask.
	import { credits } from '$lib/demo/posters';
	import { request, refusalText } from '$lib/api';
	import { page } from '$app/state';
	import { button } from '$lib/controls';
	import Glyph from '$lib/components/Glyph.svelte';
	import Reveal from '$lib/components/Reveal.svelte';
	import Segmented from '$lib/components/Segmented.svelte';

	let dismissed = $state(false);
	let debugging = $state(false);
	let sending = $state(false);
	let feedback = $state('');
	let arr = $state('radarr');
	let debugToggle: HTMLButtonElement | undefined = $state();

	// The panel's own control unmounts with it, so hand focus back to the link
	// that opened it rather than dropping it on the body.
	function stopDebugging() {
		debugging = false;
		debugToggle?.focus();
	}

	async function send(path: string, body: object, say: (titles: string[]) => string) {
		sending = true;
		feedback = '';
		try {
			const result = await request<{ titles: string[] }>(path, {
				method: 'POST',
				body: JSON.stringify(body)
			});
			feedback = say(result.titles);
		} catch (error) {
			feedback = refusalText(error);
		} finally {
			sending = false;
		}
	}

	const imported = (titles: string[]) =>
		`Imported ${titles.join(' and ')} · 4K, surround audio, no stereo.`;

	// In the order they read as a tour: the opening board, then the three that
	// pose something it cannot show on its own.
	const BOARDS = [
		{
			name: 'multiple-variants',
			label: 'Multiple variants',
			said: (titles: string[]) =>
				`${titles.join(' and ')} held by a 4K instance as well. Open one to compare the files.`
		},
		{
			name: 'large-series',
			label: 'Large series',
			said: (titles: string[]) =>
				`${titles.join(' and ')} now has 1,000 episodes across 40 seasons.`
		},
		{
			name: 'connection-trouble',
			label: 'Connection trouble',
			said: () =>
				'Radarr is unreachable; Sonarr has a missing root and a failed callback. Open Connections to inspect. Full board restores healthy connections.'
		},
		{ name: 'full', label: 'Full board', said: () => 'Back to the opening board.' },
		{ name: 'imports-only', label: 'Imports only', said: imported },
		{
			name: 'failed',
			label: 'Failed rewrite',
			said: (titles: string[]) => `The rewrite of ${titles.join(' and ')} broke.`
		},
		{ name: 'held', label: 'Paused', said: () => 'Everything held, with the sweep waiting.' }
	];

	const shown = credits();
	let crediting = $state(false);

	const ARRS = [
		{ value: 'radarr', label: 'Radarr' },
		{ value: 'sonarr', label: 'Sonarr' }
	];

	const link =
		'flex-none font-medium text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg';

	const heading = 'text-[11px] font-semibold tracking-wider text-faint uppercase';
</script>

<!-- Every part of the line opens and closes on its own height, so the page
     under it travels instead of jumping a panel's worth. -->
<Reveal when={!dismissed}>
	<div
		class="border-b border-demo/30 bg-demo/12 pt-[env(safe-area-inset-top)] text-[12.5px] text-dim lg:pt-0"
		role="note"
	>
		<!-- touch-none: a drag on the line is not a scroll, so it cannot pull the
		     page to a refresh. The credits below scroll as text does. -->
		<div class="flex touch-none items-center gap-3 px-4 py-1.5">
			<span class="min-w-0 flex-1 truncate">
				<span class="font-semibold text-demo">Demo.</span>
				A sample library of openly licensed films. Nothing here is real, and nothing is saved past a reload.
			</span>
			{#if page.data.user?.role === 'admin'}
				<button
					type="button"
					bind:this={debugToggle}
					onclick={() => (debugging = !debugging)}
					aria-expanded={debugging}
					aria-controls="demo-debug"
					class={link}>Debug</button
				>
			{/if}
			{#if shown.length}
				<button
					type="button"
					onclick={() => (crediting = !crediting)}
					aria-expanded={crediting}
					class={link}
				>
					Credits
				</button>
			{/if}
			<button type="button" onclick={() => location.reload()} class={link}>Start over</button>
			<!-- Negative margins buy a 44px target without the line growing to hold
			     one. A reload brings the bar back, which is the demo's reset anyway. -->
			<button
				type="button"
				onclick={() => (dismissed = true)}
				aria-label="Dismiss the demo notice"
				class="-my-2.5 -mr-2 flex-none rounded px-2 py-4 text-faint hover:text-fg"
			>
				<Glyph name="cross" />
			</button>
		</div>
		<Reveal when={debugging && page.data.user?.role === 'admin'}>
			<div id="demo-debug" class="border-t border-demo/25 px-4 py-3">
				<!-- A row each, since a scenario poses the whole board rather than
				     delivering from the service picked above. Under one heading the
				     four controls wrapped ragged on a phone and read as one action. -->
				<h3 id="demo-webhook" class={heading}>Webhook</h3>
				<div class="mt-1.5 flex gap-2">
					<Segmented
						options={ARRS}
						value={arr}
						disabled={sending}
						labelledBy="demo-webhook"
						onchange={(value) => (arr = value)}
					/>
					<button
						class={`${button} flex-none`}
						disabled={sending}
						onclick={() => send('/api/demo/import', { arr }, imported)}
					>
						Import
					</button>
				</div>
				<p class="mt-1.5">A 4K replacement missing stereo, alongside whatever is running.</p>

				<h3 class={`${heading} mt-3`}>Scenarios</h3>
				<!-- Two by two on a phone; loose in a row they wrapped three then one. -->
				<div class="mt-1.5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
					{#each BOARDS as board (board.name)}
						<button
							class={`${button} sm:flex-none`}
							disabled={sending}
							onclick={() => send('/api/demo/scenario', { name: board.name }, board.said)}
						>
							{board.label}
						</button>
					{/each}
				</div>
				<p class="mt-1.5">
					Each rebuilds the board from the catalogue: the sweep the demo opens on, imports only, a
					rewrite that broke, everything held, separate variants, a large series, or connection
					trouble. Each scenario resets the previous one.
				</p>
				<!-- The Debug link is off the top of a phone by the time the panel is
				     read, so the way out shares the last line with what came back. -->
				<div class="mt-2 flex items-center gap-3">
					<p role="status" class="min-w-0 flex-1 text-fg">{feedback}</p>
					<button
						type="button"
						onclick={stopDebugging}
						aria-label="Collapse the debug panel"
						class="-my-2.5 -mr-2 flex-none rounded px-2 py-4 text-faint hover:text-fg"
					>
						<span class="flex -rotate-90"><Glyph name="chevron" /></span>
					</button>
				</div>
			</div>
		</Reveal>
		<Reveal when={crediting}>
			<p class="px-4 pb-2 leading-relaxed">
				Artwork from Wikimedia Commons, each under its own licence:
				{#each shown as credit, at (credit.id)}
					<!-- An address on Commons, not a route of ours. -->
					<!-- eslint-disable svelte/no-navigation-without-resolve -->
					<a
						href={credit.url}
						target="_blank"
						rel="noreferrer"
						class="text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"
						>{credit.name}</a
					>
					<!-- eslint-enable svelte/no-navigation-without-resolve -->
					<span class="whitespace-nowrap"
						>({credit.by}, {credit.licence}){at < shown.length - 1 ? ' · ' : '.'}</span
					>
				{/each}
				The files, verdicts and history are invented.
			</p>
		</Reveal>
	</div>
</Reveal>
