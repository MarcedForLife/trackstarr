<script lang="ts">
	import { resolve } from '$app/paths';
	import Glyph from './Glyph.svelte';
	import type { GlyphName } from './Glyph.svelte';
	import { button, danger, iconButton, primary } from '$lib/controls';
	import { REPORT_ONLY_NOTE, type RunMode } from '$lib/library';
	import { popover } from '$lib/popover.svelte';

	// The two library-wide runs, as two marks beside the heading. Each asks
	// before it goes: a sweep reads every file, and the marks carry no word to
	// read first.
	let {
		mayRewrite,
		disabled = false,
		// Which of the two is starting, so its mark says so.
		busy = '',
		onrun
	}: {
		mayRewrite: boolean;
		disabled?: boolean;
		busy?: '' | RunMode;
		onrun: (mode: RunMode) => void;
	} = $props();

	type Choice = {
		mode: RunMode;
		label: string;
		glyph: GlyphName;
		// What pressing the verb does, in the popover.
		asks: string;
		fill: string;
		verb: string;
	};

	// Accent on Plan, not Process: the loud one should be safe to press without
	// thinking.
	const CHOICES: Choice[] = [
		{
			mode: 'report',
			label: 'Plan',
			glyph: 'doc',
			asks: 'Reads every file in the library and records what needs doing. Changes nothing.',
			fill: 'bg-accent text-surface active:brightness-90',
			verb: primary
		},
		{
			mode: 'apply',
			label: 'Process',
			glyph: 'play',
			asks: 'Reads every file in the library and applies the rules. Files are rewritten on disk with no undo.',
			fill: 'border border-line-strong bg-raised active:bg-sunken',
			verb: danger
		}
	];

	const id = $props.id();
	let asking = $state<'' | RunMode>('');
	let triggers: Partial<Record<RunMode, HTMLButtonElement>> = $state({});
	let sheets: Partial<Record<RunMode, HTMLDivElement>> = $state({});

	// Hung from the mark's right edge, since the pair sits at the right of its row.
	const question = popover({ onclose: () => (asking = '') });

	async function ask(mode: RunMode) {
		const sheet = sheets[mode];
		const trigger = triggers[mode];
		if (!sheet || !trigger) return;
		if (question.holds(sheet)) {
			question.lower();
			return;
		}
		asking = mode;
		await question.raise(trigger, sheet);
	}

	function go(mode: RunMode) {
		question.lower();
		onrun(mode);
	}

	// Both marks and both sheets, not just the one up, or pressing the other mark
	// reads as a dismissal.
	function outside(target: Node) {
		return (
			!Object.values(sheets).some((sheet) => sheet?.contains(target)) &&
			!Object.values(triggers).some((trigger) => trigger?.contains(target))
		);
	}
</script>

<svelte:window
	onpointerdown={(event) => asking && outside(event.target as Node) && question.lower()}
	onresize={() => asking && question.lower()}
/>

<div class="flex flex-none items-center gap-2">
	{#each CHOICES as choice (choice.mode)}
		{@const dead = choice.mode === 'apply' && !mayRewrite}
		<button
			bind:this={triggers[choice.mode]}
			type="button"
			onclick={() => ask(choice.mode)}
			disabled={disabled || !!busy}
			aria-label={choice.label}
			aria-busy={busy === choice.mode || undefined}
			aria-disabled={dead || undefined}
			aria-expanded={asking === choice.mode}
			aria-controls={`${id}-${choice.mode}`}
			title={choice.label}
			class={`${iconButton} ${choice.fill} aria-disabled:opacity-(--disabled)`}
		>
			<!-- Larger than a glyph in a row of words: here the mark is the whole
			     control. -->
			<span class={busy === choice.mode ? 'animate-pulse' : ''}
				><Glyph name={choice.glyph} size={15} /></span
			>
		</button>
		<div
			bind:this={sheets[choice.mode]}
			id={`${id}-${choice.mode}`}
			popover="manual"
			role="dialog"
			aria-label={choice.label}
			class="fixed m-0 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong bg-raised/85 p-3.5 text-fg shadow-lg backdrop-blur-md"
		>
			<p class="text-[13px] font-semibold">{choice.label} the whole library</p>
			{#if choice.mode === 'apply' && !mayRewrite}
				<p class="mt-1.5 text-[12.5px] leading-relaxed text-dim">{REPORT_ONLY_NOTE}</p>
				<a
					href={resolve('/settings')}
					class="mt-2 inline-block text-[12.5px] font-medium text-accent underline underline-offset-2 hover:text-fg"
				>
					Change it in Settings
				</a>
			{:else}
				<p class="mt-1.5 text-[12.5px] leading-relaxed text-dim">{choice.asks}</p>
				<div class="mt-3 flex gap-2">
					<button type="button" onclick={() => question.lower()} class={`flex-1 ${button}`}>
						Cancel
					</button>
					<button type="button" onclick={() => go(choice.mode)} class={`flex-1 ${choice.verb}`}>
						<Glyph name={choice.glyph} />
						{choice.label}
					</button>
				</div>
			{/if}
		</div>
	{/each}
</div>
