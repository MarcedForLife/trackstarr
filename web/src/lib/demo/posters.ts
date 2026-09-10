// Poster art for the demo's titles, drawn here rather than fetched: the real
// posters belong to their studios, and the demo ships nothing it cannot
// licence. Each title gets its own hue from its name, a device for its kind and
// its name set in type, as a data URI the cards load like any other cover.

export type Subject = { name: string; year?: number; kind: string };

// Built up by the world as it registers titles; null until then, so importing
// this module costs the normal build nothing.
let known: Map<string, Subject> | null = null;

export function register(id: string, subject: Subject): void {
	known ??= new Map();
	known.set(id, subject);
}

/** A stable number from a name, so a title keeps its colour across loads. */
function hash(text: string): number {
	let value = 2166136261;
	for (const char of text) value = Math.imul(value ^ char.codePointAt(0)!, 16777619);
	return value >>> 0;
}

function escape(text: string): string {
	return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

// The poster's proportions, and where the name sits: the *arrs' own 2:3.
const WIDTH = 300;
const HEIGHT = 450;

/** A name as lines that fit the poster's width at `size` pixels: a bold face
 * runs about half a pixel of width per pixel of size. */
function wrap(name: string, size: number): string[] {
	const fits = Math.floor((WIDTH - 48) / (size * 0.56));
	const lines: string[] = [];
	let line = '';
	for (const word of name.split(' ')) {
		const joined = line ? `${line} ${word}` : word;
		if (joined.length > fits && line) {
			lines.push(line);
			line = word;
		} else {
			line = joined;
		}
	}
	if (line) lines.push(line);
	return lines;
}

/** The device behind the name, by kind: a disc for a film, bands for a series,
 * a frame for a folder the sweep found on its own. */
function device(kind: string, hue: number): string {
	const tint = `hsl(${(hue + 30) % 360} 70% 70%)`;
	if (kind === 'series') {
		return [0, 1, 2]
			.map(
				(band) =>
					`<rect x="0" y="${118 + band * 44}" width="${WIDTH}" height="18" fill="${tint}" opacity="0.18"/>`
			)
			.join('');
	}
	if (kind === 'folder') {
		return `<rect x="34" y="60" width="${WIDTH - 68}" height="${HEIGHT - 150}" rx="6" fill="none" stroke="${tint}" stroke-opacity="0.35" stroke-width="3"/>`;
	}
	return `<circle cx="${WIDTH * 0.68}" cy="150" r="96" fill="${tint}" opacity="0.22"/>`;
}

function draw(subject: Subject): string {
	const hue = hash(subject.name) % 360;
	const size = subject.name.length > 24 ? 22 : 26;
	const lines = wrap(subject.name, size);
	const top = HEIGHT - 44 - lines.length * (size + 4);
	const name = lines
		.map(
			(line, at) =>
				`<text x="24" y="${top + at * (size + 4)}" font-size="${size}" font-weight="700" fill="#fff">${escape(line)}</text>`
		)
		.join('');
	const year = subject.year
		? `<text x="24" y="${HEIGHT - 26}" font-size="14" fill="#fff" opacity="0.7">${subject.year}</text>`
		: '';
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" ` +
		`font-family="system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif">` +
		`<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">` +
		`<stop offset="0" stop-color="hsl(${hue} 42% 30%)"/>` +
		`<stop offset="1" stop-color="hsl(${(hue + 40) % 360} 48% 12%)"/>` +
		`</linearGradient></defs>` +
		`<rect width="${WIDTH}" height="${HEIGHT}" fill="url(#g)"/>` +
		device(subject.kind, hue) +
		`<text x="24" y="${top - 40}" font-size="120" font-weight="800" fill="#fff" opacity="0.14">${escape(subject.name[0] ?? '')}</text>` +
		`<rect x="24" y="${top - size - 14}" width="28" height="3" fill="hsl(38 65% 62%)"/>` +
		name +
		year +
		`</svg>`
	);
}

// Drawn once per title: the grid asks for every card's cover on every render.
let drawn: Map<string, string> | null = null;

/** The poster for a registered title, or a plain tile for anything else. */
export function poster(id: string): string {
	drawn ??= new Map();
	const made = drawn.get(id);
	if (made) return made;
	const subject = known?.get(id) ?? { name: '', kind: 'movie' };
	const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(draw(subject))}`;
	drawn.set(id, url);
	return url;
}
