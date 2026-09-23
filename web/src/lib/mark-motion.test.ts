import { expect, test } from 'vitest';
import {
	BARS,
	CYCLE_MS,
	ROLL_BARS,
	SEGMENTS,
	SAMPLES,
	markPose,
	markTurn,
	type MarkPose
} from './mark-motion';

type Point = { x: number; y: number };
type Segment = [Point, Point];

function endpoints(pose: MarkPose): Segment {
	const angle = (pose.rotation * Math.PI) / 180;
	const x = (-Math.sin(angle) * pose.span) / 2;
	const y = (Math.cos(angle) * pose.span) / 2;
	return [
		{ x: pose.x - x, y: pose.y - y },
		{ x: pose.x + x, y: pose.y + y }
	];
}

function pointDistance(point: Point, [a, b]: Segment): number {
	const x = b.x - a.x;
	const y = b.y - a.y;
	const amount = Math.max(
		0,
		Math.min(1, ((point.x - a.x) * x + (point.y - a.y) * y) / (x * x + y * y || 1))
	);
	return Math.hypot(point.x - a.x - amount * x, point.y - a.y - amount * y);
}

function cross(a: Point, b: Point, c: Point): number {
	return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

function distance(a: Segment, b: Segment): number {
	if (cross(...a, b[0]) * cross(...a, b[1]) < 0 && cross(...b, a[0]) * cross(...b, a[1]) < 0)
		return 0;
	return Math.min(
		pointDistance(a[0], b),
		pointDistance(a[1], b),
		pointDistance(b[0], a),
		pointDistance(b[1], a)
	);
}

test('bars stay separated and inside the mark, including between animation keyframes', () => {
	const frames = Array.from({ length: SAMPLES + 1 }, (_, frame) =>
		ROLL_BARS.map((_, bar) =>
			Array.from({ length: SEGMENTS }, (_, part) => markPose(frame / SAMPLES, bar, part))
		)
	);
	let closest = Infinity;
	let widest = 0;
	const seamGaps: number[] = [];
	// Six samples per interval also cover the browser's linear interpolation.
	for (let sample = 0; sample <= SAMPLES * 6; sample++) {
		const frame = Math.min(SAMPLES - 1, Math.floor(sample / 6));
		const fraction = sample / 6 - frame;
		const bars = frames[frame].map((bar, index) =>
			bar.map((from, part) => {
				const to = frames[frame + 1][index][part];
				const turn = to.rotation - from.rotation;
				return endpoints({
					x: from.x + (to.x - from.x) * fraction,
					y: from.y + (to.y - from.y) * fraction,
					span: from.span + (to.span - from.span) * fraction,
					rotation: from.rotation + (turn - Math.round(turn / 180) * 180) * fraction
				});
			})
		);
		let seamGap = Infinity;
		for (let bar = 0; bar < bars.length; bar++) {
			for (const segment of bars[bar]) {
				for (const point of segment)
					widest = Math.max(widest, Math.hypot(point.x - 12, point.y - 12) + 1.6);
				for (let other = bar + 1; other < bars.length; other++) {
					// The centre halves deliberately meet at their shared seam.
					for (const neighbor of bars[other]) {
						const gap = distance(segment, neighbor);
						if (bar === 2 && other === 3) seamGap = Math.min(seamGap, gap);
						else closest = Math.min(closest, gap);
					}
				}
			}
		}
		seamGaps.push(seamGap);
	}
	// Include the full 3.2-unit stroke and a visible gap at the small header size.
	expect(closest).toBeGreaterThan(3.75);
	expect(widest).toBeLessThan(12);
	// The shared caps separate steadily until clear, then never collide again before return.
	// Once circular, linear keyframe interpolation slightly shortens the rotating chords.
	for (let sample = 1; sample < seamGaps.length; sample++) {
		const change = seamGaps[sample] - seamGaps[sample - 1];
		if (sample < SAMPLES * 1.5 && seamGaps[sample - 1] < 3.75)
			expect(change).toBeGreaterThanOrEqual(-0.000001);
		if (sample > SAMPLES * 4.5) expect(change).toBeLessThanOrEqual(0.000001);
		if ((sample >= SAMPLES * 1.5 || seamGaps[sample - 1] >= 3.75) && sample <= SAMPLES * 4.5)
			expect(seamGaps[sample]).toBeGreaterThan(3.75);
	}
});

test('the loop ends at the original star without a pose jump', () => {
	ROLL_BARS.forEach((bar, index) => {
		for (let part = 0; part < SEGMENTS; part++) {
			const first = markPose(0, index, part);
			const last = markPose(1, index, part);
			expect(first.x).toBeCloseTo(bar.x, 8);
			expect(first.y).toBeCloseTo(bar.top + ((part + 0.5) / SEGMENTS) * (bar.bottom - bar.top), 8);
			expect(last.x).toBeCloseTo(first.x, 8);
			expect(last.y).toBeCloseTo(first.y, 8);
			expect(last.span).toBeCloseTo(first.span, 8);
			expect(Math.cos((last.rotation * Math.PI) / 180)).toBeCloseTo(1, 8);
		}
	});
});

test('returning bars keep travelling as they change length, including near rest', () => {
	for (const bar of [1, 2, 3, 4]) {
		for (const elapsed of [4700, 4800, 5200, 5600, 6000, 6200, 6350]) {
			const time = elapsed / CYCLE_MS;
			const before = markPose(time, bar, 3);
			const after = markPose(time + 0.005, bar, 3);
			expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeGreaterThan(0.000001);
			expect(Math.abs(after.span - before.span)).toBeGreaterThan(0.000001);
		}
	}
});

test('the departure accelerates gently without reversing or stopping the roll', () => {
	const speed = (time: number) => (markTurn(time + 0.00001) - markTurn(time - 0.00001)) / 0.00002;
	for (let step = 0; step <= SAMPLES; step++) expect(speed(step / SAMPLES)).toBeGreaterThan(0);
	for (let elapsed = 100; elapsed <= 1400; elapsed += 100) {
		expect(speed(elapsed / CYCLE_MS)).toBeLessThan(speed(0.5));
		expect(Math.abs(speed(elapsed / CYCLE_MS) - speed((elapsed - 25) / CYCLE_MS))).toBeLessThan(1);
	}
	// The joins into the circular roll and return preserve angular velocity.
	for (const time of [1400 / CYCLE_MS, 4600 / CYCLE_MS])
		expect(Math.abs(speed(time - 0.00001) - speed(time + 0.00001))).toBeLessThan(0.02);
	expect(speed(0)).toBeGreaterThan(0);
	expect(speed(1)).toBeCloseTo(speed(0), 2);
	expect(speed(0)).toBeGreaterThan(speed(0.5) / 10);
	expect(speed(0)).toBeLessThan(speed(0.5) / 5);
	expect(speed(0.5) - speed(0.49)).toBeLessThan(speed(0.3) - speed(0.29));
});

test('the circle cruises steadily and slows throughout reformation', () => {
	const speed = (elapsed: number) =>
		markTurn((elapsed + 1) / CYCLE_MS) - markTurn((elapsed - 1) / CYCLE_MS);
	const cruise = [2200, 2600, 3200, 3800, 4200].map(speed);
	expect(Math.max(...cruise) / Math.min(...cruise)).toBeLessThan(1.08);
	for (let elapsed = 4700; elapsed <= CYCLE_MS; elapsed += 100)
		expect(speed(elapsed)).toBeLessThan(speed(elapsed - 100));
});

test('the circle holds its shape before gathering and joins each phase without a velocity jump', () => {
	for (let bar = 0; bar < ROLL_BARS.length; bar++) {
		for (let part = 0; part < SEGMENTS; part++) {
			// The ring keeps its shape right up to the gathering phase.
			expect(markPose(4600 / CYCLE_MS, bar, part).span).toBeCloseTo(
				markPose(0.5, bar, part).span,
				8
			);
			for (const elapsed of [3200, 4600]) {
				const time = elapsed / CYCLE_MS;
				const step = 0.000001;
				const before = markPose(time - step, bar, part);
				const at = markPose(time, bar, part);
				const after = markPose(time + step, bar, part);
				for (const property of ['x', 'y', 'span'] as const)
					expect(
						Math.abs((after[property] - 2 * at[property] + before[property]) / step)
					).toBeLessThan(0.01);
			}
		}
	}
});

test('the breakup keeps its gradual finish as the spin carries it into the circle', () => {
	// Compare geometry in the rotating frame, so spin cannot masquerade as morphing.
	const local = (time: number, bar: number, part: number) => {
		const { x, y } = markPose(time, bar, part);
		const turn = markTurn(time);
		return {
			x: (x - 12) * Math.cos(turn) + (y - 12) * Math.sin(turn),
			y: (y - 12) * Math.cos(turn) - (x - 12) * Math.sin(turn)
		};
	};
	const remaining = (elapsed: number) =>
		ROLL_BARS.reduce(
			(total, _, bar) =>
				total +
				Array.from({ length: SEGMENTS }, (_, part) => {
					const current = local(elapsed / CYCLE_MS, bar, part);
					const ring = local(0.5, bar, part);
					return Math.hypot(current.x - ring.x, current.y - ring.y);
				}).reduce((sum, distance) => sum + distance, 0),
			0
		);
	expect(remaining(200)).toBeGreaterThan(remaining(0) * 0.75);
	expect(remaining(500)).toBeGreaterThan(remaining(0) * 0.5);
	expect(remaining(1000)).toBeGreaterThan(remaining(0) * 0.25);
	expect(remaining(1400)).toBeGreaterThan(remaining(0) * 0.05);
	expect(remaining(2200)).toBeLessThan(remaining(0) * 0.02);
	expect(remaining(3200)).toBeLessThan(0.000001);
});

test('the formed star rolls through the loop seam with matching velocity', () => {
	const step = 0.000001;
	ROLL_BARS.forEach((_, bar) => {
		for (let part = 0; part < SEGMENTS; part++) {
			const start = markPose(0, bar, part);
			const leaving = markPose(step, bar, part);
			const arriving = markPose(1 - step, bar, part);
			const end = markPose(1, bar, part);
			const before = { x: (end.x - arriving.x) / step, y: (end.y - arriving.y) / step };
			const after = { x: (leaving.x - start.x) / step, y: (leaving.y - start.y) / step };
			expect(before.x).toBeCloseTo(after.x, 2);
			expect(before.y).toBeCloseTo(after.y, 2);
			expect(Math.hypot(after.x, after.y)).toBeGreaterThan(0.1);
			expect((leaving.span - start.span) / step).toBeCloseTo(0, 2);
			expect((end.span - arriving.span) / step).toBeCloseTo(0, 2);
		}
	});
});

test('the left bars enter upward while the right bars enter downward', () => {
	for (const time of [0.025, 0.05, 0.075, 0.1, 0.15]) {
		const centre = (index: number) =>
			Array.from({ length: SEGMENTS }, (_, part) => markPose(time, index, part).y).reduce(
				(sum, y) => sum + y,
				0
			) / SEGMENTS;
		for (const index of [0, 1]) expect(centre(index)).toBeLessThan(12);
		for (const index of [4, 5]) expect(centre(index)).toBeGreaterThan(12);
	}
});

test('the centre splits up and right, down and left, then rejoins the original bar', () => {
	// Check the departure before the faster turn carries the halves around the ring.
	for (const elapsed of [48, 96, 144, 192]) {
		const time = elapsed / CYCLE_MS;
		for (const index of [2, 3]) {
			const points = Array.from({ length: SEGMENTS }, (_, part) => markPose(time, index, part));
			const x = points.reduce((sum, point) => sum + point.x, 0) / SEGMENTS;
			const y = points.reduce((sum, point) => sum + point.y, 0) / SEGMENTS;
			if (index === 2) {
				expect(x).toBeGreaterThan(12);
				expect(y).toBeLessThan(7.2);
			} else {
				expect(x).toBeLessThan(12);
				expect(y).toBeGreaterThan(16.8);
			}
		}
	}
	for (const time of [0, 1]) {
		const top = endpoints(markPose(time, 2, 0))[0];
		const upperSeam = endpoints(markPose(time, 2, SEGMENTS - 1))[1];
		const lowerSeam = endpoints(markPose(time, 3, 0))[0];
		const bottom = endpoints(markPose(time, 3, SEGMENTS - 1))[1];
		expect(top.y).toBeCloseTo(BARS[2].top, 8);
		expect(bottom.y).toBeCloseTo(BARS[2].bottom, 8);
		expect(upperSeam.x).toBeCloseTo(lowerSeam.x, 8);
		expect(upperSeam.y).toBeCloseTo(lowerSeam.y, 8);
	}
});
