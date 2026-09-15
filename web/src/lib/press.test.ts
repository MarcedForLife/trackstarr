import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { pressGesture } from './press';

let node: HTMLElement;
let gesture: ReturnType<typeof pressGesture>;
let tap: ReturnType<typeof vi.fn<() => void>>;
let hold: ReturnType<typeof vi.fn<() => void>>;

function send(type: string, properties: Record<string, unknown> = {}) {
	const event = new Event(type);
	Object.assign(event, {
		pointerId: 1,
		pointerType: 'mouse',
		button: 0,
		clientX: 20,
		clientY: 20,
		detail: 1,
		...properties
	});
	node.dispatchEvent(event);
}

beforeEach(() => {
	vi.useFakeTimers();
	node = Object.assign(new EventTarget(), {
		setPointerCapture: vi.fn(),
		hasPointerCapture: () => true
	}) as unknown as HTMLElement;
	tap = vi.fn();
	hold = vi.fn();
	gesture = pressGesture(node, {
		ontap: tap,
		onhold: hold,
		onpress: vi.fn(),
		within: (x) => x >= 0 && x < 100
	});
});

afterEach(() => {
	gesture.destroy();
	vi.useRealTimers();
});

test('a secondary mouse button cannot select on a long press', () => {
	send('pointerdown', { button: 2 });
	vi.advanceTimersByTime(700);
	send('pointerup', { button: 2 });
	expect(hold).not.toHaveBeenCalled();
	expect(tap).not.toHaveBeenCalled();
});

test('a mouse drag within the row does not become a click', () => {
	send('pointerdown');
	send('pointermove', { clientX: 60 });
	send('pointerup', { clientX: 60 });
	send('click');
	expect(tap).not.toHaveBeenCalled();
});

test('a touch released just beyond the edge does not open the row', () => {
	send('pointerdown', { pointerType: 'touch', clientX: 98 });
	send('pointermove', { pointerType: 'touch', clientX: 102 });
	send('pointerup', { pointerType: 'touch', clientX: 102 });
	expect(tap).not.toHaveBeenCalled();
});

test('a second pointer cannot replace or cancel the held gesture', () => {
	send('pointerdown', { pointerType: 'touch' });
	send('pointerdown', { pointerType: 'touch', pointerId: 2 });
	send('pointercancel', { pointerId: 2 });
	vi.advanceTimersByTime(700);
	send('pointerup', { pointerType: 'touch' });
	expect(hold).toHaveBeenCalledOnce();
	expect(tap).not.toHaveBeenCalled();
});

test('a long press selects once and leaves keyboard activation available', () => {
	send('pointerdown');
	vi.advanceTimersByTime(700);
	send('pointerup');
	send('click');
	expect(hold).toHaveBeenCalledOnce();
	expect(tap).not.toHaveBeenCalled();
	send('click', { detail: 0 });
	expect(tap).toHaveBeenCalledOnce();
});

test('a canceled press does not select and the next tap still opens', () => {
	send('pointerdown');
	vi.advanceTimersByTime(200);
	send('pointercancel');
	vi.advanceTimersByTime(700);
	send('pointerup');
	expect(hold).not.toHaveBeenCalled();
	send('pointerdown');
	send('pointerup');
	send('click');
	expect(tap).toHaveBeenCalledOnce();
});
