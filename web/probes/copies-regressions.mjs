// Keep this CI entry point stable; every scenario gets a fresh fixture and page.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, serve } from './stub.mjs';
import { copyFixture } from './copy-regressions/fixtures.mjs';
import { responseDelays } from './copy-regressions/delays.mjs';
import { selectionRegressions } from './copy-regressions/selection.mjs';
import { readingRegressions } from './copy-regressions/reading.mjs';
import { connectionRegressions } from './copy-regressions/connections.mjs';
import { pauseRegressions, pauseIdentityRegressions } from './copy-regressions/pause.mjs';
import { actionSessionRegressions } from './copy-regressions/actions.mjs';
import { readSessionRegressions } from './copy-regressions/reads.mjs';
import { editorSessionRegressions } from './copy-regressions/editor.mjs';
import { mappingRegressions } from './copy-regressions/mapping.mjs';

const scenarios = {
	selection: selectionRegressions,
	reading: readingRegressions,
	connections: connectionRegressions,
	pause: pauseRegressions,
	'pause-identity': pauseIdentityRegressions,
	'title-actions': actionSessionRegressions,
	'file-actions': (page, fixture) => actionSessionRegressions(page, fixture, true),
	reads: readSessionRegressions,
	editor: editorSessionRegressions,
	mapping: mappingRegressions
};
const selected = process.env.SCENARIOS?.split(',') ?? Object.keys(scenarios);
for (const name of selected) assert.ok(scenarios[name], `Unknown copy scenario: ${name}`);

for (const engine of ENGINES) {
	const browser = await playwright[engine].launch();
	try {
		for (const width of [390, 1280]) {
			for (const name of selected) {
				console.log(`${engine} ${width}: ${name}`);
				const fixture = await copyFixture();
				const { site, close } = await serve(5206, fixture.shape, fixture.answers);
				const delays = responseDelays();
				const context = await browser.newContext({ viewport: { width, height: 915 } });
				try {
					const page = await context.newPage();
					const errors = [];
					page.on('pageerror', (error) => errors.push(error.message));
					try {
						await scenarios[name](page, { ...fixture, site, delays, width, engine });
						assert.deepEqual(errors, [], 'browser runtime errors');
					} finally {
						delays.releaseAll();
						await page.unrouteAll({ behavior: 'wait' });
					}
				} catch (error) {
					throw new Error(`${engine} ${width} ${name}: ${error.message}`, { cause: error });
				} finally {
					await context.close();
					close();
				}
			}
		}
		console.log(`${engine}: ${selected.length} copy scenarios passed at 390/1280px`);
	} finally {
		await browser.close();
	}
}
