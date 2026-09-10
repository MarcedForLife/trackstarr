// Enough of cron to read SWEEP_AT back as moments: the five fields, with `*`,
// steps, ranges and lists. The service does this in Python for the schedule
// check; the demo has to answer the same page itself.

// Each field's range, in order: minute, hour, day of month, month, weekday.
const RANGES: [number, number][] = [
	[0, 59],
	[0, 23],
	[1, 31],
	[1, 12],
	[0, 7]
];

/** The values one field allows, or null for one this reader cannot parse. */
function allowed(field: string, [low, high]: [number, number]): Set<number> | null {
	const values = new Set<number>();
	for (const part of field.split(',')) {
		const [span, stepText] = part.split('/');
		const step = stepText === undefined ? 1 : Number(stepText);
		if (!Number.isInteger(step) || step < 1) return null;
		let from = low;
		let to = high;
		if (span !== '*') {
			const [startText, endText] = span.split('-');
			from = Number(startText);
			to = endText === undefined ? (stepText === undefined ? from : high) : Number(endText);
			if (!Number.isInteger(from) || !Number.isInteger(to) || from < low || to > high || from > to)
				return null;
		}
		for (let value = from; value <= to; value += step) values.add(value);
	}
	return values;
}

type Schedule = {
	minutes: Set<number>;
	hours: Set<number>;
	days: Set<number>;
	months: Set<number>;
	weekdays: Set<number>;
	// Whether the day fields were both narrowed, in which case cron takes a
	// moment either one allows.
	either: boolean;
};

export function parse(expression: string): Schedule | null {
	const fields = expression.trim().split(/\s+/);
	if (fields.length !== 5) return null;
	const sets = fields.map((field, at) => allowed(field, RANGES[at]));
	if (sets.some((set) => set === null)) return null;
	const [minutes, hours, days, months, weekdays] = sets as Set<number>[];
	// Sunday is 0 and 7.
	if (weekdays.has(7)) weekdays.add(0);
	return {
		minutes,
		hours,
		days,
		months,
		weekdays,
		either: fields[2] !== '*' && fields[4] !== '*'
	};
}

function fires(schedule: Schedule, at: Date): boolean {
	if (!schedule.minutes.has(at.getMinutes()) || !schedule.hours.has(at.getHours())) return false;
	if (!schedule.months.has(at.getMonth() + 1)) return false;
	const day = schedule.days.has(at.getDate());
	const weekday = schedule.weekdays.has(at.getDay());
	return schedule.either ? day || weekday : day && weekday;
}

// How far ahead to look before giving up on an expression that never fires.
const HORIZON_MINUTES = 366 * 24 * 60;

/** The next `count` moments the expression fires after `from`, in the browser's
 * clock, or null for an expression this reader cannot parse. */
export function nextRuns(expression: string, from: Date, count: number): Date[] | null {
	const schedule = parse(expression);
	if (!schedule) return null;
	const found: Date[] = [];
	const at = new Date(from);
	at.setSeconds(0, 0);
	for (let step = 0; step < HORIZON_MINUTES && found.length < count; step++) {
		at.setMinutes(at.getMinutes() + 1);
		if (fires(schedule, at)) found.push(new Date(at));
	}
	return found;
}

/** A moment as the service stamps its schedule: local time, no offset. */
export function localStamp(at: Date): string {
	const pad = (part: number) => String(part).padStart(2, '0');
	return (
		`${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
		`T${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
	);
}
