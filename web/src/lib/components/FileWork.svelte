<script lang="ts">
	import type { Snippet } from 'svelte';
	import { refusalText } from '$lib/api';
	import { rowButton } from '$lib/controls';
	import { named } from '$lib/format';
	import { resume, place } from '$lib/pauses';
	import { atFront, queueAction, type FileState } from '$lib/queue';
	import { skipFile } from '$lib/runs';
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

	async function act(action: 'top' | 'skip' | 'pause' | 'resume', seconds?: number) {
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

<!-- Pulled into the card's padding, since a 44px target wants the edge. -->
<div class="flex items-start gap-2">
	{@render children()}
	{#if admin && ready}
		<span class="-mt-2 -mr-2 flex-none">
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
