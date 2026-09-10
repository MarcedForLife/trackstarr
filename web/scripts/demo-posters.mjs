// Fetch the demo's posters from Wikimedia Commons into src/lib/demo/posters,
// which is gitignored: the images are the Commons' to serve and the repo's to
// name, not to hold. src/lib/demo/posters.json says which file each title
// takes and under what licence; a file whose licence on Commons no longer
// matches is left out, as is anything that fails to arrive, and the demo
// draws a tile for it instead. Run by the demo workflow before the build, and
// by `npm run posters:demo` for a local look.

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const manifestPath = join(here, '..', 'src', 'lib', 'demo', 'posters.json');
const outDir = join(here, '..', 'src', 'lib', 'demo', 'posters');

// Commons asks that a client say who it is.
const AGENT = 'trackstarr-demo (https://github.com/MarcedForLife/trackstarr)';
const API = 'https://commons.wikimedia.org/w/api.php';

// Wide enough for a card on a high-density phone, small enough to ship, and
// both sizes Commons keeps ready: it refuses widths off its list. A PNG is
// several times the bytes of a JPEG at one width, so it is asked for smaller.
const WIDTH = 330;
const PNG_WIDTH = 250;

// How many titles one API call may name.
const BATCH = 50;

/** A title id as its poster's file stem: slug() in ../src/lib/demo/util.ts. */
function slug(id) {
	return id
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-|-$/g, '');
}

/** The Commons imageinfo for these file titles, keyed by the title asked for. */
async function lookUp(titles) {
	const query = new URLSearchParams({
		action: 'query',
		prop: 'imageinfo',
		titles: titles.join('|'),
		iiprop: 'url|extmetadata',
		iiurlwidth: String(WIDTH),
		format: 'json'
	});
	const response = await fetch(`${API}?${query}`, { headers: { 'user-agent': AGENT } });
	if (!response.ok) throw new Error(`Commons answered ${response.status}`);
	const { query: answer } = await response.json();
	// The API tidies titles; map its spelling back to ours.
	const asked = Object.fromEntries((answer.normalized ?? []).map(({ from, to }) => [to, from]));
	const found = {};
	for (const page of Object.values(answer.pages)) {
		const info = page.imageinfo?.[0];
		if (!info) continue;
		const licence = info.extmetadata?.LicenseShortName?.value ?? '';
		// The API answers at WIDTH, with tracking on the end; a PNG is asked for
		// again at PNG_WIDTH.
		const url = (info.thumburl ?? info.url)
			.split('?')[0]
			.replace(/\/\d+px-(.*\.png)$/i, `/${PNG_WIDTH}px-$1`);
		found[asked[page.title] ?? page.title] = { url, licence };
	}
	return found;
}

async function main() {
	const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
	const entries = Object.entries(manifest);
	await mkdir(outDir, { recursive: true });
	const found = {};
	for (let at = 0; at < entries.length; at += BATCH) {
		const titles = entries.slice(at, at + BATCH).map(([, poster]) => poster.file);
		Object.assign(found, await lookUp(titles));
	}
	let fetched = 0;
	for (const [id, poster] of entries) {
		const info = found[poster.file];
		if (!info) {
			console.warn(`${id}: ${poster.file} is not on Commons`);
			continue;
		}
		if (info.licence !== poster.licence) {
			console.warn(`${id}: ${poster.file} is ${info.licence} on Commons, not ${poster.licence}`);
			continue;
		}
		const response = await fetch(info.url, { headers: { 'user-agent': AGENT } });
		if (!response.ok) {
			console.warn(`${id}: ${info.url} answered ${response.status}`);
			continue;
		}
		const extension =
			new URL(info.url).pathname.match(/\.(jpe?g|png)$/i)?.[1]?.toLowerCase() ?? 'jpg';
		await writeFile(
			join(outDir, `${slug(id)}.${extension === 'jpeg' ? 'jpg' : extension}`),
			Buffer.from(await response.arrayBuffer())
		);
		fetched += 1;
	}
	console.log(`${fetched} of ${entries.length} posters in ${outDir}`);
}

main().catch((error) => {
	// The demo stands without its posters, so a Commons outage is a warning.
	console.warn(`posters not fetched: ${error.message}`);
});
