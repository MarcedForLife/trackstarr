<script lang="ts">
	import FileTitle from './FileTitle.svelte';
	let titleDetails: FileTitle;
	import { onMount } from 'svelte';
	import Sheet, { SLIDE } from './Sheet.svelte';
	import { refusalText } from '$lib/api';
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
	import Tick from './Tick.svelte';
	import { button } from '$lib/controls';

	let {
		admin = false,
		onclose,
		onchanged
	}: { admin?: boolean; onclose: () => void; onchanged: () => void } = $props();
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
	let busy = $state(false);
	let loading = $state(true);
	let error = $state('');
	let message = $state('');
	let undo = $state<string | null>(null);
	let generation = 0;
	// Whether the list has moved under the header, which is all the shadow needs.
	let scrolled = $state(false);
	const choices = $derived(Object.values(selected));
	const allPage = $derived(
		data.items.length > 0 && data.items.every((item) => selected[queueKey(item)])
	);
	const somePage = $derived(!allPage && data.items.some((item) => selected[queueKey(item)]));

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
			open = false;
			setTimeout(onclose, SLIDE);
		}
	});

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
	function pickPage() {
		const next = { ...selected };
		for (const item of data.items) {
			if (allPage) delete next[queueKey(item)];
			else if (Object.keys(next).length < 1000) next[queueKey(item)] = item;
		}
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

<Sheet {open} onclose={() => sheet.lower()} label={`Queue ${data.total}`}>
	<!-- Tall as its content up to the cap, not always the cap: a short queue sits
	     low, where the thumb is, and Sheet's own growth carry eases the top edge
	     when rows or the action bar change it. A fixed height defeated both. -->
	<div class="flex max-h-[min(44rem,calc(88dvh-1.5rem))] min-h-0 flex-col">
		<!-- Raised over the list, so the shadow it casts once the tray scrolls under
		     it lands on the rows rather than behind them. The white hairline is for
		     the dark palettes, where black over a near-black tray shows nothing; on
		     a light one it falls white on white. -->
		<div
			class={`relative z-10 shrink-0 transition-shadow duration-200 ${scrolled ? 'shadow-[0_6px_14px_-2px_rgb(0_0_0/0.3),0_1px_0_rgb(255_255_255/0.08)]' : ''}`}
		>
			<header class="px-4 pt-3 sm:px-6">
				<div class="flex items-center justify-between gap-3">
					<h2 id="queue-title" class="text-lg font-semibold tracking-tight">
						Queue <span class="ml-1 text-sm font-normal text-faint tabular-nums">{counted}</span>
					</h2>
					<button class={button} onclick={() => sheet.lower()}>Close</button>
				</div>
				<p class="mt-2 text-[12px] leading-relaxed text-dim">
					Files start in queue order as workers become available.
				</p>
			</header>
			<div class="space-y-2 px-4 py-3 sm:px-6">
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
					class="min-h-11 w-full rounded-lg border border-line-strong bg-sunken px-3 text-base sm:text-sm"
				/>
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
			     the bottom of the sheet is where a finger expects a row. -->
			{#if admin && choices.length}
				<div class="px-4 pb-3 sm:px-6">
					<div class="mb-2 flex min-h-8 items-center justify-between gap-2 text-[12px]">
						<span class="font-medium">{choices.length} selected</span>
						<button
							class="min-h-8 text-dim underline underline-offset-4"
							disabled={busy}
							onclick={() => (selected = {})}>Clear selection</button
						>
					</div>
					<div class="grid grid-cols-3 gap-2">
						<button
							class={`${button} !min-h-11 !px-2`}
							disabled={busy || loading || !!error}
							onclick={() => quiet('top')}>Move to top</button
						>
						<PauseMenu
							label="selected files"
							disabled={busy || loading || !!error}
							onchoose={(seconds) => act('pause', choices, seconds)}
							class={`${button} !min-h-11 !px-2`}
						/>
						<button
							class={`${button} !min-h-11 !px-2`}
							disabled={busy || loading || !!error}
							onclick={() => quiet('skip')}>Skip</button
						>
					</div>
				</div>
			{/if}
		</div>
		<FileTitle bind:this={titleDetails} />
		<!-- The list runs to the sheet's edge, so it carries the inset the footer
		     used to. -->
		<div
			class="min-h-0 flex-1 overflow-y-auto p-1.5 pb-[max(0.375rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4"
			aria-busy={loading}
			onscroll={(event) => (scrolled = event.currentTarget.scrollTop > 0)}
		>
			<!-- One well for the whole list, as the overview panel draws its rows. -->
			<div class="rounded-xl border border-line bg-sunken p-3 sm:p-4">
				{#if admin && data.items.length}
					<button
						type="button"
						role="checkbox"
						aria-checked={allPage ? true : somePage ? 'mixed' : false}
						disabled={busy || loading || !!error}
						onclick={pickPage}
						class="mb-1 flex min-h-11 w-full items-center gap-3 text-[12px] text-dim disabled:opacity-(--disabled)"
					>
						<Tick on={allPage} some={somePage} />
						Select shown ({data.items.length})
					</button>
				{/if}
				<ul class="space-y-2">
					{#each data.items as item (queueKey(item))}
						<QueueFile
							{item}
							cover={data.covers?.[item.path]}
							plan={data.plans?.[item.path]}
							current={data.plans_current}
							onopen={() => titleDetails.show(item.path, data.covers?.[item.path])}
							{admin}
							selected={!!selected[queueKey(item)]}
							disabled={busy || !!error}
							onpick={() => pick(item)}
							ontop={() => act('top', [item])}
							onskip={() => act('skip', [item])}
							onpause={(seconds) => act('pause', [item], seconds)}
						/>
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
