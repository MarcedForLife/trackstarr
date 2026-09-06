// Serves web/build the way the image does: static files, index.html for
// unknown paths, /api handed to the listener. So a measurement runs against
// the real bundle instead of Vite's unbundled dev modules.
//
//   node tools/uireview/serve-build.mjs
//   PORT=5191 BUILD=/elsewhere/build node tools/uireview/serve-build.mjs
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { join, extname } from 'node:path';
import { root } from './harness.mjs';

const build = process.env.BUILD ?? join(root, 'web', 'build');
const api = Number(process.env.TRACKSTARR_API_PORT) || 5120;
const port = Number(process.env.PORT) || 5190;

const TYPES = {
	'.html': 'text/html',
	'.js': 'text/javascript',
	'.css': 'text/css',
	'.svg': 'image/svg+xml',
	'.png': 'image/png',
	'.woff2': 'font/woff2',
	'.json': 'application/json',
	'.webmanifest': 'application/manifest+json'
};

createServer(async (req, res) => {
	const url = new URL(req.url, 'http://x');
	if (['/api', '/health', '/webhook'].some((p) => url.pathname.startsWith(p))) {
		const upstream = await fetch(`http://127.0.0.1:${api}${req.url}`, {
			method: req.method,
			headers: { ...req.headers, host: `127.0.0.1:${api}` },
			body: ['GET', 'HEAD'].includes(req.method) ? undefined : req,
			duplex: 'half',
			redirect: 'manual'
		});
		// fetch() has already gunzipped the body, so passing the listener's own
		// content-encoding on earns an ERR_CONTENT_DECODING_FAILED. The length is
		// wrong for the same reason. Both are this hop's to state.
		const headers = Object.fromEntries(upstream.headers);
		delete headers['content-encoding'];
		delete headers['content-length'];
		const body = Buffer.from(await upstream.arrayBuffer());
		res.writeHead(upstream.status, { ...headers, 'content-length': body.length });
		res.end(body);
		return;
	}
	for (const path of [join(build, url.pathname), join(build, 'index.html')]) {
		try {
			const body = await readFile(path);
			res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' });
			res.end(body);
			return;
		} catch {
			/* fall through to the SPA fallback */
		}
	}
	res.writeHead(404).end();
}).listen(port, () => console.log(`build on http://localhost:${port} → api ${api}`));
