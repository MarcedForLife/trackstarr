<script lang="ts">
	import { setTheme, theme } from '$lib/theme.svelte';

	const label = $derived(
		theme.resolved === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'
	);

	function toggle() {
		setTheme(theme.resolved === 'dark' ? 'light' : 'dark');
	}
</script>

<button
	onclick={toggle}
	aria-label={label}
	title={label}
	class="rounded-md p-2.5 text-faint hover:bg-raised hover:text-fg active:bg-raised lg:p-1.5"
>
	<!-- One glyph for both themes: the core swells, a masked circle slides in
	     to bite the crescent, and the rays fold away. -->
	<svg width="16" height="16" viewBox="0 0 24 24" fill="none">
		<mask id="theme-toggle-cutout">
			<rect width="24" height="24" fill="white"></rect>
			<circle class="cutout" cx="27" cy="1" r="8" fill="black"></circle>
		</mask>
		<circle
			class="core"
			cx="12"
			cy="12"
			r="4.5"
			fill="currentColor"
			mask="url(#theme-toggle-cutout)"
		></circle>
		<g class="rays" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
			<line x1="12" y1="2.5" x2="12" y2="4.5"></line>
			<line x1="12" y1="19.5" x2="12" y2="21.5"></line>
			<line x1="2.5" y1="12" x2="4.5" y2="12"></line>
			<line x1="19.5" y1="12" x2="21.5" y2="12"></line>
			<line x1="5.3" y1="5.3" x2="6.7" y2="6.7"></line>
			<line x1="17.3" y1="17.3" x2="18.7" y2="18.7"></line>
			<line x1="17.3" y1="6.7" x2="18.7" y2="5.3"></line>
			<line x1="5.3" y1="18.7" x2="6.7" y2="17.3"></line>
		</g>
	</svg>
</button>

<style>
	/* layout.css's own timing, so the glyph morphs in step with the palette
	   fading around it. */
	.core {
		transition: r var(--theme-swap) var(--theme-swap-ease);
	}
	.cutout {
		transition:
			cx var(--theme-swap) var(--theme-swap-ease),
			cy var(--theme-swap) var(--theme-swap-ease);
	}
	.rays {
		transform-origin: 12px 12px;
		transition:
			transform var(--theme-swap) var(--theme-swap-ease),
			opacity var(--theme-swap) var(--theme-swap-ease);
	}
	:global([data-theme='dark']) .core {
		r: 8px;
	}
	:global([data-theme='dark']) .cutout {
		cx: 17px;
		cy: 6px;
	}
	:global([data-theme='dark']) .rays {
		opacity: 0;
		transform: rotate(-40deg) scale(0.5);
	}
</style>
