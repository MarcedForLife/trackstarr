import { beforeEach, expect, test, vi } from 'vitest';
import { atFront, readQueue, type QueuePage } from '$lib/queue';

vi.mock('$lib/api', () => ({ request: vi.fn() }));
const { request } = await import('$lib/api');

const ROWS = 120;

/** One page as the service answers it, off a queue of ROWS files. */
function said(offset: number, revision: number, epoch = 'first run'): QueuePage {
	return {
		total: ROWS,
		matched: ROWS,
		offset,
		epoch,
		revision,
		plans_current: true,
		items: Array.from({ length: Math.min(50, ROWS - offset) }, (_, at) => ({
			run: 'sweep',
			path: `/media/File ${offset + at}.mkv`,
			expected: 0,
			position: offset + at + 1
		}))
	};
}

let answers: QueuePage[];
let asked: string[];

beforeEach(() => {
	answers = [];
	asked = [];
	vi.mocked(request).mockImplementation((path: string) => {
		asked.push(path);
		return Promise.resolve(answers.shift() as never);
	});
});

const paths = (page: QueuePage | null) => page?.items.map((item) => item.path);

test('pages describing one queue join into a single answer', async () => {
	answers = [said(0, 7), said(50, 7), said(100, 7)];
	const page = await readQueue('', 150, () => true);
	expect(paths(page)).toHaveLength(ROWS);
	expect(asked).toHaveLength(3);
	expect(page?.revision).toBe(7);
});

test('a page from a changed queue is dropped and the read starts again', async () => {
	answers = [said(0, 7), said(50, 8), said(0, 9), said(50, 9)];
	const page = await readQueue('', 100, () => true);
	expect(paths(page)?.[50]).toBe('/media/File 50.mkv');
	expect(page?.revision).toBe(9);
});

test('a restart is caught by the epoch, which the numbers alone would not be', async () => {
	answers = [said(0, 7), { ...said(50, 7), epoch: 'after a restart' }, said(0, 7), said(50, 7)];
	const page = await readQueue('', 100, () => true);
	expect(paths(page)).toHaveLength(100);
	expect(asked).toHaveLength(4);
});

test('a rule change during the read leaves every joined row stale', async () => {
	answers = [said(0, 7), { ...said(50, 7), plans_current: false }, said(100, 7)];
	const page = await readQueue('', 150, () => true);
	expect(paths(page)).toHaveLength(ROWS);
	expect(page?.plans_current).toBe(false);
});

test('a queue that keeps changing refuses rather than answering a mixed read', async () => {
	answers = Array.from({ length: 8 }, (_, at) => said(at % 2 ? 50 : 0, at));
	await expect(readQueue('', 100, () => true)).rejects.toThrow('changing quickly');
	expect(asked).toHaveLength(6);
});

test('a superseded read stops where it is and answers with nothing', async () => {
	answers = [said(0, 7), said(50, 7), said(100, 7)];
	let live = true;
	const page = await readQueue('', 150, () => {
		const answering = live;
		live = false;
		return answering;
	});
	expect(page).toBeNull();
	expect(asked).toHaveLength(2);
});

test('a queue shorter than the rows asked for is read in one request', async () => {
	answers = [{ ...said(0, 7), total: 12, matched: 12 }];
	expect(await readQueue('', 150, () => true)).not.toBeNull();
	expect(asked).toEqual(['/api/queue?q=&offset=0']);
});

/** Rows as a title's work lists them, at the given places. */
const at = (...places: number[]) =>
	places.map((position) => ({
		run: 'sweep',
		path: `/media/${position}.mkv`,
		expected: 0,
		position
	}));

test('a title holding the head of the queue has nowhere to be moved', () => {
	expect(atFront(at(1))).toBe(true);
	expect(atFront(at(1, 2, 3))).toBe(true);
	// A gap means the files behind it would move up.
	expect(atFront(at(1, 3))).toBe(false);
	expect(atFront(at(2))).toBe(false);
	expect(atFront([])).toBe(false);
});
