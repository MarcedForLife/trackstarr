import { describe, expect, test } from 'vitest';
import { OUT, dropOf, plan, slopeAt, speedOf } from '$lib/slide';

describe('slopeAt', () => {
	test('starts out-cubic at three times the mean speed and ends still', () => {
		expect(slopeAt(OUT, 0)).toBeCloseTo(3.03, 1);
		expect(slopeAt(OUT, 0.5)).toBeGreaterThan(0.5);
		expect(slopeAt(OUT, 1)).toBe(0);
	});

	test('only ever slows on the way in', () => {
		const shares = Array.from({ length: 11 }, (_, at) => at / 10);
		const slopes = shares.map((share) => slopeAt(OUT, share));
		for (let at = 1; at < slopes.length; at++)
			expect(slopes[at]).toBeLessThanOrEqual(slopes[at - 1]);
	});
});

describe('speedOf', () => {
	test('is the curve scaled to the run, and nothing once it is over', () => {
		const motion = { at: 1000, from: 400, span: 400, curve: OUT };
		expect(speedOf(motion, 1000)).toBeCloseTo(3.03, 1);
		expect(speedOf(motion, 1200)).toBeCloseTo(slopeAt(OUT, 0.5), 5);
		expect(speedOf(motion, 1400)).toBe(0);
		expect(speedOf(null, 1000)).toBe(0);
	});
});

describe('plan', () => {
	test('keeps the out curve and lengthens the span when the element is already quick', () => {
		// Moving at the speed a fresh 400ms out-cubic would start 400px at.
		const run = plan(400, 3.03, 400);
		expect(run.curve[1]).toBeCloseTo(1, 2);
		expect(run.span).toBeCloseTo(400, 0);
		// Twice as fast: the same start, over twice the distance's worth of time.
		expect(plan(400, 6.06, 400).span).toBeCloseTo(200, 0);
	});

	test('bends the start down to a slower element, to a swell from standstill', () => {
		expect(plan(300, 0, 260)).toEqual({ span: 260, curve: [0.33, 0, 0.68, 1] });
		const gentle = plan(405, 1.377, 400);
		expect(gentle.span).toBe(400);
		expect(gentle.curve[1]).toBeCloseTo(0.449, 2);
	});

	test('allows a little momentum against a shrink, never a bounce', () => {
		expect(plan(-40, 1, 400).curve[1]).toBe(-0.2);
	});
});

describe('dropOf', () => {
	test('reads the vertical part of a computed translate', () => {
		expect(dropOf('none')).toBe(0);
		expect(dropOf('0px')).toBe(0);
		expect(dropOf('0px 110.5px')).toBe(110.5);
		expect(dropOf('0px -40px 0px')).toBe(-40);
	});
});
