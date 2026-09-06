import { beforeEach, describe, expect, test } from 'vitest';
import { keep, keepAll, keepFlag, stored, storedAll, storedFlag } from '$lib/prefs';

beforeEach(() => localStorage.clear());

const VERDICTS = ['fixed', 'failed', 'skip'] as const;

describe('stored', () => {
	test('takes a kept word that is still one of the choices', () => {
		localStorage.setItem('order', 'failed');
		expect(stored('order', VERDICTS, 'fixed')).toBe('failed');
	});

	test('falls back for a word that has stopped being one', () => {
		localStorage.setItem('order', 'retired');
		expect(stored('order', VERDICTS, 'fixed')).toBe('fixed');
	});

	test('falls back for a key nobody has written', () => {
		expect(stored('order', VERDICTS, 'fixed')).toBe('fixed');
	});
});

describe('keep', () => {
	test('writes a choice that is not the default', () => {
		keep('order', 'failed', false);
		expect(localStorage.getItem('order')).toBe('failed');
	});

	// The bargain the module makes: a default is no key at all, so changing
	// the default reaches every browser that never chose.
	test('removes the key when the choice is the default', () => {
		localStorage.setItem('order', 'failed');
		keep('order', 'fixed', true);
		expect(localStorage.getItem('order')).toBeNull();
	});
});

describe('flags', () => {
	test('reads a flag that was set, and one that was never written', () => {
		keepFlag('sidebar-collapsed', true);
		expect(localStorage.getItem('sidebar-collapsed')).toBe('1');
		expect(storedFlag('sidebar-collapsed')).toBe(true);
		keepFlag('sidebar-collapsed', false);
		expect(localStorage.getItem('sidebar-collapsed')).toBeNull();
		expect(storedFlag('sidebar-collapsed')).toBe(false);
	});

	// The spelling before prefs.ts owned this key wrote the word either way, so
	// a browser holding the `0` it wrote must not read as the opposite.
	test('reads a kept 0 as off rather than as a key that is there', () => {
		localStorage.setItem('sidebar-collapsed', '0');
		expect(storedFlag('sidebar-collapsed')).toBe(false);
	});
});

describe('storedAll', () => {
	test('keeps the words that are still choices and drops the rest', () => {
		localStorage.setItem('held', 'fixed,retired,skip');
		expect(storedAll('held', VERDICTS)).toEqual(['fixed', 'skip']);
	});

	test('says the same word once', () => {
		localStorage.setItem('held', 'skip,skip');
		expect(storedAll('held', VERDICTS)).toEqual(['skip']);
	});

	test('reads a missing key, and one holding nothing, as all of them', () => {
		expect(storedAll('held', VERDICTS)).toEqual([]);
		localStorage.setItem('held', '');
		expect(storedAll('held', VERDICTS)).toEqual([]);
	});
});

describe('keepAll', () => {
	test('writes the chosen words comma-separated', () => {
		keepAll('held', ['fixed', 'skip']);
		expect(localStorage.getItem('held')).toBe('fixed,skip');
	});

	test('removes the key when nothing is held', () => {
		localStorage.setItem('held', 'skip');
		keepAll('held', []);
		expect(localStorage.getItem('held')).toBeNull();
	});
});
