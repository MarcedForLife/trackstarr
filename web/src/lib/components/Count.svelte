<script lang="ts">
	import { Tween } from 'svelte/motion';
	import { countThrough, digitRoll } from '$lib/motion.svelte';

	// A number somebody watches change: files done, a queue count, a place in
	// the queue. A digit that changes rolls the way the number went, so the
	// change and its direction are seen and the number never shows a value it
	// was not. A jump counts through the values between instead, since every
	// digit rolling at once says nothing about how far it went. Nothing plays
	// on arrival: a panel opening shows the number it has.
	let { value, class: extra = '' }: { value: number; class?: string } = $props();

	// From this far, a change is a jump.
	const JUMP = 10;
	// How far a digit travels, in its own height: a whole place, so the new digit
	// comes up from where the next would sit and the old leaves for the one
	// before, each clipped at the slot's edge on the way.
	const TRAVEL = 1;

	// svelte-ignore state_referenced_locally
	const shown = new Tween(value, { duration: 0 });
	$effect.pre(() => {
		// Setting the target runs this again; a second set would stop the count.
		if (value === shown.target) return;
		shown.set(value, Math.abs(value - shown.target) < JUMP ? { duration: 0 } : countThrough());
	});
	// Mid-count the digits change every frame, and rolling each would be noise.
	const counting = $derived(shown.current !== shown.target);

	// The value before this one, so a change knows which way it went. A plain
	// variable rather than state, or writing it here would run this again.
	// svelte-ignore state_referenced_locally
	let previous = value;
	const roll = $derived.by(() => {
		const current = Math.round(shown.current);
		const direction = current >= previous ? 1 : -1;
		previous = current;
		const text = current.toLocaleString();
		// Slots are counted from the units end, so the last digit stays the last
		// digit when the number grows a place, and the new place arrives in front.
		const slots = [...text].map((char, i, all) => ({ index: all.length - 1 - i, char }));
		return { text, direction, slots };
	});

	// `fly`, but opaque for the middle of the turn. Fading the whole way left
	// the digit faint for most of it, which is what made a turn easy to miss;
	// the fade is there so the last sliver of a leaving digit is not.
	function turn(_node: Element, { y }: { y: number }) {
		return {
			...digitRoll(),
			css: (t: number, u: number) =>
				`transform: translateY(${u * y}em); opacity: ${Math.min(1, t * 2)}`
		};
	}
	// Counting up, a digit rises: the old one leaves above and the new one comes
	// up from below. Read when the transition starts, once the roll has turned.
	function arriving() {
		return { y: roll.direction * TRAVEL };
	}
	function leaving() {
		return { y: -roll.direction * TRAVEL };
	}
</script>

<!-- The digits are drawn from an attribute rather than set as text: a
     number split into a span a digit reads out digit by digit, and a copy for
     the reader beside it would be copied twice. The one copy that is text is
     the clipped one, which a screen reader, a search and the clipboard get. -->
<span class={`tabular-nums ${extra}`}
	><span class="sr-only">{roll.text}</span>{#if counting}<span
			class="glyph"
			aria-hidden="true"
			data-char={roll.text}
		></span>{:else}<span aria-hidden="true"
			>{#each roll.slots as slot (slot.index)}<span
					class="slot"
					in:turn={arriving()}
					out:turn={leaving()}
					>{#key slot.char}<span
							class="glyph"
							data-char={slot.char}
							in:turn={arriving()}
							out:turn={leaving()}
						></span>{/key}</span
				>{/each}</span
		>{/if}</span
>

<style>
	.glyph::before {
		content: attr(data-char);
	}

	/* One cell, so the leaving digit and the arriving one share the place and
	   the number holds its width through the turn. Clipped to the line, so a
	   digit a whole place away is behind the slot's edge, not over the next line. */
	.slot {
		display: inline-grid;
		clip-path: inset(0);
	}

	.slot > .glyph {
		grid-area: 1 / 1;
	}
</style>
