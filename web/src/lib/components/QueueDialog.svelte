<script lang="ts">
	import FileTitle from './FileTitle.svelte';
	import type { Runner } from './TitleSheet.svelte';
	let titleDetails: FileTitle;
	import { onMount } from 'svelte';
	import { flip } from 'svelte/animate';
	import { fade } from 'svelte/transition';
	import Sheet, { SLIDE } from './Sheet.svelte';
	import { refusalText } from '$lib/api';
	import { rowFade, rowSlide } from '$lib/motion.svelte';
	import { overlay } from '$lib/overlay';
	import {
		QueueChanging,
		queueAction,
		queueKey,
		readQueue,
		type QueueItem,
		type QueuePage
	} from '$lib/queue';
	import QueueFile from './QueueFile.svelte';
	import PauseMenu from './PauseMenu.svelte';
	import Reveal from './Reveal.svelte';
	import SelectButton from './SelectButton.svelte';
	import Glyph from './Glyph.svelte';
	import { button, control, quietInline, radius } from '$lib/controls';

	let {
		admin = false,
		runner,
		onclose,
		onchanged
	}: { admin?: boolean; runner?: Runner; onclose: () => void; onchanged: () => void } = $props();
	let open = $state(false);
	let search = $state('');
	let visible = $state(50);
	let data = $state<QueuePage>({
		items: [],
		total: 0,
		matched: 0,
		offset: 0,
		epoch: '',
		revision: 0,
		plans_current: true
	});
	let selected = $state<Record<string, QueueItem>>({});
	// Whether a tap on a cover picks rather than opens, as the grid's own mode
	// works. Off until asked for, so a reader who only wants one file's menu is
	// offered no ticks.
	let picking = $state(false);
	let busy = $state(false);
	let loading = $state(true);
	let error = $state('');
	let message = $state('');
	let undo = $state<string | null>(null);
	let generation = 0;
	// Whether the list has moved under the header, which is all the shadow needs.
	let scrolled = $state(false);
	const choices = $derived(Object.values(selected));

	// The whole queue, or the part a search or a half-read page narrowed it to.
	const counted = $derived.by(() => {
		if (!data.matched) return search ? 'no matches' : '0 waiting';
		if (data.items.length < data.matched)
			return `1–${data.items.length} of ${data.matched}${search ? ' matching' : ''}`;
		return search ? `${data.matched} matching` : `${data.total} waiting`;
	});
	const sheet = overlay({
		name: 'queue',
		close: () => {
			// Back and Escape step out of picking first. The entry goes back, so the
			// sheet keeps its place in history.
			if (picking) {
				stopPicking();
				sheet.raise();
				return;
			}
			open = false;
			setTimeout(onclose, SLIDE);
		}
	});

	// The sheet going for good rather than that step back.
	function leave() {
		stopPicking();
		sheet.lower();
	}

	async function refresh(q = search, count = visible) {
		const ticket = ++generation;
		loading = true;
		try {
			// The whole visible prefix, read together. A read that cannot be made
			// coherent leaves the rows already on screen alone.
			const next = await readQueue(q, count, () => ticket === generation);
			if (!next) return;
			data = next;
			error = '';
		} catch (failure) {
			if (ticket === generation)
				error = failure instanceof QueueChanging ? failure.message : refusalText(failure);
		} finally {
			if (ticket === generation) loading = false;
		}
	}
	$effect(() => {
		const q = search,
			count = visible;
		const timer = setTimeout(() => refresh(q, count), 200);
		return () => clearTimeout(timer);
	});
	onMount(() => {
		open = true;
		sheet.raise();
		const timer = setInterval(() => {
			if (!busy && !loading) void refresh();
		}, 5000);
		return () => {
			generation++;
			clearInterval(timer);
		};
	});
	function pick(item: QueueItem) {
		const key = queueKey(item);
		const next = { ...selected };
		if (next[key]) delete next[key];
		else if (choices.length < 1000) next[key] = item;
		selected = next;
	}
	// A cover held and released in place: picking starts with that file ticked.
	function hold(item: QueueItem) {
		const key = queueKey(item);
		picking = true;
		if (!selected[key]) selected = { ...selected, [key]: item };
	}
	// Leaving picking drops what was picked, or a later press would act on files
	// the reader can no longer see ticked.
	function stopPicking() {
		picking = false;
		selected = {};
	}
	// Everything read so far, which is not the whole queue once it is paged.
	function pickShown() {
		const next = { ...selected };
		for (const item of data.items) if (Object.keys(next).length < 1000) next[queueKey(item)] = item;
		selected = next;
	}
	async function act(action: 'top' | 'pause' | 'skip' | 'undo', items = choices, seconds?: number) {
		busy = true;
		error = '';
		try {
			const answer = await queueAction(action, items, { seconds, token: undo ?? undefined });
			undo = answer.undo ?? null;
			const count = answer.moved ?? answer.changed ?? 0;
			message =
				action === 'undo'
					? 'Queue order restored for files still waiting.'
					: count
						? `${count} ${count === 1 ? 'file' : 'files'} ${action === 'top' ? 'moved to top' : action === 'pause' ? 'paused' : 'skipped'}.`
						: 'Those files are no longer waiting.';
			selected = {};
			visible = 50;
			await refresh(search, 50);
			onchanged();
		} catch (failure) {
			error = refusalText(failure);
			throw failure;
		} finally {
			busy = false;
		}
	}
	const quiet = (action: 'top' | 'skip' | 'undo') => {
		void act(action).catch(() => {});
	};
