import { beforeEach, expect, test, vi } from 'vitest';
import { TitleWorkController } from '$lib/title-work.svelte';
import { fileState, getTitleWork, queueAction, type TitleWork } from '$lib/queue';
import { getPauses, place, resume, type Pause } from '$lib/pauses';
import { skipFile } from '$lib/runs';

const harness = vi.hoisted(() => ({
	stop: vi.fn(),
	now: vi.fn(),
	watches: [] as { ask: () => Promise<void>; ready: () => boolean; pace: () => number }[]
}));
vi.mock('$lib/poll', () => ({
	poll: (watch: (typeof harness.watches)[number]) => {
		harness.watches.push(watch);
		return { stop: harness.stop, now: harness.now };
	}
}));
vi.mock('$lib/queue', async (original) => ({
	...(await original<typeof import('$lib/queue')>()),
	getTitleWork: vi.fn(),
	queueAction: vi.fn()
}));
vi.mock('$lib/pauses', () => ({ getPauses: vi.fn(), place: vi.fn(), resume: vi.fn() }));
vi.mock('$lib/runs', () => ({ skipFile: vi.fn() }));
function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: Error) => void;
	const promise = new Promise<T>((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
}
const empty = (): TitleWork => ({ queued: [], active: [], pauses: [] });
const queued = (): TitleWork => ({
	...empty(),
	queued: [{ run: 'original', path: '/original.mkv', expected: 1, position: 2 }]
});
const paused: Pause = {
	path: '/original.mkv',
	title_id: '',
	title_name: '',
	seconds: null,
	until: null,
	at: '',
	by: ''
};
beforeEach(() => {
	vi.resetAllMocks();
	harness.watches.length = 0;
	vi.mocked(getPauses).mockResolvedValue([]);
	vi.mocked(getTitleWork).mockResolvedValue(empty());
	vi.mocked(place).mockResolvedValue([]);
	vi.mocked(resume).mockResolvedValue([]);
});
function setup() {
	const controller = new TitleWorkController();
	const reload = vi.fn().mockResolvedValue(undefined);
	controller.open({ id: 'a', reload, current: () => true });
	return { controller, reload };
}

test('queue answer supersedes a late initial pause lookup, including failure', async () => {
	for (const fail of [false, true]) {
		const lookup = deferred<Pause[]>();
		vi.mocked(getPauses).mockReturnValueOnce(lookup.promise);
		const { controller } = setup();
		vi.mocked(getTitleWork).mockResolvedValueOnce({ ...empty(), pauses: [paused] });
		await controller.readWork();
		if (fail) lookup.reject(new Error('offline'));
		else lookup.resolve([]);
		await Promise.resolve();
		expect(controller.pauses).toEqual([paused]);
	}
});

test('obsolete queue success cannot trigger a verdict refresh after a newer action', async () => {
	const { controller, reload } = setup();
	controller.work = queued();
	const old = deferred<TitleWork>();
	vi.mocked(getTitleWork).mockReturnValueOnce(old.promise);
	const reading = controller.readWork();
	vi.mocked(getTitleWork).mockResolvedValueOnce(queued());
	await controller.queueAct('top');
	old.resolve(empty());
	await reading;
	expect(reload).not.toHaveBeenCalled();
	expect(controller.work?.queued).toHaveLength(1);
});

test('work disappearance refreshes verdicts first and rechecks ownership afterwards', async () => {
	const { controller, reload } = setup();
	controller.work = queued();
	const verdicts = deferred<void>();
	reload.mockReturnValueOnce(verdicts.promise);
	const reading = controller.readWork();
	await Promise.resolve();
	expect(reload).toHaveBeenCalledOnce();
	expect(controller.work?.queued).toHaveLength(1);
	controller.open({ id: 'b', reload, current: () => true });
	verdicts.resolve();
	await reading;
	expect(controller.work).toBeUndefined();
});

test('same-title reopen rejects old work failures and preserves a newer busy action', async () => {
	const { controller, reload } = setup();
	const old = deferred<TitleWork>();
	vi.mocked(getTitleWork).mockReturnValueOnce(old.promise);
	const reading = controller.readWork();
	controller.close();
	controller.open({ id: 'a', reload, current: () => true });
	const pause = deferred<Pause[]>();
	vi.mocked(place).mockReturnValueOnce(pause.promise);
	const action = controller.keep(3600);
	old.reject(new Error('old'));
	await reading;
	expect(controller.workError).toBe('');
	expect(controller.workBusy).toBe(true);
	pause.resolve([]);
	await action;
	expect(controller.workBusy).toBe(false);
});

test('accepted title pause cancels captured work after close without reading the new title', async () => {
	const { controller, reload } = setup();
	controller.work = queued();
	const pause = deferred<Pause[]>();
	vi.mocked(place).mockReturnValueOnce(pause.promise);
	const action = controller.keep(3600);
	controller.close();
	controller.open({ id: 'b', reload, current: () => true });
	pause.resolve([paused]);
	await action;
	expect(place).toHaveBeenCalledWith({ ids: ['a'] }, 3600);
	expect(queueAction).toHaveBeenCalledWith('skip', queued().queued);
	expect(getTitleWork).not.toHaveBeenCalled();
	expect(controller.pauses).toEqual([]);
});

test.each([false, true])(
	'stale resume completion (failure=%s) leaves the later session alone',
	async (fail) => {
		const { controller, reload } = setup();
		const old = deferred<Pause[]>();
		vi.mocked(resume).mockReturnValueOnce(old.promise);
		const action = controller.release(paused);
		controller.open({ id: 'a', reload, current: () => true });
		const pending = deferred<Pause[]>();
		vi.mocked(place).mockReturnValueOnce(pending.promise);
		const newer = controller.keep(3600);
		if (fail) old.reject(new Error('old'));
		else old.resolve([paused]);
		await action;
		expect(resume).toHaveBeenCalledWith({ paths: ['/original.mkv'] });
		expect(controller.workBusy).toBe(true);
		expect(controller.pauseBusy).toBe('3600');
		expect(controller.pauseError).toBe('');
		pending.resolve([]);
		await newer;
	}
);

test.each(['top', 'skip'] as const)(
	'late %s retains page notification but cannot refresh or clear new busy state',
	async (kind) => {
		const { controller, reload } = setup();
		controller.work = queued();
		const pending = deferred<{ changed?: number }>();
		vi.mocked(queueAction).mockReturnValueOnce(pending.promise);
		const notify = vi.fn();
		const action = controller.queueAct(kind, notify);
		controller.open({ id: 'b', reload, current: () => true });
		const pause = deferred<Pause[]>();
		vi.mocked(place).mockReturnValueOnce(pause.promise);
		const newer = controller.keep(0);
		pending.reject(new Error('old'));
		await action;
		expect(notify).toHaveBeenCalledOnce();
		expect(controller.workBusy).toBe(true);
		expect(controller.pauseError).toBe('');
		expect(getTitleWork).not.toHaveBeenCalled();
		pause.resolve([]);
		await newer;
	}
);

test('per-file pause captures queue and active targets before awaiting, and invalidates row feedback', async () => {
	const { controller, reload } = setup();
	const standing = fileState(queued(), '/original.mkv');
	standing.running = [{ run: 'active-run', path: '/original.mkv' } as TitleWork['active'][number]];
	const pending = deferred<Pause[]>();
	vi.mocked(place).mockReturnValueOnce(pending.promise);
	const operation = controller.fileAction('/original.mkv', standing, 'pause', 3600);
	const action = operation.run();
	standing.waiting[0].path = '/changed.mkv';
	standing.running[0].run = 'changed-run';
	controller.open({ id: 'b', reload, current: () => true });
	pending.resolve([]);
	await action;
	expect(queueAction).toHaveBeenCalledWith('skip', queued().queued);
	expect(skipFile).toHaveBeenCalledWith('active-run', '/original.mkv');
	expect(operation.current()).toBe(false);
	expect(getTitleWork).not.toHaveBeenCalled();
});

test('current per-file failure releases busy state, refreshes work and remains reportable', async () => {
	const { controller } = setup();
	vi.mocked(place).mockRejectedValueOnce(new Error('refused'));
	const operation = controller.fileAction(
		'/original.mkv',
		fileState(empty(), '/original.mkv'),
		'pause'
	);
	await expect(operation.run()).rejects.toThrow('refused');
	expect(operation.current()).toBe(true);
	expect(controller.workBusy).toBe(false);
	expect(getTitleWork).toHaveBeenCalledWith('a');
});

test('poll lifecycle and disposal invalidate pending reads and stop once', async () => {
	const { controller, reload } = setup();
	const watch = harness.watches[0];
	expect(watch.ready()).toBe(true);
	expect(watch.pace()).toBe(5000);
	const pending = deferred<TitleWork>();
	vi.mocked(getTitleWork).mockReturnValueOnce(pending.promise);
	controller.work = queued();
	const reading = watch.ask();
	controller.dispose();
	controller.dispose();
	pending.resolve(empty());
	await reading;
	controller.open({ id: 'b', reload, current: () => true });
	expect(watch.ready()).toBe(false);
	expect(harness.stop).toHaveBeenCalledOnce();
	expect(reload).not.toHaveBeenCalled();
});

test('current title pause failure remains retryable and title resume uses title scope', async () => {
	const { controller } = setup();
	vi.mocked(place).mockRejectedValueOnce(new Error('offline'));
	await expect(controller.keep(0)).rejects.toThrow('offline');
	expect(controller.workBusy).toBe(false);
	expect(controller.pauseBusy).toBe('');
	await controller.keep(0);
	await controller.release({ ...paused, title_id: 'a' });
	expect(resume).toHaveBeenCalledWith({ ids: ['a'] });
});

test.each(['top', 'skip', 'resume'] as const)(
	'per-file %s returns feedback only to its original operation',
	async (kind) => {
		const { controller, reload } = setup();
		const operation = controller.fileAction(
			'/original.mkv',
			fileState(queued(), '/original.mkv'),
			kind
		);
		await operation.run();
		expect(operation.current()).toBe(true);
		expect(controller.workBusy).toBe(false);
		if (kind === 'resume') expect(resume).toHaveBeenCalledWith({ paths: ['/original.mkv'] });
		else expect(queueAction).toHaveBeenCalledWith(kind, queued().queued);
		controller.open({ id: 'a', reload, current: () => true });
		expect(operation.current()).toBe(false);
	}
);

test.each([false, true])(
	'shared browsing invalidation rejects pending work (failure=%s)',
	async (fail) => {
		const controller = new TitleWorkController();
		let current = true;
		const reload = vi.fn().mockResolvedValue(undefined);
		controller.open({ id: 'a', current: () => current, reload });
		controller.work = queued();
		const pending = deferred<TitleWork>();
		vi.mocked(getTitleWork).mockReturnValueOnce(pending.promise);
		const reading = controller.readWork();
		current = false;
		if (fail) pending.reject(new Error('old'));
		else pending.resolve(empty());
		await reading;
		expect(reload).not.toHaveBeenCalled();
		expect(controller.workError).toBe('');
		expect(controller.work?.queued).toHaveLength(1);
		await controller.keep(0);
		expect(place).not.toHaveBeenCalled();
		expect(harness.watches[0].ready()).toBe(false);
	}
);

test('close stops polling and reopen installs an independent poller', () => {
	const { controller, reload } = setup();
	controller.close();
	expect(harness.stop).toHaveBeenCalledOnce();
	controller.open({ id: 'a', reload, current: () => true });
	expect(harness.watches).toHaveLength(2);
	controller.dispose();
	expect(harness.stop).toHaveBeenCalledTimes(2);
});
