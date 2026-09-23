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
	import { fade } from 'svelte/transition';
	import { rowFade } from '$lib/motion.svelte';

	// The queue controls at the corner of one file's card. The caller shows its
	// state, see fileState.

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
	// Queued or running for this file.
	const involved = $derived(standing.waiting.length > 0 || standing.running.length > 0);
	let error = $state('');
	let resuming = $state(false);
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
			try {
				await operation.run();
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
		try {
			if (action === 'top') {
				await queueAction('top', standing.waiting);
			} else if (action === 'resume') {
				await resume({ paths: [path] });
			} else {
				if (action === 'pause') await place({ paths: [path] }, seconds ?? 0);
				if (standing.waiting.length) await queueAction('skip', standing.waiting);
				for (const item of standing.running) await skipFile(item.run, path);
			}
		} catch (failure) {
			error = refusalText(failure);
			throw failure;
		} finally {
			busy = false;
			await onchanged();
		}
	}

	async function resumeFile() {
		resuming = true;
		await act('resume').catch(() => {});
		resuming = false;
	}
</script>

<!-- Center the 44px action target on the 32px filename row, keeping its
     larger hit area in the card's padding. -->
<div class="flex items-start gap-2">
	{@render children()}
	{#if admin && ready}
		<!-- One cell, right-aligned, so Resume and the menu fade over each other. -->
		<span class="-mt-1.5 -mr-2 grid flex-none justify-items-end">
			{#if !involved && standing.paused?.path === path}
				<button
					class={`${control} [grid-area:1/1]`}
					disabled={busy || unavailable}
					aria-busy={resuming}
					onclick={resumeFile}
					out:fade={rowFade()}
					in:fade={rowFade()}>Resume</button
				>
			{:else if involved || !standing.paused}
				<div class="flex [grid-area:1/1]" in:fade={rowFade()}>
					<FileActions
						{label}
						active={standing.active}
						disabled={busy || unavailable || standing.stopping}
						ontop={involved && !standing.running.length && !front ? () => act('top') : undefined}
						onskip={involved ? () => act('skip') : undefined}
						onrun={involved ? undefined : onrun}
						{mayRewrite}
						{runDisabled}
						{refuses}
						onchoose={(seconds) => act('pause', seconds)}
						hint={involved
							? standing.active
								? 'Stops this attempt. A later sweep can restart it after the pause ends.'
								: 'A later sweep can process this file after the pause ends.'
							: undefined}
						class={control}
					/>
				</div>
			{/if}
		</span>
	{/if}
</div>
{#if error}<p role="alert" class="text-[12px] text-danger">{error}</p>{/if}
