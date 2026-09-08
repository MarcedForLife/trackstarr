// The real build behind a stub service, so a probe drives what ships rather
// than Vite's unbundled dev modules. Static files with index.html for a route
// the adapter did not prerender, the way the image serves them, and the API
// answered from the contract fixtures pytest wrote: the service's own answers,
// kept current by tests/test_contract.py. A probe hands in answers of its own
// for the paths it needs to bend.
//
//   ROOT=/path/to/another/build    serve a different bundle, for a "before"
//   ENGINES=firefox                one engine rather than both

import { createServer } from 'node:http';
import { readdir, readFile } from 'node:fs/promises';
import { basename, extname, join, normalize } from 'node:path';

const HERE = new URL('./', import.meta.url).pathname;
const ROOT = process.env.ROOT ? `${process.env.ROOT}/` : join(HERE, '../build/');
const FIXTURES = join(HERE, '../src/fixtures');

export const ENGINES = (process.env.ENGINES ?? 'chromium,firefox').split(',');

const TYPES = {
	'.html': 'text/html',
	'.js': 'text/javascript',
	'.css': 'text/css',
	'.json': 'application/json',
	'.svg': 'image/svg+xml',
	'.woff2': 'font/woff2',
	'.png': 'image/png',
	'.ico': 'image/x-icon'
};

/** The service's recorded answers, by fixture name. */
export async function fixtures() {
	const names = await readdir(FIXTURES);
	const read = names.map(async (name) => [
		basename(name, '.json'),
		JSON.parse(await readFile(join(FIXTURES, name), 'utf8'))
	]);
	return Object.fromEntries(await Promise.all(read));
}

// A 500x750 cover, the size the app asks for, generated once and served for
// every title. A BMP rather than a JPEG because both engines decode one and
// writing it takes no dependency: 24-bit, bottom-up, which is what the format
// wants. The decode is real, which is what a paint measurement needs.
function cover() {
	const width = 500;
	const height = 750;
	const row = width * 3 + ((4 - ((width * 3) % 4)) % 4);
	const body = Buffer.alloc(row * height);
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			const at = y * row + x * 3;
			const up = height - 1 - y;
			body[at] = (x * 3) % 255;
			body[at + 1] = (up * 5) % 255;
			body[at + 2] = (x * 7 + up * 3) % 255;
		}
	}
	const head = Buffer.alloc(54);
	head.write('BM');
	head.writeUInt32LE(54 + body.length, 2);
	head.writeUInt32LE(54, 10);
	head.writeUInt32LE(40, 14);
	head.writeInt32LE(width, 18);
	head.writeInt32LE(height, 22);
	head.writeUInt16LE(1, 26);
	head.writeUInt16LE(24, 28);
	head.writeUInt32LE(body.length, 34);
	return Buffer.concat([head, body]);
}

function json(response, body) {
	response.setHeader('content-type', 'application/json');
	response.end(JSON.stringify(body));
}

async function file(response, path) {
	const wanted = path === '/' ? '/index.html' : path;
	for (const candidate of [wanted, `${wanted}.html`, `${wanted}/index.html`, '/index.html']) {
		try {
			const found = await readFile(join(ROOT, normalize(candidate)));
			response.setHeader('content-type', TYPES[extname(candidate)] ?? 'application/octet-stream');
			return response.end(found);
		} catch {
			// Try the next shape of the same path.
		}
	}
	response.statusCode = 404;
	response.end('nothing here');
}

/**
 * Serve the build on `port`. `answers` maps a pathname to a JSON body, or to
 * a function of the request and the response that returns one and may set
 * the status on its way. It is laid over the fixtures in `shape`, so a probe
 * names only the paths it bends. Any other `/api/` path answers an empty
 * object, the cover a bitmap, and the stream stays open and silent.
 */
export async function serve(port, shape, answers = {}) {
	const answer = {
		'/api/auth/me': { name: 'probe', role: 'admin', must_change: false },
		'/api/runs': shape.runs,
		'/api/library': shape.library,
		'/api/library/summary': shape.summary,
		'/api/library/title': shape.title,
		'/api/library/links': [],
		'/api/events': shape.events,
		'/api/holds': shape.holds,
		'/api/settings': shape.settings,
		...answers
	};
	const image = cover();

	const server = createServer(async (request, response) => {
		const path = new URL(request.url, 'http://localhost').pathname;
		if (path in answer) {
			const found = answer[path];
			return json(response, typeof found === 'function' ? await found(request, response) : found);
		}
		if (path === '/api/library/cover') {
			response.setHeader('content-type', 'image/bmp');
			response.setHeader('cache-control', 'no-store');
			return response.end(image);
		}
		if (path === '/api/stream') {
			response.writeHead(200, {
				'content-type': 'text/event-stream',
				'cache-control': 'no-cache',
				connection: 'keep-alive'
			});
			return response.write(': open\n\n');
		}
		if (path.startsWith('/api/')) return json(response, {});
		return file(response, path);
	});
	await new Promise((done) => server.listen(port, done));
	return { site: `http://localhost:${port}`, close: () => server.close() };
}

/** Hold the process open on the served build, for a browser of your own. */
export async function hold(site) {
	console.log(`serving the real build with a stub API on ${site}`);
	await new Promise(() => {});
}
