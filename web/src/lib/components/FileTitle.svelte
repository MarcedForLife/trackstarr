<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import { request, refusalText } from '$lib/api';
	import type { Card } from '$lib/library';
	import type { FileCover } from '$lib/queue';
	import { portal } from '$lib/portal';
	import TitleSheet, { type Runner } from './TitleSheet.svelte';

	// What running this title takes, from the page that owns the runs.
	let { runner }: { runner?: Runner } = $props();

	let mounted = $state(false);
	let error = $state('');
	let loading = $state(false);
	let sheet = $state<TitleSheet>();
	let trigger: HTMLElement | null = null;
	let gone = false;
	onDestroy(() => {
		gone = true;
	});

	/** The details of the title this file belongs to. The row's own cover names
	 * that title, so the sheet comes up on the tap and the card fills its header
	 * in when it lands. */
	export async function show(path: string, known?: FileCover) {
		if (loading) return;
		trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		loading = true;
		error = '';
		if (known) {
			mounted = true;
			await tick();
			void sheet!.open(known);
		}
		try {
			const answer = await request<{ card: Card | null }>(
				`/api/library/file?${new URLSearchParams({ path, card: '1' })}`
			);
			if (gone) return;
			// A sheet already up says what went wrong itself, since its own read of
			// the title fails the same way.
			if (known) {
				if (answer.card) sheet?.fill(answer.card);
				return;
			}
			// The tap has to still stand: a row that has gone, or a sheet that closed
			// over it, is not somewhere to open a title.
			if (!trigger?.isConnected || trigger.closest('[inert]')) return;
			if (!answer.card) {
				error = 'This title is no longer in the library.';
				return;
			}
			mounted = true;
			await tick();
			await sheet!.open(answer.card);
		} catch (failure) {
			if (!known) error = refusalText(failure);
		} finally {
			loading = false;
		}
	}
	function closed() {
		mounted = false;
		void tick().then(() => trigger?.isConnected && trigger.focus());
	}
</script>

{#if error}<p role="alert" class="px-4 py-2 text-[12px] text-danger">{error}</p>{/if}
{#if loading}<span role="status" class="sr-only">Opening title details…</span>{/if}
<!-- A title opened over a queue must be outside its transformed sheet, and above
     its inert backdrop. Mounted only on demand, then the normal sheet. -->
{#if mounted}
	<div use:portal><TitleSheet bind:this={sheet} {runner} onshut={closed} /></div>
{/if}
