// A node moved to the body, out of whatever holds it. An overlay inside a
// transformed or clipped box is placed against that box, not the window, and an
// inert one takes it with it.

export function portal(node: HTMLElement) {
	document.body.appendChild(node);
	return { destroy: () => node.remove() };
}
