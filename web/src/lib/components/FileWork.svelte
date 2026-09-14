<script lang="ts">
	import { refusalText } from '$lib/api';
	import { rowButton } from '$lib/controls';
	import { named } from '$lib/format';
	import { resume, place } from '$lib/pauses';
	import { queueAction, type TitleWork } from '$lib/queue';
	import { skipFile } from '$lib/runs';
	import FileActions from './FileActions.svelte';

	let {
		path,
		work,
		admin = false,
		unavailable = false,
		busy = $bindable(false),
		onchanged
	}: {
		path: string;
		work?: TitleWork;
		admin?: boolean;
		unavailable?: boolean;
		busy?: boolean;
		onchanged: () => Promise<void>;
	} = $props();
	const waiting = $derived(work?.queued.filter((item) => item.path === path) ?? []);
	const running = $derived(work?.active.filter((item) => item.path === path) ?? []);
	const paused = $derived(
		work?.pauses.find((pause) => pause.path === path) ??
			work?.pauses.find((pause) => path.startsWith(`${pause.path}/`))
	);
	const active = $derived(running.some((item) => item.stage !== 'waiting'));
	const stopping = $derived(running.some((item) => item.stopping || item.skipped));
	const title = $derived(named(path));
	const label = $derived(`${title.name}${title.episode ? ` ${title.episode}` : ''}`);
	let notice = $state('');
	let error = $state('');
	let undo = $state<string | null>(null);
	// Hovering to the raised tone: these rows sit on the sunken tray.
	const control = `${rowButton} gap-1.5 px-2 text-dim hover:bg-raised`;

	async function act(action: 'top' | 'skip' | 'pause' | 'resume' | 'undo', seconds?: number) {
		busy = true;
		error = '';
		notice = '';
		try {
			if (action === 'top' || action === 'undo') {
				const answer = await queueAction(action, waiting, { token: undo ?? undefined });
				undo = answer.undo ?? null;
				notice =
					action === 'undo'
						? 'Queue order restored.'
						: answer.moved
							? 'Moved to top.'
							: 'This file is no longer waiting.';
			} else if (action === 'resume') {
				await resume({ paths: [path] });
				notice = 'Resumed. A later sweep can process this file.';
			} else {
				if (action === 'pause') await place({ paths: [path] }, seconds ?? 0);
				if (waiting.length) await queueAction('skip', waiting);
				for (const item of running) await skipFile(item.run, path);
				undo = null;
				notice =
					action === 'pause'
						? 'File paused.'
						: running.length
							? 'Cancellation requested.'
							: 'Skipped.';
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

<div class="mt-1 text-[12px]">
	<div class="flex flex-wrap items-center justify-between gap-x-2">
		<p class="text-dim">
			{#if !work}Reading queue status…
			{:else if stopping}Stopping…
			{:else if running.length}{active ? 'Processing now' : 'Waiting for a worker'}
			{:else if paused}{paused.path === path ? 'Paused' : 'Title paused'}
			{:else if waiting.length}Queue position {Math.min(
					...waiting.map((item) => item.position)
				)}{waiting.length > 1 ? ` · ${waiting.length} runs` : ''}
			{:else}Not queued{/if}
		</p>
		{#if admin && work}
			{#if waiting.length || running.length}
				<FileActions
					{label}
					{active}
					disabled={busy || unavailable || stopping}
					ontop={!running.length ? () => act('top') : undefined}
					onskip={() => act('skip')}
					onchoose={(seconds) => act('pause', seconds)}
					hint={active
						? 'Stops this attempt. A later sweep can restart it after the pause ends.'
						: 'A later sweep can process this file after the pause ends.'}
					class={control}
				/>
			{:else if paused?.path === path}
				<button
					class={control}
					disabled={busy || unavailable}
					onclick={() => void act('resume').catch(() => {})}>Resume</button
				>
			{:else if !paused}
				<FileActions
					{label}
					disabled={busy || unavailable}
					onchoose={(seconds) => act('pause', seconds)}
					class={control}
				/>
			{/if}
		{/if}
	</div>
	{#if notice}<p role="status" class="pb-1 text-dim">
			{notice}{#if undo}<button
					class="ml-2 min-h-8 underline"
					disabled={busy || unavailable}
					onclick={() => void act('undo').catch(() => {})}>Undo</button
				>{/if}
		</p>{/if}
	{#if error}<p role="alert" class="pb-1 text-danger">{error}</p>{/if}
</div>