</script>

<Sheet {open} onclose={leave} label={`Queue ${data.total}`}>
	<!-- Tall as its content up to the cap, not always the cap: a short queue sits
	     low, where the thumb is, and Sheet's own growth carry eases the top edge
	     when rows or the action bar change it. A fixed height defeated both. -->
	<div
		class="flex max-h-[min(44rem,calc(88dvh-1.5rem))] min-h-0 flex-col sm:max-h-[min(56rem,calc(88dvh-1.5rem))]"
	>
		<!-- Raised over the list, so the shadow it casts once the tray scrolls under
		     it lands on the rows rather than behind them. The white hairline is for
		     the dark palettes, where black over a near-black tray shows nothing; on
		     a light one it falls white on white. -->
		<div
			class={`relative z-10 shrink-0 transition-shadow duration-200 ${scrolled ? 'shadow-[0_6px_14px_-2px_rgb(0_0_0/0.3),0_1px_0_rgb(255_255_255/0.08)]' : ''}`}
		>
			<header class="px-4">
				<div class="flex items-center gap-3">
					<h2 id="queue-title" class="flex items-center gap-2 text-lg font-semibold tracking-tight">
						<span class="flex text-dim"><Glyph name="list" size={18} /></span>
						<span
							>Queue <span class="ml-1 text-sm font-normal text-faint tabular-nums">{counted}</span
							></span
						>
					</h2>
				</div>
				<p class="mt-2 text-[12px] leading-relaxed text-dim">
					Files start in queue order as workers become available.
				</p>
			</header>
			<div class="space-y-2 px-4 py-3">
				<!-- Picking sits beside what narrows the list, as it does over the
				     grid: the two together are what the reader is acting on. -->
				<div class="flex items-center gap-2 sm:gap-3">
					<input
						type="search"
						aria-label="Search queue"
						placeholder="Search titles or file paths"
						value={search}
						oninput={(event) => {
							search = event.currentTarget.value;
							visible = 50;
							selected = {};
						}}
						class={`${control} ${radius} min-w-0 flex-1 border border-line-strong bg-sunken px-3 text-base sm:text-sm`}
					/>
					{#if admin}
						<SelectButton
							{picking}
							what="files"
							disabled={!data.items.length && !picking}
							onclick={() => (picking ? stopPicking() : (picking = true))}
						/>
					{/if}
				</div>
				{#if !data.plans_current}
					<p class="text-[12px] leading-relaxed text-dim">
						The rules have changed since these files were checked, so each is checked again before
						it is processed.
					</p>
				{/if}
				{#if message}<p role="status" class="text-[12px] text-dim">
						{message}
						{#if undo}<button
								class="ml-2 min-h-8 font-medium text-accent underline"
								disabled={busy}
								onclick={() => quiet('undo')}>Undo</button
							>{/if}
					</p>{/if}
				{#if error}<p role="alert" class="text-[12px] text-danger">
						{error} <button class="ml-2 underline" onclick={() => refresh()}>Retry</button>
					</p>{/if}
			</div>
			<!-- Over the list, not under the thumb: these three throw work away, and
			     the bottom of the sheet is where a finger expects a row. On its own
			     height, or the list under it jumps a chin's worth. -->
			<Reveal when={admin && picking} class="px-4 pb-3">
				<div class="mb-2 flex min-h-8 flex-wrap items-center justify-between gap-x-2 text-[12px]">
					<!-- What to do next while nothing is ticked, as the grid's bar
					     asks for a title. -->
					<span class="font-medium"
						>{choices.length
							? `${choices.length} ${choices.length === 1 ? 'file' : 'files'} selected`
							: 'Select a file'}</span
					>
					<!-- The pair the grid's bar carries, in its order: what widens the
					     selection, then what drops it. -->
					<div class="flex flex-none items-center gap-1">
						<button
							type="button"
							class={quietInline}
							disabled={busy || !data.items.length}
							onclick={pickShown}>Select shown ({data.items.length})</button
						>
						{#if choices.length}
							<button
								type="button"
								class={quietInline}
								disabled={busy}
								onclick={() => (selected = {})}>Clear</button
							>
						{/if}
					</div>
				</div>
				<div class="grid grid-cols-3 gap-2">
					<button
						class={`${button} !min-h-11 !px-2`}
						disabled={busy || loading || !!error || !choices.length}
						onclick={() => quiet('top')}>Prioritise</button
					>
					<PauseMenu
						label="selected files"
						disabled={busy || loading || !!error || !choices.length}
						onchoose={(seconds) => act('pause', choices, seconds)}
						class={`${button} !min-h-11 !px-2`}
					/>
					<button
						class={`${button} !min-h-11 !px-2`}
						disabled={busy || loading || !!error || !choices.length}
						onclick={() => quiet('skip')}>Skip</button
					>
				</div>
			</Reveal>
		</div>
		<FileTitle bind:this={titleDetails} {runner} />
		<!-- The list runs to the sheet's edge, so it carries the inset the footer
		     used to. -->
		<div
			class="min-h-0 flex-1 overflow-y-auto p-1.5 pb-[max(0.375rem,env(safe-area-inset-bottom))]"
			aria-busy={loading}
			onscroll={(event) => (scrolled = event.currentTarget.scrollTop > 0)}
		>
			<!-- One well for the whole list, as the overview panel draws its rows. -->
			<div class="rounded-xl border border-line bg-sunken p-3 sm:p-4">
				<ul class="space-y-2">
					{#each data.items as item (queueKey(item))}
						<li animate:flip={rowSlide()} in:fade={rowFade()} out:fade={rowFade()}>
							<QueueFile
								{item}
								cover={data.covers?.[item.path]}
								plan={data.plans?.[item.path]}
								current={data.plans_current}
								onopen={() => titleDetails.show(item.path, data.covers?.[item.path])}
								{admin}
								{picking}
								selected={!!selected[queueKey(item)]}
								disabled={busy || !!error}
								onpick={() => pick(item)}
								onhold={() => hold(item)}
								ontop={item.position === 1 ? undefined : () => act('top', [item])}
								onskip={() => act('skip', [item])}
								onpause={(seconds) => act('pause', [item], seconds)}
							/>
						</li>
					{/each}
				</ul>
				{#if !data.items.length}<p class="py-12 text-center text-sm text-dim">
						{loading
							? 'Reading queue…'
							: search
								? 'No queued files match your search.'
								: 'No files waiting.'}
					</p>{/if}
				{#if data.items.length < data.matched}
					<div class="mt-3 flex justify-center">
						<button class={button} disabled={busy || loading} onclick={() => (visible += 50)}>
							{loading ? 'Reading queue…' : 'Show more'}
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
</Sheet>
