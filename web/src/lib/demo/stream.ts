// The demo's stand-in for the service's event stream. $lib/stream opens one of
// these instead of an EventSource, and the world publishes into it as the
// simulation moves, so the pages refetch exactly as they would against a
// service.

/** What $lib/stream needs of a stream: EventSource's shape, without the rest. */
export type Source = {
	readonly readyState: number;
	onopen: ((event: Event) => void) | null;
	onmessage: ((event: MessageEvent) => void) | null;
	onerror: ((event: Event) => void) | null;
	close(): void;
};

// EventSource.OPEN and CLOSED, spelled out: the class is a browser global and
// the tests run without one.
const OPEN = 1;
const CLOSED = 2;

class DemoSource implements Source {
	readyState = OPEN;
	onopen: ((event: Event) => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;

	constructor() {
		// After the caller has set its handlers, as a real connection opens.
		setTimeout(() => this.onopen?.(new Event('open')), 0);
	}

	close(): void {
		this.readyState = CLOSED;
		if (current === this) current = null;
	}
}

// The one stream a tab holds; $lib/stream never opens two.
let current: DemoSource | null = null;

export function demoSource(): Source {
	current = new DemoSource();
	return current;
}

/** Tell the pages something changed, as the service would. */
export function publish(kind: string): void {
	current?.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ kind }) }));
}
