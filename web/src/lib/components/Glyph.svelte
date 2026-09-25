<script module lang="ts">
	// Named here so a lookup elsewhere can hold a mark's name and still be
	// checked, as the events feed's per-kind marks are.
	export type GlyphName =
		| 'play'
		| 'pause'
		| 'bolt'
		| 'stop'
		| 'doc'
		| 'folder'
		| 'open'
		| 'next'
		| 'arrow'
		| 'cross'
		| 'chevron'
		| 'more'
		| 'refresh'
		| 'sliders'
		| 'pencil'
		| 'list'
		| 'log'
		| 'calendar'
		| 'plus'
		| 'top'
		| 'skip';
</script>

<script lang="ts">
	// Small marks. The transport three and `skip` are filled, since at 12px a
	// stroked triangle is a smudge. `bolt` is Process, since a play mark beside
	// Suspend read as Resume. `doc` is stroked to tell Plan from Process. `next`
	// and `arrow` are drawn because neither font carries U+2192. `chevron` is
	// `next` a shade lighter. `refresh` leaves its arc open where the tick goes,
	// since a closed ring with an arrowhead is a blot this small. `sliders` says
	// settings without a gear, whose teeth blur at 12px. `log` is a prompt and
	// its output, since `doc` is taken.
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
	{:else if name === 'bolt'}
		<path
			d="M9.5 1.8 4.2 9h3.4l-1 5.2L11.8 7H8.4z"
			stroke="currentColor"
			stroke-width="1.2"
			stroke-linejoin="round"
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
	{:else if name === 'folder'}
		<path
			d="M2 4.5V3h4.5L8 4.5h6V13H2z"
			fill="none"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
		></path>
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
	{:else if name === 'plus'}
		<path
			d="M8 3.2v9.6M3.2 8h9.6"
			fill="none"
			stroke="currentColor"
			stroke-width="1.8"
			stroke-linecap="round"
		></path>
	{:else if name === 'more'}
		<circle cx="3" cy="8" r="1.35"></circle>
		<circle cx="8" cy="8" r="1.35"></circle>
		<circle cx="13" cy="8" r="1.35"></circle>
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
	{:else if name === 'list'}
		<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
			<path d="M2.4 3.7h11.2"></path>
			<path d="M2.4 8h11.2"></path>
			<path d="M2.4 12.3h6.4"></path>
		</g>
	{:else if name === 'log'}
		<!-- Both end on one baseline, or the chevron reads as the row mark. -->
		<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
			<path d="M3.4 4.4 7 8l-3.6 3.6" stroke-linejoin="round"></path>
			<path d="M8.8 11.6h3.8"></path>
		</g>
	{:else if name === 'calendar'}
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<rect x="2.5" y="3.5" width="11" height="10" rx="1.5" />
			<path d="M5 2v3M11 2v3M2.5 7h11" />
		</g>
	{:else if name === 'top'}
		<g
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="M3.4 2.6h9.2"></path>
			<path d="M8 13.4V6.8"></path>
			<path d="M4.8 9.2 8 6l3.2 3.2"></path>
		</g>
	{:else if name === 'skip'}
		<path d="M3.6 3.9v8.2L9.6 8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"
		></path>
		<rect x="10.6" y="3.2" width="2.4" height="9.6" rx="0.9"></rect>
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
