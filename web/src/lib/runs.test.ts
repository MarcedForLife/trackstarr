import { describe, expect, test } from 'vitest';
import { fileRows, fileStatus, progressLabel, remaining } from '$lib/runs';
import type { ActiveFile, Run } from '$lib/runs';

function run(over: Partial<Run> = {}): Run {
	return {
		id: 'r1',
		kind: 'sweep',
		started: '2026-09-02T04:00:00+12:00',
		seconds: 0,
		dry_run: false,
		label: '',
		total: 0,
		done: 0,
		counts: {},
		stopping: false,
		active: [],
		seen: 0,
		...over
	};
}

function file(over: Partial<ActiveFile> = {}): ActiveFile {
	return {
		path: '/data/a.mkv',
		seconds: 0,
		stage: 'encoding',
		duration: 0,
		done: 0,
		speed: 0,
		...over
	};
}

describe('progressLabel', () => {
	test('counts the files once there is more than one', () => {
		expect(progressLabel(run({ total: 1200, done: 40 }))).toBe('40 of 1,200 files');
	});

	// "0 of 0" would read as a run that found nothing rather than one still
	// counting, and each kind is still counting for a different reason.
	test('says what a run with no total yet is doing', () => {
		expect(progressLabel(run({ kind: 'sweep' }))).toBe('Walking the library…');
		expect(progressLabel(run({ kind: 'import' }))).toBe('Queueing the delivery…');
		expect(progressLabel(run({ kind: 'recheck' }))).toBe('Listing the files…');
	});

	test('counts nothing for a run of one file, and says where it is', () => {
		expect(progressLabel(run({ total: 1 }))).toBe('Waiting for a free worker');
		expect(progressLabel(run({ total: 1, active: [file()] }))).toBe('');
		expect(progressLabel(run({ total: 1, done: 1 }))).toBe('');
	});
});

describe('remaining', () => {
	test('says nothing for a run that is not moving', () => {
		const going = run({ total: 1000, done: 500, seconds: 600 });
		expect(remaining(going, true)).toBe('');
		expect(remaining({ ...going, stopping: true })).toBe('');
	});

	test("takes the service's own figure over the rate, and counts it down", () => {
		const rewriting = run({ rewrite_seconds: 240, queued: 12 });
		expect(remaining(rewriting)).toBe('12 to rewrite · about 4m left');
		expect(remaining(rewriting, false, 60)).toBe('12 to rewrite · about 3m left');
	});

	test('hedges further while the walk is still finding work', () => {
		expect(remaining(run({ rewrite_seconds: 240, walking: true }))).toBe('at least 4m left');
	});

	// An encode running past the machine's own rate. The next snapshot answers
	// it; a countdown through zero would not.
	test('says nothing once the estimate has run out', () => {
		expect(remaining(run({ rewrite_seconds: 240 }), false, 240)).toBe('');
	});

	test('adds the time of day once there is enough left to be worth one', () => {
		expect(remaining(run({ rewrite_seconds: 3600 }))).toMatch(/^about 1h 0m left · done .+/);
	});

	test('falls back to the rate a reporting sweep has got through files at', () => {
		expect(remaining(run({ total: 500, done: 100, seconds: 60 }))).toBe('about 4m left');
	});

	test('waits for enough files and enough seconds before guessing', () => {
		// The first files of a sweep are the ones answered from the cache, so a
		// rate made from them promises minutes and takes hours.
		expect(remaining(run({ total: 1000, done: 39, seconds: 60 }))).toBe('');
		expect(remaining(run({ total: 1000, done: 100, seconds: 29 }))).toBe('');
		expect(remaining(run({ total: 0, done: 100, seconds: 60 }))).toBe('');
	});

	test('says nothing for a run whose count has caught its total', () => {
		expect(remaining(run({ total: 100, done: 100, seconds: 60 }))).toBe('');
	});
});

describe('fileStatus', () => {
	test('names the wait a queued rewrite is in', () => {
		expect(fileStatus(file({ stage: 'waiting' }))).toBe('Waiting for a free rewrite slot');
	});

	test('says nothing for a file with no bar to caption', () => {
		expect(fileStatus(file({ stage: 'working' }))).toBe('');
		expect(fileStatus(file({ duration: 0 }))).toBe('');
	});

	test('reads how far through and how much longer off the encode', () => {
		const encoding = file({ duration: 3600, done: 900, speed: 2 });
		expect(fileStatus(encoding)).toBe('25% · 22m left');
	});

	// The encode does not wait for the next snapshot, so the meantime is
	// carried forward at the speed ffmpeg last reported.
	test('carries the reading forward at the speed it was going at', () => {
		expect(fileStatus(file({ duration: 3600, done: 900, speed: 2 }), 100)).toBe('30% · 20m left');
	});

	test('gives how far without how long before ffmpeg reports a speed', () => {
		expect(fileStatus(file({ duration: 3600, done: 900 }))).toBe('25%');
	});

	test('never reads past the end of the file', () => {
		expect(fileStatus(file({ duration: 3600, done: 3500, speed: 2 }), 600)).toBe('100%');
	});
});

describe('fileRows', () => {
	// A sweep releases a file after the probe and picks it up again for the
	// rewrite, so one path holds a released row and a queued one at once, and
	// the run's key was the path.
	test('says each file once, at the newest thing it is doing', () => {
		const rows = fileRows(
			run({
				active: [file({ path: '/data/a.mkv' })],
				upcoming: [{ path: '/data/b.mkv', expected: 90 }],
				recent: [
					{ path: '/data/b.mkv', status: '', seconds: 2, detail: '' },
					{ path: '/data/a.mkv', status: '', seconds: 1, detail: '' },
					{ path: '/data/c.mkv', status: 'modified', seconds: 30, detail: 'added 2.0' }
				]
			})
		);

		expect(rows.map((row) => row.path)).toEqual(['/data/a.mkv', '/data/b.mkv', '/data/c.mkv']);
		expect(rows[0].live).not.toBeNull();
		expect(rows[1].waiting).not.toBeNull();
		expect(rows[2].verdict).toBe('modified');
	});

	test('keeps the newest of two released rows for one file', () => {
		const rows = fileRows(
			run({
				recent: [
					{ path: '/data/a.mkv', status: 'modified', seconds: 30, detail: 'added 2.0' },
					{ path: '/data/a.mkv', status: '', seconds: 1, detail: '' }
				]
			})
		);

		expect(rows).toHaveLength(1);
		expect(rows[0].verdict).toBe('modified');
	});
});
