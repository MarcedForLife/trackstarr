// Enough of cron to read SWEEP_AT back as moments: the five fields, with `*`,
// steps, ranges and lists. The service does this in Python for the schedule
// check; the demo has to answer the same page itself.

// Each field's name and range, in order. The names are the service's, since a
// refusal quotes them back.
const FIELDS: [string, number, number][] = [
	['minute', 0, 59],
	['hour', 0, 23],
	['day of month', 1, 31],
	['month', 1, 12],
	['day of week', 0, 7]
];

/** A whole number, or the refusal the service raises for anything else. */
function number(raw: string, label: string): number {
	const value = Number(raw);
	if (!Number.isInteger(value)) throw new Error(`${label} has a non-number '${raw}'`);
	return value;
}

/** The values one field allows: a star, a number, a range, a step, or a list. */
function allowed(field: string, [label, low, high]: [string, number, number]): Set<number> {
	const values = new Set<number>();
	for (const piece of field.split(',')) {
		const [span, stepText] = piece.split('/');
		const step = stepText === undefined ? 1 : number(stepText, label);
		if (step < 1) throw new Error(`${label} step '${stepText}' must be at least 1`);
		let first: number;
		let last: number;
		if (span === '*') {
			[first, last] = [low, high];
		} else if (span.includes('-')) {
			const [from, to] = span.split('-');
			[first, last] = [number(from, label), number(to, label)];
		} else {
			// A bare number. With a step it opens a range, as Vixie cron does.
			first = number(span, label);
			last = stepText === undefined ? first : high;
		}
		if (!(low <= first && first <= last && last <= high))
			throw new Error(`${label} '${piece}' is outside ${low}-${high}`);
		for (let value = first; value <= last; value += step) values.add(value);
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

/** Throws with the service's own wording, which the sweep page shows. */
export function parse(expression: string): Schedule {
	const fields = expression.trim().split(/\s+/);
	if (fields.length !== FIELDS.length)
		throw new Error('a schedule is 5 fields: minute hour day-of-month month day-of-week');
	const [minutes, hours, days, months, weekdays] = fields.map((field, at) =>
		allowed(field, FIELDS[at])
	);
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

function dayMatches(schedule: Schedule, at: Date): boolean {
	const day = schedule.days.has(at.getDate());
	const weekday = schedule.weekdays.has(at.getDay());
	// Both narrowed means either may fire; an unrestricted field is the whole
	// set, so it never vetoes.
	return schedule.either ? day || weekday : day && weekday;
}

// Nine years, as the service allows, so a Feb 29 schedule finds its leap day.
const HORIZON_DAYS = 366 * 9;

/**
 * The next `count` moments the expression fires after `from`, throwing the
 * service's refusal for one it cannot read or that never fires.
 *
 * Walks a day, then an hour, then a minute, as the service does. A minute at a
 * time over nine years is millions of steps.
 */
export function nextRuns(expression: string, from: Date, count: number): Date[] {
	const schedule = parse(expression);
	const found: Date[] = [];
	const at = new Date(from);
	at.setSeconds(0, 0);
	at.setMinutes(at.getMinutes() + 1);
	const limit = new Date(at);
	limit.setDate(limit.getDate() + HORIZON_DAYS);
	while (found.length < count) {
		if (at >= limit) throw new Error('the schedule never matches a real date');
		if (!schedule.months.has(at.getMonth() + 1) || !dayMatches(schedule, at)) {
			at.setDate(at.getDate() + 1);
			at.setHours(0, 0, 0, 0);
		} else if (!schedule.hours.has(at.getHours())) {
			at.setHours(at.getHours() + 1, 0, 0, 0);
		} else if (!schedule.minutes.has(at.getMinutes())) {
			at.setMinutes(at.getMinutes() + 1);
		} else {
			found.push(new Date(at));
			at.setMinutes(at.getMinutes() + 1);
		}
	}
	return found;
}

/** The next moment it fires, or null where the schedule is unset or unreadable.
 * For a caller with nowhere to show a refusal. */
export function nextRun(expression: string, from: Date): Date | null {
	if (!expression.trim()) return null;
	try {
		return nextRuns(expression, from, 1)[0] ?? null;
	} catch {
		return null;
	}
}

/** A moment as the service stamps its schedule: local time, no offset. */
export function localStamp(at: Date): string {
	const pad = (part: number) => String(part).padStart(2, '0');
	return (
		`${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
		`T${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
	);
}
