<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import type { FileActionAdapter } from '$lib/title-work.svelte';
	import { refusalText } from '$lib/api';
	import { rowButton } from '$lib/controls';
	import { named } from '$lib/format';
	import { resume, place } from '$lib/pauses';
	import { atFront, queueAction, type FileState } from '$lib/queue';
	import { skipFile } from '$lib/runs';
	import type { RunMode } from '$lib/library';
	import FileActions from './FileActions.svelte';

	// The queue controls for one file, at the corner of the card handed in. What
	// the queue makes of it is the caller's line; see fileState.

	let {
		path,
		standing,
		ready = false,
		admin = false,
		unavailable = false,
		busy = $bindable(false),
		onchanged,
		onaction,
		onrun,
		mayRewrite = false,
		runDisabled = false,
		refuses = '',
		children
	}: {
		path: string;
		standing: FileState;
		/** The title's work has been read, so the controls know what to offer. */
		ready?: boolean;
		admin?: boolean;
		unavailable?: boolean;
		busy?: boolean;
		onchanged: () => Promise<void>;
		onaction?: FileActionAdapter;
		onrun?: (mode: RunMode) => Promise<void>;
		mayRewrite?: boolean;
		runDisabled?: boolean;
		refuses?: string;
		children: Snippet;
	} = $props();

	const title = $derived(named(path));
	// Already at the head, where Prioritise would move nothing.
	const front = $derived(atFront(standing.waiting));
	const label = $derived(`${title.name}${title.episode ? ` ${title.episode}` : ''}`);
	let notice = $state('');
	let error = $state('');
	// Hovering to the raised tone: these rows sit on the sunken tray.
	const control = `${rowButton} gap-1.5 px-2 text-dim hover:bg-raised`;

	let alive = true;
	onDestroy(() => {
		alive = false;
	});

	async function act(action: 'top' | 'skip' | 'pause' | 'resume', seconds?: number) {
		if (onaction) {
			const target = path;
			const operation = onaction(path, standing, action, seconds);
			const current = () => alive && path === target && operation.current();
			error = '';
			notice = '';
			try {
				const message = await operation.run();
				if (current()) notice = message;
			} catch (failure) {
				if (current()) {
					error = refusalText(failure);
					throw failure;
				}
			}
			return;
		}

		busy = true;
		error = '';
		notice = '';
		try {
			if (action === 'top') {
				await queueAction('top', standing.waiting);
			} else if (action === 'resume') {
				await resume({ paths: [path] });
				notice = 'Resumed. A later sweep can process this file.';
			} else {
				if (action === 'pause') await place({ paths: [path] }, seconds ?? 0);
				if (standing.waiting.length) await queueAction('skip', standing.waiting);
				for (const item of standing.running) await skipFile(item.run, path);
				// A pause is a state, and the row leads with it. A skip needs a line,
				// since the row stands as it was until the queue answers.
				if (action === 'skip')
					notice = standing.running.length ? 'Cancellation requested.' : 'Skipped for this run.';
			}
		} catch (failure) {
			error = refusalText(failure);
			throw failure;
		} finally {
			busy = false;
			await onchanged();
		}
	}
</script>

<!-- Center the 44px action target on the 32px filename row, keeping its
     larger hit area in the card's padding. -->
<div class="flex items-start gap-2">
	{@render children()}
	{#if admin && ready}
		<span class="-mt-1.5 -mr-2 flex-none">
			{#if standing.waiting.length || standing.running.length}
				<FileActions
					{label}
					active={standing.active}
					disabled={busy || unavailable || standing.stopping}
					ontop={!standing.running.length && !front ? () => act('top') : undefined}
					onskip={() => act('skip')}
					onchoose={(seconds) => act('pause', seconds)}
					hint={standing.active
						? 'Stops this attempt. A later sweep can restart it after the pause ends.'
						: 'A later sweep can process this file after the pause ends.'}
					class={control}
				/>
			{:else if standing.paused?.path === path}
				<button
					class={control}
					disabled={busy || unavailable}
					onclick={() => void act('resume').catch(() => {})}>Resume</button
				>
			{:else if !standing.paused}
				<FileActions
					{label}
					{onrun}
					{mayRewrite}
					{runDisabled}
					{refuses}
					disabled={busy || unavailable}
					onchoose={(seconds) => act('pause', seconds)}
					class={control}
				/>
			{/if}
		</span>
	{/if}
</div>
{#if notice}<p role="status" class="text-[12px] text-dim">{notice}</p>{/if}
{#if error}<p role="alert" class="text-[12px] text-danger">{error}</p>{/if}
