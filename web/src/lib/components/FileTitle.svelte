<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import { request, refusalText } from '$lib/api';
	import type { Card } from '$lib/library';
	import type { FileCover } from '$lib/queue';
	import { portal } from '$lib/portal';
	import { SLIDE } from './Sheet.svelte';
	import TitleSheet, { type Runner } from './TitleSheet.svelte';

	// What running this title takes, from the page that owns the runs.
	let { runner }: { runner?: Runner } = $props();

	let mounted = $state(false);
	let error = $state('');
	let loading = $state(false);
	let sheet = $state<TitleSheet>();
	let trigger: HTMLElement | null = null;
	// The unmount waiting on a shut sheet's slide out.
	let unmounting: ReturnType<typeof setTimeout> | null = null;
	let gone = false;
	onDestroy(() => {
		gone = true;
		if (unmounting !== null) clearTimeout(unmounting);
	});

	/** The details of the title this file belongs to. The row's own cover names
	 * that title, so the sheet comes up on the tap and the card fills its header
	 * in when it lands. */
	export async function show(path: string, known?: FileCover) {
		if (loading) return;
		// Tapped again while the last slides out: the sheet stays up for the new
		// title.
		if (unmounting !== null) {
			clearTimeout(unmounting);
			unmounting = null;
		}
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
		// `onshut` comes as the slide out starts. Unmounted then, the sheet
		// vanished instead of sliding, so it goes once the slide has run.
		unmounting = setTimeout(() => {
			unmounting = null;
			mounted = false;
		}, SLIDE);
		void tick().then(() => trigger?.isConnected && trigger.focus());
	}
</script>

{#if error}<p role="alert" class="px-4 py-2 text-[12px] text-danger">{error}</p>{/if}
{#if loading}<span role="status" class="sr-only">Opening title details…</span>{/if}
<!-- A title opened over a queue must be outside its transformed sheet, and above
     its inert backdrop. Mounted only on demand and kept until its slide out has
     run, then the normal sheet. -->
{#if mounted}
	<div use:portal><TitleSheet bind:this={sheet} {runner} onshut={closed} /></div>
{/if}
