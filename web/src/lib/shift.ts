// One animated height owns both the detail clip and its space in the row.
// Parent frames and following rows therefore follow the exact same edge,
// including when content arrives late or an opening is interrupted.
import { reduced } from '$lib/motion.svelte';

const panels = new WeakMap<HTMLElement, { shut: () => number; reopen: () => void }>();

/** Milliseconds from a computed CSS time. */
function ms(time: string): number {
	const value = parseFloat(time);
	return time.trim().endsWith('ms') ? value : value * 1000;
}

export type Shifting = {
	// What the opening run came to, so a chevron turns with it.
	measured?: (duration: number) => void;
	// Whether the mount itself is an opening. False for a panel already open at
	// the first render, which takes its height at once.
	animate?: boolean;
};

export function shift(node: HTMLElement, options?: Shifting) {
	const content = node.firstElementChild;
	if (!(content instanceof HTMLElement)) return;
	const inner: HTMLElement = content;
	const style = getComputedStyle(node);
	const closeSpan = ms(style.getPropertyValue('--reveal-span'));
	const easing = style.getPropertyValue('--reveal-ease').trim();
	const marginTop = style.marginTop;
	const marginBottom = style.marginBottom;
	let height = inner.getBoundingClientRect().height;
	let closing = false;
	let motion: Animation | undefined;

	function duration(opening: boolean): number {
		if (reduced()) return 0;
		return opening
			? Math.round(closeSpan + Math.min(60, Math.max(0, height - 120) / 6))
			: closeSpan;
	}

	function move(to: number, span: number, from = node.getBoundingClientRect().height) {
		// Read the current frame before cancelling, so reversal never snaps.
		const current = getComputedStyle(node);
		const start = {
			height: `${from}px`,
			marginTop: current.marginTop,
			marginBottom: current.marginBottom
		};
		motion?.cancel();
		node.style.overflow = 'hidden';
		const end = {
			height: `${to}px`,
			marginTop: closing ? '0px' : marginTop,
			marginBottom: closing ? '0px' : marginBottom
		};
		Object.assign(node.style, end);
		const run = node.animate([start, end], { duration: span, easing, fill: 'both' });
		motion = run;
		void run.finished
			.then(() => {
				if (motion !== run) return;
				motion = undefined;
				run.cancel();
				if (!closing) {
					// Natural sizing at rest, and no clipping of focus rings.
					node.style.height = '';
					node.style.marginTop = '';
					node.style.marginBottom = '';
					node.style.overflow = 'visible';
				}
			})
			.catch(() => {});
	}

	function open(from?: number) {
		closing = false;
		height = inner.getBoundingClientRect().height;
		const span = duration(true);
		options?.measured?.(span);
		move(height, span, from);
	}

	const watch = new ResizeObserver(() => {
		const now = inner.getBoundingClientRect().height;
		if (Math.abs(now - height) < 1) return;
		// At rest, layout has already adopted the new content height. Start
		// from the previous height; in flight, take over the animated height.
		const from = motion ? node.getBoundingClientRect().height : height;
		height = now;
		if (!closing) open(from);
	});
	watch.observe(inner);
	panels.set(node, {
		shut() {
			if (closing) return duration(false);
			closing = true;
			const span = duration(false);
			move(0, span);
			return span;
		},
		reopen() {
			if (closing) open();
		}
	});

	if (options?.animate === false) {
		// Where a finished opening leaves it, so focus rings are not clipped.
		node.style.overflow = 'visible';
	} else {
		node.style.marginTop = '0px';
		node.style.marginBottom = '0px';
		open(0);
	}

	return {
		destroy() {
			watch.disconnect();
			motion?.cancel();
			panels.delete(node);
		}
	};
}

/** Keep the panel mounted until its height has closed. */
export function fold(node: HTMLElement) {
	return { duration: panels.get(node)?.shut() ?? 0 };
}

/** Svelte reuses the node when a row reopens during its outro. */
export function unfold(node: HTMLElement) {
	panels.get(node)?.reopen();
}
