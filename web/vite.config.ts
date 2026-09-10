import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

// One origin for both halves, as in the image: Vite answers on the listener's
// port and proxies the listener's paths to the port dev.py moved it to.
const webPort = Number(process.env.TRACKSTARR_WEB_PORT) || 5120;
const apiPort = Number(process.env.TRACKSTARR_API_PORT) || webPort + 1;

// What the listener owns; everything else is a page.
const apiPaths = ['/api', '/health', '/webhook'];

// Where the pages sit on their host. Empty in the image, which serves them at
// the root; the demo on GitHub Pages sits under the repository's name.
const basePath = process.env.BASE_PATH ?? '';

export default defineConfig(({ mode }) => ({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// A pure SPA: the stdlib server has no Node, so index.html stands in
			// for every route.
			adapter: adapter({ fallback: 'index.html' }),
			paths: { base: basePath as '' | `/${string}` },
			// `vite --mode demo` builds the pages with no service behind them,
			// answered from $lib/demo in the browser. The switch is which file
			// `$demo` names, so the normal build never imports the demo.
			alias: { $demo: mode === 'demo' ? 'src/lib/demo/hooks.ts' : 'src/lib/demo/none.ts' }
		})
	],
	server: {
		port: webPort,
		// Sliding to the next free port would land on the listener's.
		strictPort: true,
		proxy: Object.fromEntries(apiPaths.map((path) => [path, `http://127.0.0.1:${apiPort}`]))
	},
	// The node environment: a component test would want a DOM, and this ships
	// none. The plugins above still resolve $lib and compile runes.
	test: {
		environment: 'node',
		include: ['src/**/*.test.ts'],
		setupFiles: ['./vitest.setup.ts']
	}
}));
