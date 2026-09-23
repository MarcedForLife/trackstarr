import { sineInOut } from 'svelte/easing';

export const BARS = [
	{ x: 2.4, top: 12, bottom: 12.001 },
	{ x: 7.2, top: 9.8, bottom: 14.2 },
	{ x: 12, top: 2.4, bottom: 21.6 },
	{ x: 16.8, top: 9.8, bottom: 14.2 },
	{ x: 21.6, top: 12, bottom: 12.001 }
];

// The centre halves share an endpoint at rest and occupy opposite arcs in the ring.
export const ROLL_BARS = [
	{ ...BARS[0], angle: 150, departure: -0.35, arrival: 0.1, clearance: 2.5 },
	{ ...BARS[1], angle: 210, departure: 0.12, arrival: -0.3, clearance: 0.5 },
	{ ...BARS[2], bottom: 12, angle: 270, departure: 0.4, arrival: 0.3, clearance: 0 },
	{ ...BARS[2], top: 12, angle: 90, departure: -0.1, arrival: -0.4, clearance: 0 },
	{ ...BARS[3], angle: 30, departure: 0.28, arrival: -0.05, clearance: -0.5 },
	{ ...BARS[4], angle: -30, departure: -0.2, arrival: 0.35, clearance: -2.5 }
];

export const SEGMENTS = 8;
export const SAMPLES = 256;
export const CYCLE_MS = 6400;
const ROLL_IN_MS = 1400;
const REFORMATION_MS = 1800;

export interface MarkPose {
	x: number;
	y: number;
	rotation: number;
	span: number;
}

/** Keep the rotational lift independent of the pieces breaking apart. */
function rollIn(time: number): number {
	const progress = Math.max(0, Math.min(1, (time * CYCLE_MS) / ROLL_IN_MS));
	return sineInOut(progress);
}

function deformation(time: number): number {
	if (time < 0.5) return morphWave(morphPhase(time));
	const remaining = Math.max(0, Math.min(1, ((1 - time) * CYCLE_MS) / REFORMATION_MS));
	return sineInOut(remaining);
}

function morphPhase(time: number): number {
	const morphTime = time + Math.sin(2 * Math.PI * time) / (3 * Math.PI);
	return (1 - Math.cos(Math.PI * morphTime)) / 2;
}

function morphWave(phase: number): number {
	return 1 - (1 - Math.sin(Math.PI * phase)) ** 1.5;
}

/** Broaden the cruising speed and carry the gathering pieces into the gentle roll. */
export function markTurn(time: number): number {
	const lift = time < 0.5 ? rollIn(time) : deformation(time);
	const push = lift - morphWave(morphPhase(time));
	// Flatten the crest without changing the total turns or velocity at the loop seam.
	const cruise = (Math.PI / 30) * (Math.sin(2 * Math.PI * time) - Math.sin(4 * Math.PI * time) / 2);
	return (
		3 * Math.PI * (1 - Math.cos(Math.PI * time)) +
		((4 * Math.PI) / 21) * Math.sin(2 * Math.PI * time) +
		(Math.PI / 8) * push +
		cruise
	);
}

/** One continuous deformation, with separated routes in a shared rotating frame. */
export function markPose(time: number, index: number, part: number): MarkPose {
	const bar = ROLL_BARS[index];
	// The bars fully reform with zero deformation speed while the whole mark rolls through.
	const turn = markTurn(time);
	const wave = deformation(time);
	// The departure follows its original pacing, and the return keeps its gentler timing.
	const outwardPhase = Math.asin(1 - (1 - wave) ** (2 / 3)) / Math.PI;
	const phase = time < 0.5 ? morphPhase(time) : 1 - outwardPhase;
	// Each bar leads or trails differently on departure and return, without waiting at rest.
	const blend = (1 - Math.cos(Math.PI * phase)) / 2;
	const pace = bar.departure + (bar.arrival - bar.departure) * blend;
	const amount = wave + pace * wave * (1 - wave);
	const angle = (bar.angle * Math.PI) / 180;
	const tangent = ((((bar.angle + 270) % 180) - 90) * Math.PI) / 180;
	const side = Math.cos(angle - tangent) > 0 ? 1 : -1;
	const ringLength = (8.8 * 32 * Math.PI) / 180;
	const length =
		index === 2 || index === 3
			? ringLength + (bar.bottom - bar.top - ringLength) * (1 - amount) ** 1.5
			: bar.bottom - bar.top + (ringLength - (bar.bottom - bar.top)) * amount;
	const facing = tangent * amount;
	const bend = (side * (32 * amount + 130 * Math.sin(Math.PI * amount) ** 2) * Math.PI) / 180;
	const along = ((part + 0.5) / SEGMENTS - 0.5) * bend;
	const radius = Math.abs(bend) > 0.000001 ? length / bend : 0;
	const localX = radius * (Math.cos(along) - 1);
	const localY = radius ? radius * Math.sin(along) : length * ((part + 0.5) / SEGMENTS - 0.5);
	const centreY = (bar.top + bar.bottom) / 2 - 12;
	const x =
		bar.x -
		12 +
		(12 + 8.8 * Math.cos(angle) - bar.x) * amount +
		localX * Math.cos(facing) -
		localY * Math.sin(facing);
	const y =
		centreY +
		(8.8 * Math.sin(angle) - centreY) * amount +
		bar.clearance * 4 * amount * (1 - amount) +
		localX * Math.sin(facing) +
		localY * Math.cos(facing);
	return {
		x: 12 + x * Math.cos(turn) - y * Math.sin(turn),
		y: 12 + x * Math.sin(turn) + y * Math.cos(turn),
		rotation: ((facing + along + turn) * 180) / Math.PI,
		span:
			index === 0 || index === ROLL_BARS.length - 1
				? 0
				: radius
					? 2 * radius * Math.sin(bend / (2 * SEGMENTS))
					: length / SEGMENTS
	};
}
