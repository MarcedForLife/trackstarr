// A page's way of holding the tab bar in place. The library raises its own bar
// over it, and two bottom bars sliding independently is noise; it also keeps
// --tabbar honest for the three things offset by it.

let held = $state(false);

export const chrome = {
	get held() {
		return held;
	}
};

// Stated, not refcounted: the library's effect re-runs on every poll, and a
// teardown between runs released the hold for good.
export function setHold(on: boolean) {
	held = on;
}
