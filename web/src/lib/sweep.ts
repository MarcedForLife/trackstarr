// The sweep API: a live check that reads a schedule back in words and says
// whether its media dirs are mounted. A POST because it carries unsaved values.

import { request } from '$lib/api';

export type DirCheck = {
	path: string;
	// ok, missing (nothing mounted, which the sweep survives) or invalid (a path
	// the setting cannot hold, which the save refuses).
	state: 'ok' | 'missing' | 'invalid';
	detail: string;
};

export type ScheduleCheck = {
	ok: boolean;
	// Stamps in the service's local time, with no offset.
	runs: string[];
	// The zone's name, since the stamps cannot say.
	zone: string;
	error: string;
	dirs: DirCheck[];
};

// Offered by name; the service takes any five-field expression.
export const PRESETS: { value: string; label: string }[] = [
	{ value: '0 4 * * *', label: 'Every night at 04:00' },
	{ value: '0 4 * * 0', label: 'Sunday at 04:00' },
	{ value: '0 */6 * * *', label: 'Every six hours' },
	{ value: '30 2 1 * *', label: 'The 1st of the month at 02:30' }
];

export function checkSweep(at: string, dirs: string[], tz: string): Promise<ScheduleCheck> {
	return request<ScheduleCheck>('/api/sweep/check', {
		method: 'POST',
		body: JSON.stringify({ at, dirs, tz })
	});
}

/** One upcoming run on the service's wall clock. The stamp has no offset, so
 * parsing and formatting in the browser's zone cancel out. */
export function runLabel(stamp: string): string {
	const at = new Date(stamp);
	if (isNaN(at.getTime())) return stamp;
	return at.toLocaleString(undefined, {
		weekday: 'short',
		day: 'numeric',
		month: 'short',
		hour: '2-digit',
		minute: '2-digit'
	});
}
