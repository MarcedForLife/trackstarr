<script module lang="ts">
	// Named here so a lookup elsewhere can hold a mark's name and still be
	// checked, as the events feed's per-kind marks are.
	export type GlyphName =
		| 'play'
		| 'pause'
		| 'stop'
		| 'doc'
		| 'open'
		| 'next'
		| 'arrow'
		| 'cross'
		| 'chevron'
		| 'refresh'
		| 'sliders'
		| 'pencil';
</script>

<script lang="ts">
	// Small marks. The transport three are filled: at 12px a stroked triangle is
	// a smudge. `doc` is stroked so Plan reads apart from Process beside it.
	// `next` and `arrow` are drawn because neither font carries U+2192. `cross`
	// empties a field or drops a row; `chevron` is `next` a shade lighter, for a
	// row that opens. `refresh` is an arc left open where its tick goes, since a
	// closed ring with an arrowhead on it is a blot this small. `sliders` says
	// settings without a gear, whose teeth are mud at 12px. `pencil` marks a
	// row whose tags open for editing.
	let { name, size = 12 }: { name: GlyphName; size?: number } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 16 16"
	fill="currentColor"
	aria-hidden="true"
	class="flex-none"
>
	{#if name === 'play'}
		<!-- Stroked with round joins too, or the corners spike. -->
		<path d="M5.6 3.9v8.2L12.7 8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"
		></path>
	{:else if name === 'pause'}
		<rect x="3.6" y="3" width="3.2" height="10" rx="1.1"></rect>
		<rect x="9.2" y="3" width="3.2" height="10" rx="1.1"></rect>
	{:else if name === 'doc'}
		<!-- Two lines; at 12px a third fills the page in. -->
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M4.4 3.2h4.6l2.6 2.6v7h-7.2z"></path>
			<path d="M6.6 8.4h3"></path>
			<path d="M6.6 10.6h3"></path>
		</g>
	{:else if name === 'open'}
		<!-- The box is open at the top right, or the crossing strokes blot. -->
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M9.4 3.4h3.2v3.2"></path>
			<path d="M12.6 3.4 7.8 8.2"></path>
			<path d="M11 9.6v3h-7.6V5h3"></path>
		</g>
	{:else if name === 'next'}
		<path
			d="M6.4 3.6 10.8 8l-4.4 4.4"
			fill="none"
			stroke="currentColor"
			stroke-width="1.9"
			stroke-linecap="round"
			stroke-linejoin="round"
		></path>
	{:else if name === 'chevron'}
		<path
			d="M6.4 3.6 10.8 8l-4.4 4.4"
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin="round"
		></path>
	{:else if name === 'arrow'}
		<!-- Shaft stopped short of the head, or the caps thicken it. -->
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.8"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M2.6 8h9.2"></path>
			<path d="M8.8 4.6 12.4 8l-3.6 3.4"></path>
		</g>
	{:else if name === 'sliders'}
		<!-- Two rails, each with its knob at a different stop. The knob is filled
		     rather than ringed: a 3px ring at this size closes up into a dot
		     anyway. -->
		<g stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
			<path d="M3 6.2h10" fill="none"></path>
			<path d="M3 10.6h10" fill="none"></path>
			<circle cx="6.2" cy="6.2" r="1.6"></circle>
			<circle cx="10" cy="10.6" r="1.6"></circle>
		</g>
	{:else if name === 'refresh'}
		<!-- The tick is the arc's own end turned square, as a compass needle is. -->
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M12.4 9.6A4.8 4.8 0 1 1 12.2 5.2"></path>
			<path d="M8.8 5.2h3.6V1.8"></path>
		</g>
	{:else if name === 'pencil'}
		<!-- The body is the diagonal; one cross-stroke says where the point starts,
		     since a second would close up into a smudge at this size. -->
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M3.4 12.6l.9-3.1 6.4-6.4 2.2 2.2-6.4 6.4z"></path>
			<path d="M9.4 4.4l2.2 2.2"></path>
		</g>
	{:else if name === 'cross'}
		<!-- Half the box, centred: six pixels of stroke at 12px. -->
		<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
			<path d="M4 4 12 12"></path>
			<path d="M12 4 4 12"></path>
		</g>
	{:else}
		<rect x="3.4" y="3.4" width="9.2" height="9.2" rx="2.2"></rect>
	{/if}
</svg>
