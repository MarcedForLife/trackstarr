import { beforeEach, expect, test, vi } from 'vitest';
import { TitleBrowsing } from '$lib/title-browsing.svelte';
import { getLinks, getTitle, type LibraryFile, type TitleDetail } from '$lib/library';

const harness = vi.hoisted(() => ({
	watches: [] as { ask: () => Promise<void>; ready: () => boolean }[],
	stop: vi.fn(),
	mark: vi.fn()
}));
vi.mock('$lib/poll', () => ({
	poll: (watch: (typeof harness.watches)[number]) => {
		harness.watches.push(watch);
		return { stop: harness.stop, mark: harness.mark };
	}
}));
vi.mock('$lib/library', async (original) => ({
	...(await original<typeof import('$lib/library')>()),
	getTitle: vi.fn(),
	getLinks: vi.fn()
}));
function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: Error) => void;
	const promise = new Promise<T>((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
}
const card = (id = 'a') => ({ id, name: id, kind: 'series', state: 'pending' as const });
function detail(id = 'a', count = 30): TitleDetail {
	return {
		...card(id),
		folder: `/media/${id}`,
		current: true,
		total: 100,
		folders: [{ source: '', folder: `/media/${id}` }],
		servers: [],
		files: Array.from({ length: count }, (_, at): LibraryFile => ({
			path: `/media/${id}/Show - S${at < 15 ? '01' : '02'}E${String(at + 1).padStart(2, '0')}.mkv`,
			name: `Show - S${at < 15 ? '01' : '02'}E${String(at + 1).padStart(2, '0')}.mkv`,
			status: 'pending',
			bytes: 1,
			seconds: 3600,
			why: {},
			tracks: [],
			planned: []
		}))
	};
}
beforeEach(() => {
	vi.resetAllMocks();
	harness.watches.length = 0;
	vi.mocked(getTitle).mockImplementation(async (id) => detail(id));
	vi.mocked(getLinks).mockResolvedValue([]);
});

test('reading survives refresh and a failed retry of the title, and resets for another', async () => {
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	browsing.more();
	browsing.fold(browsing.grouped![0], 0);
	browsing.reading.selectedPaths.episode = '/chosen.mkv';
	await browsing.loadMore();
	await browsing.reload();
	const kept = {
		pages: 2,
		renderedGroups: 24,
		expandedSeasons: [],
		selectedPaths: { episode: '/chosen.mkv' }
	};
	expect(browsing.reading).toEqual(kept);
	vi.mocked(getTitle).mockRejectedValueOnce(new Error('offline'));
	await browsing.open(browsing.opened!);
	expect(browsing.failure).toBeTruthy();
	expect(browsing.reading).toEqual(kept);
	await browsing.open(browsing.opened!);
	expect(getTitle).toHaveBeenLastCalledWith('a', fetch, 2);
	expect(browsing.failure).toBe('');
	await browsing.open(card('b'));
	expect(browsing.reading).toEqual({
		pages: 1,
		renderedGroups: 12,
		expandedSeasons: null,
		selectedPaths: {}
	});
	browsing.close();
	await browsing.open(card('b'));
	expect(browsing.reading.pages).toBe(1);
});

test('failed pagination retains prefix and depth; retry replaces it cumulatively', async () => {
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	const previous = browsing.detail;
	vi.mocked(getTitle).mockRejectedValueOnce(new Error('offline'));
	await browsing.loadMore();
	expect(browsing.detail).toBe(previous);
	expect(browsing.reading.pages).toBe(1);
	expect(browsing.moreError).toBeTruthy();
	vi.mocked(getTitle).mockResolvedValueOnce(detail('a', 40));
	await browsing.loadMore();
	expect(getTitle).toHaveBeenLastCalledWith('a', fetch, 2);
	expect(browsing.detail?.files).toHaveLength(40);
	expect(browsing.reading.pages).toBe(2);
	expect(browsing.moreError).toBe('');
});

test.each([0, 30])('failed refresh retains %i files and retry clears feedback', async (count) => {
	vi.mocked(getTitle).mockResolvedValueOnce(detail('a', count));
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	const previous = browsing.detail;
	vi.mocked(getTitle).mockRejectedValueOnce(new Error('offline'));
	await browsing.reload();
	expect(browsing.detail).toBe(previous);
	expect(browsing.refreshError).toBeTruthy();
	await browsing.reload();
	expect(browsing.refreshError).toBe('');
});

test.each(['success', 'failure'])(
	'old initial %s cannot settle a newer opening',
	async (outcome) => {
		const old = deferred<TitleDetail>();
		const latest = deferred<TitleDetail>();
		vi.mocked(getTitle).mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
		const browsing = new TitleBrowsing();
		const first = browsing.open(card());
		const second = browsing.open(card('b'));
		if (outcome === 'success') old.resolve(detail());
		else old.reject(new Error('offline'));
		await first;
		expect(browsing.loading).toBe(true);
		expect(browsing.detail).toBeNull();
		expect(browsing.failure).toBe('');
		latest.resolve(detail('b'));
		await second;
		expect(browsing.detail?.id).toBe('b');
	}
);

test('pagination supersedes refresh, blocks duplicate loads and commits only its response', async () => {
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	const stale = deferred<TitleDetail>();
	const more = deferred<TitleDetail>();
	vi.mocked(getTitle).mockReturnValueOnce(stale.promise).mockReturnValueOnce(more.promise);
	const refresh = browsing.reload();
	const loading = browsing.loadMore();
	await browsing.reload();
	await browsing.loadMore();
	expect(getTitle).toHaveBeenCalledTimes(3);
	more.resolve(detail('a', 40));
	await loading;
	stale.reject(new Error('obsolete'));
	await refresh;
	expect(browsing.detail?.files).toHaveLength(40);
	expect(browsing.refreshError).toBe('');
});

test.each(['success', 'failure'])(
	'quick same-title reopen rejects old detail/link %s and resets reading',
	async (outcome) => {
		const browsing = new TitleBrowsing();
		const links = deferred<Awaited<ReturnType<typeof getLinks>>>();
		vi.mocked(getLinks).mockReturnValueOnce(links.promise);
		await browsing.open(card());
		await browsing.loadMore();
		browsing.more();
		browsing.reading.selectedPaths.episode = '/chosen.mkv';
		const stale = deferred<TitleDetail>();
		vi.mocked(getTitle).mockReturnValueOnce(stale.promise);
		const pending = browsing.reload();
		browsing.close();
		await browsing.open(card());
		browsing.clear(); // A late closing callback cannot empty a reopened sheet.
		if (outcome === 'success') {
			stale.resolve(detail('a', 50));
			links.resolve([{ server: 'plex', label: 'Stale', url: 'https://example.com' }]);
		} else {
			stale.reject(new Error('obsolete'));
			links.reject(new Error('obsolete'));
		}
		await pending;
		expect(browsing.detail?.files).toHaveLength(30);
		expect(browsing.links).toEqual([]);
		expect(browsing.refreshError).toBe('');
		expect(browsing.reading).toEqual({
			pages: 1,
			renderedGroups: 12,
			expandedSeasons: null,
			selectedPaths: {}
		});
	}
);

test('switching during pagination protects new busy state and page depth', async () => {
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	const old = deferred<TitleDetail>();
	vi.mocked(getTitle).mockReturnValueOnce(old.promise);
	const first = browsing.loadMore();
	await browsing.open(card('b'));
	const latest = deferred<TitleDetail>();
	vi.mocked(getTitle).mockReturnValueOnce(latest.promise);
	const second = browsing.loadMore();
	old.reject(new Error('obsolete'));
	await first;
	expect(browsing.loadingMore).toBe(true);
	expect(browsing.moreError).toBe('');
	expect(browsing.reading.pages).toBe(1);
	latest.resolve(detail('b', 40));
	await second;
	expect(browsing.reading.pages).toBe(2);
});

test('disposal without close stops polling and ignores pending reads and later calls', async () => {
	const browsing = new TitleBrowsing();
	const pending = deferred<TitleDetail>();
	const links = deferred<Awaited<ReturnType<typeof getLinks>>>();
	vi.mocked(getTitle).mockReturnValueOnce(pending.promise);
	vi.mocked(getLinks).mockReturnValueOnce(links.promise);
	const opening = browsing.open(card());
	browsing.dispose();
	browsing.dispose();
	pending.resolve(detail());
	links.resolve([]);
	await opening;
	await harness.watches[0].ask();
	await browsing.open(card('b'));
	await browsing.loadMore();
	expect(harness.stop).toHaveBeenCalledTimes(1);
	expect(harness.watches[0].ready()).toBe(false);
	expect(getTitle).toHaveBeenCalledTimes(1);
	expect(browsing.opened).toBeNull();
	expect(browsing.detail).toBeNull();
	expect(browsing.links).toBeNull();
});

test('fill cannot replace another library header and links settle independently', async () => {
	const pending = deferred<TitleDetail>();
	vi.mocked(getTitle).mockReturnValueOnce(pending.promise);
	const browsing = new TitleBrowsing();
	const opening = browsing.open(card());
	await Promise.resolve();
	expect(browsing.links).toEqual([]);
	expect(browsing.loading).toBe(true);
	browsing.fill(card('b'));
	expect(browsing.opened?.id).toBe('a');
	browsing.fill({ ...card(), name: 'Fresh name' });
	pending.resolve(detail());
	await opening;
	expect(browsing.opened?.name).toBe('Fresh name');
	expect(harness.watches[0].ready()).toBe(true);
	browsing.close();
	expect(harness.watches[0].ready()).toBe(false);
});

test('a captured session cannot refresh after switch, quick reopen or disposal', async () => {
	const browsing = new TitleBrowsing();
	await browsing.open(card());
	const first = browsing.session!;
	await browsing.open(card('b'));
	const second = browsing.session!;
	browsing.close();
	await browsing.open(card('b'));
	browsing.clear();
	expect(browsing.detail?.id).toBe('b');
	expect(browsing.reading).toEqual({
		pages: 1,
		renderedGroups: 12,
		expandedSeasons: null,
		selectedPaths: {}
	});
	const third = browsing.session!;
	browsing.dispose();
	vi.mocked(getTitle).mockClear();
	for (const session of [first, second, third]) {
		expect(session.current()).toBe(false);
		await session.reload();
	}
	expect(getTitle).not.toHaveBeenCalled();
});
