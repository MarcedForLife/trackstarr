import { describe, expect, it } from 'vitest';
import { forTitle, type Pause } from './pauses';

const held: Pause = {
	path: '/movies4k/Film',
	title: 'arr:radarr:1',
	name: 'Film',
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
			forTitle([{ ...held, title: 'arr:radarr-4k:7' }], 'arr:radarr:1', [
				{ folder: '/movies/Film' },
				{ folder: held.path }
			])
		).toBeDefined();
	});

	it('uses the opening ID only until source folders are available', () => {
		expect(forTitle([held], held.title)).toBe(held);
		expect(forTitle([held], held.title, [{ folder: '/replacement/Film' }])).toBeUndefined();
		expect(forTitle([held], held.title, [])).toBeUndefined();
	});

	it('does not promote a file hold or a neighbouring folder to a title hold', () => {
		const folders = [{ folder: held.path }];
		expect(forTitle([{ ...held, title: '' }], held.title, folders)).toBeUndefined();
		expect(
			forTitle([{ ...held, path: held.path + '/film.mkv' }], held.title, folders)
		).toBeUndefined();
		expect(forTitle([held], held.title, [{ folder: '/movies4k/Film 2' }])).toBeUndefined();
	});
});
