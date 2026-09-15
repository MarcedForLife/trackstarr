// The sheet's way home when its rest moves under it: its top edge stands on
// the floor and jumps when content lands mid-slide. All on `translate`, which
// stays on the compositor, and a run in flight is taken over at the speed it
// has rather than kicked or snapped. $lib/carry moves other things by
// `transform`; the two never share an element.

/** A cubic-bezier easing, as its two control points. */
export type Curve = [number, number, number, number];

/** Out-cubic: the way in, and the curve every takeover bends from. */
export const OUT: Curve = [0.33, 1, 0.68, 1];

// The steepest start the family has, OUT's own.
const STEEP = OUT[1] / OUT[0];

// Where the curve is at a parameter, on either axis.
function along(param: number, first: number, second: number): number {
	const rest = 1 - param;
	return 3 * rest * rest * param * first + 3 * rest * param * param * second + param ** 3;
}

// How fast it is moving along that axis there.
function rate(param: number, first: number, second: number): number {
	const rest = 1 - param;
	return (
		3 * rest * rest * first + 6 * rest * param * (second - first) + 3 * param * param * (1 - second)
	);
}

/** How fast an easing is going at `share` of its time: the slope of its curve
 * there, in shares of the distance per share of the span. */
export function slopeAt([x1, y1, x2, y2]: Curve, share: number): number {
	if (share <= 0) return y1 / x1;
	if (share >= 1) return (1 - y2) / (1 - x2);
	// The parameter whose time is `share`, by bisection: time never runs back.
	let low = 0;
	let high = 1;
	for (let step = 0; step < 24; step++) {
		const mid = (low + high) / 2;
		if (along(mid, x1, x2) < share) low = mid;
		else high = mid;
	}
	const param = (low + high) / 2;
	return rate(param, y1, y2) / rate(param, x1, x2);
}

/** How far down a computed `translate` has an element. */
export function dropOf(translate: string): number {
	const parts = translate.split(' ');
	return parts.length > 1 ? parseFloat(parts[1]) || 0 : 0;
}

/** One run of an element's `translate` home: when it began, how far down it
 * began, how long it takes and on what curve. */
export type Motion = { at: number; from: number; span: number; curve: Curve };

/** Pixels per millisecond a run is closing on home at, `now`. None, or one
 * that has finished, is standing still. */
export function speedOf(motion: Motion | null, now: number): number {
	if (!motion) return 0;
	const share = (now - motion.at) / motion.span;
	return share >= 1 ? 0 : (slopeAt(motion.curve, share) * motion.from) / motion.span;
}

/** The run that takes an element `from` pixels home starting at `speed`: an
 * out-cubic over `span` where that is the speed one begins at, a longer one
 * rather than a faster start where the element is already going quicker, and
 * a start bent down to meet a slower one, which from standing still is a
 * swell. */
export function plan(from: number, speed: number, span: number): Pick<Motion, 'span' | 'curve'> {
	let slope = (speed * span) / from;
	if (slope > STEEP) {
		span = (STEEP * from) / speed;
		slope = STEEP;
	}
	// A start against the way home, which a shrink under a rising panel asks
	// for, is allowed a little: momentum, not a bounce.
	const lift = Math.max(-0.2, OUT[0] * slope);
	return { span, curve: [OUT[0], lift, OUT[2], OUT[3]] };
}

/** Send the element home from `from` pixels down at `speed`, and say what it
 * is now doing. The start is written with the transition off and flushed, or
 * the run would begin wherever the last one had got to. Inline, so the caller
 * clears both when something else should own the element. */
export function slide(element: HTMLElement, from: number, speed: number, span: number): Motion {
	const run = plan(from, speed, span);
	element.style.transition = 'none';
	element.style.translate = `0 ${from}px`;
	void element.offsetHeight;
	element.style.transition = `translate ${run.span}ms cubic-bezier(${run.curve.join(', ')})`;
	element.style.translate = '0 0';
	return { at: performance.now(), from, ...run };
}
