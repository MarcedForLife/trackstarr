// Each gate exposes request entry, explicit release and route completion.
// The runner releases every gate in finally before draining route handlers.
export function responseDelays() {
	const pending = new Set();
	return {
		create() {
			let release, start, finish;
			const wait = new Promise((resolve) => (release = resolve));
			const started = new Promise((resolve) => (start = resolve));
			const finished = new Promise((resolve) => (finish = resolve));
			pending.add(release);
			return {
				async started() {
					let timer;
					try {
						await Promise.race([
							started,
							new Promise((_, reject) => {
								timer = setTimeout(() => reject(new Error('Delayed request did not start')), 30000);
							})
						]);
					} finally {
						clearTimeout(timer);
					}
				},
				finished,
				release,
				async respond(route, answer) {
					start();
					await wait;
					try {
						await route.fulfill(answer);
					} finally {
						pending.delete(release);
						finish();
					}
				}
			};
		},
		releaseAll() {
			for (const release of pending) release();
			pending.clear();
		}
	};
}
