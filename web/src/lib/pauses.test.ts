import { describe, expect, it } from 'vitest';
import { forTitle, type Pause } from './pauses';

const held: Pause = {
	path: '/movies4k/Film',
	title_id: 'arr:radarr:1',
	title_name: 'Film',
	seconds: null,
	until: null,
	by: 'admin',
	reason: '',
	at: ''
};

describe('title holds across source changes', () => {
	it('keeps a hold visible after a new primary or a merge changes the title ID', () => {
		expect(forTitle([held], 'arr:radarr-4k:7', [{ folder: held.path }])).toBe(held);
		expect(
			forTitle([{ ...held, title_id: 'arr:radarr-4k:7' }], 'arr:radarr:1', [
				{ folder: '/movies/Film' },
				{ folder: held.path }
			])
		).toBeDefined();
	});

	it('uses the opening ID only until source folders are available', () => {
		expect(forTitle([held], held.title_id)).toBe(held);
		expect(forTitle([held], held.title_id, [{ folder: '/replacement/Film' }])).toBeUndefined();
		expect(forTitle([held], held.title_id, [])).toBeUndefined();
	});

	it('does not promote a file hold or a neighbouring folder to a title hold', () => {
		const folders = [{ folder: held.path }];
		expect(forTitle([{ ...held, title_id: '' }], held.title_id, folders)).toBeUndefined();
		expect(
			forTitle([{ ...held, path: held.path + '/film.mkv' }], held.title_id, folders)
		).toBeUndefined();
		expect(forTitle([held], held.title_id, [{ folder: '/movies4k/Film 2' }])).toBeUndefined();
	});
});
