<script lang="ts">
	// The Trackstarr mark: a four-point star of five upright bars, a star from
	// across the room and a level meter up close. static/favicon.svg and the
	// home-screen icons are the same star on a #1b1d23 disc, redrawn by hand
	// when this changes.
	let { size = 19, class: className = '' }: { size?: number; class?: string } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" class={className}>
	<path
		fill="currentColor"
		d="M0.8 12a1.6 1.6 0 0 1 3.2 0v0a1.6 1.6 0 0 1-3.2 0zM5.6 9.8a1.6 1.6 0 0 1 3.2 0v4.4a1.6 1.6 0 0 1-3.2 0zM10.4 2.4a1.6 1.6 0 0 1 3.2 0v19.2a1.6 1.6 0 0 1-3.2 0zM15.2 9.8a1.6 1.6 0 0 1 3.2 0v4.4a1.6 1.6 0 0 1-3.2 0zM20 12a1.6 1.6 0 0 1 3.2 0v0a1.6 1.6 0 0 1-3.2 0z"
	></path>
</svg>
