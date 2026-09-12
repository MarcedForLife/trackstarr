<script lang="ts">
	import { afterNavigate, beforeNavigate, goto, preloadCode } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { dropPendingCovers } from '$lib/covers';
	import { behind, keyboard } from '$lib/modal';
	import { PRIMARY, routeOf, SETTINGS } from '$lib/nav';
	import { setStage } from '$lib/scroller';
	import { notice } from '$demo';
	import { overlay } from '$lib/overlay';
	import { running } from '$lib/paused.svelte';
	import Mark from '$lib/components/Mark.svelte';
	import NavProgress from '$lib/components/NavProgress.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import type { Component } from 'svelte';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	// The demo's notice over every page, which only the demo build has a
	// component for.
	let DemoNotice = $state<Component | null>(null);
	notice?.().then((loaded) => (DemoNotice = loaded.default));

	// The gear carries the accent on a settings page: with the drawer shut it is
	// the only thing on a phone that can say so.
	const settingsHere = $derived(routeOf(page.url.pathname).startsWith('/settings'));

	// Every route the nav can reach, prefetched once the first page settles: the
	// route's code was 90ms of a 500ms navigation on a throttled phone. Code
	// only; the data must stay current.
	const ROUTES = [...PRIMARY.map((spot) => spot.href), ...SETTINGS];

	$effect(() => {
		// After the first page's own work.
		const idle =
			'requestIdleCallback' in window
				? requestIdleCallback(() => ROUTES.forEach((route) => preloadCode(route)))
				: setTimeout(() => ROUTES.forEach((route) => preloadCode(route)), 1200);
		return () => {
			if ('cancelIdleCallback' in window) cancelIdleCallback(idle as number);
			else clearTimeout(idle as ReturnType<typeof setTimeout>);
		};
	});

	// The page's frame, and from lg up what scrolls it; see $lib/scroller.
	let stage: HTMLElement | null = $state(null);
	$effect(() => {
		setStage(stage);
		return () => setStage(null);
	});

	// Where each page was left, by URL. From lg up SvelteKit's own restore puts
	// back the window's scroll, which is not the page's, so back and forward
	// are put back here. A shallow entry spent, as the selection bar's is,
	// changes no page and is left alone. Nothing renders from it, so not
	// SvelteMap.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	const left = new Map<string, number>();

	// How long a page gets to grow to a saved position before it is left where
	// it is.
	const FILLING_MS = 1000;

	// The page is still filling as it mounts, so a deep position is clamped
	// short. Asked for again each frame until it is reached or the time is up;
	// a reader who moves first is left alone.
	function putBack(at: number) {
		if (!stage) return;
		stage.scrollTop = at;
		// Where it landed, which the reader has not moved from while it holds.
		let held = stage.scrollTop;
		if (held >= at) return;
		const began = performance.now();
		const step = () => {
			if (!stage || stage.scrollTop !== held) return;
			stage.scrollTop = at;
			held = stage.scrollTop;
			if (held < at && performance.now() - began < FILLING_MS) requestAnimationFrame(step);
		};
		requestAnimationFrame(step);
	}

	afterNavigate((navigation) => {
		if (!stage) return;
		// On entry `from` has a null url, whatever its type says.
		const from = navigation.from?.url?.href;
		const to = navigation.to?.url?.href ?? '';
		if (from !== to) putBack(navigation.type === 'popstate' ? (left.get(to) ?? 0) : 0);

		// A new page fades in rather than cutting. Retriggered by hand rather than
		// {#key}, which would rebuild the component for an animation.
		stage.classList.remove('entering');
		// Reading a layout property flushes the removal, so re-adding restarts the
		// animation.
		void stage.offsetWidth;
		stage.classList.add('entering');
	});

	// The nav is a drawer below lg and a rail above, so `open` only matters on a
	// phone, where back should close the menu rather than the page. Raising it
	// takes a history entry and every close spends it.
	let open = $state(false);

	// The scrim that dismisses the drawer, which stays reachable while the page
	// does not.
	let scrim: HTMLElement | null = $state(null);

	// Where a drawer link is headed while its entry is being spent.
	let heading: URL | null = null;

	// The drawer's entry and Escape. Closing also carries out a departure the
	// guard held back.
	const drawer = overlay({
		name: 'menu',
		close: () => {
			open = false;
			const to = heading;
			heading = null;
			// Already resolved: SvelteKit worked out this URL for the link the guard
			// turned down.
			// eslint-disable-next-line svelte/no-navigation-without-resolve
			if (to) goto(to);
		}
	});

	function raise() {
		open = true;
		drawer.raise();
	}

	function close() {
		drawer.lower();
	}

	beforeNavigate((navigation) => {
		if (stage && navigation.from?.url) left.set(navigation.from.url.href, stage.scrollTop);

		// Here because the overview draws posters too, and the page is still
		// whole, which is the only moment the browser listens. Only on a real
		// route change, or a same-page navigation would blank arriving posters.
		if (navigation.to?.route.id !== navigation.from?.route.id) dropPendingCovers();

		// A drawer link is a departure with the menu up on its own entry, which
		// would sit under the destination as a second copy of the page. So the
		// entry is spent first and the navigation made from the pop. Not
		// `data-sveltekit-replacestate`: a shallow entry carries the page's
		// navigation index, and SvelteKit would read the next back as a shallow
		// pop that re-renders nothing.
		if (!open || !navigation.to?.url) return;
		if (navigation.type !== 'link' && navigation.type !== 'goto') return;
		heading = navigation.to.url;
		navigation.cancel();
		drawer.lower();
	});

	// The page goes inert under the open drawer, and comes back on a resize past
	// lg, where the aside is the rail. The aside by id, which the gear's
	// aria-controls already has. See $lib/modal.
	$effect(() => {
		if (!open) return;
		const drawer = document.getElementById('app-sidebar');
		if (!drawer || !scrim) return;
		const wide = window.matchMedia('(min-width: 1024px)');
		const onWide = (event: MediaQueryListEvent) => event.matches && close();
		wide.addEventListener('change', onWide);
		// The keyboard first: it notes where it came from before that goes inert.
		const undo = [
			keyboard(() => document.querySelector<HTMLElement>('[data-drawer-close]')),
			behind(drawer, scrim)
		];
		return () => {
			wide.removeEventListener('change', onWide);
			undo.forEach((restore) => restore());
		};
	});
</script>

<NavProgress />

<!-- From lg up the frame is the viewport and main scrolls inside it, so the
     rail and the notice hold still while the page overscrolls. Below, the
     document scrolls as ever. -->
<div class="flex min-h-dvh lg:h-dvh lg:overflow-hidden">
	<Sidebar user={data.user} {open} onclose={close} />

	<!-- Tapping off the drawer closes it. A button, so a keyboard reaches it. -->
	<button
		bind:this={scrim}
		type="button"
		tabindex={open ? 0 : -1}
		aria-label="Close the menu"
		onclick={close}
		class={`fixed inset-0 z-40 bg-black/50 transition-opacity duration-300 lg:hidden ${
			open ? 'opacity-100' : 'pointer-events-none opacity-0'
		}`}
	></button>

	<div class="flex min-w-0 flex-1 flex-col lg:min-h-0">
		{#if DemoNotice}
			<DemoNotice />
		{/if}
		<!-- touch-none: a drag on the bar is not a scroll, so it cannot pull the
		     page to a refresh. Taps still land. -->
		<header
			class="sticky top-0 z-30 touch-none border-b border-line bg-surface/85 pt-[env(safe-area-inset-top)] backdrop-blur-md lg:hidden"
		>
			<div class="flex h-14 items-center gap-2 px-4">
				<!-- Keep the home link separate from the current page heading. -->
				<a
					href={resolve('/')}
					aria-label="Overview"
					class="mark-press -ml-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-md"
				>
					<Mark
						size={19}
						class="flex-none text-accent-fill"
						motion={running.current ? 'travel' : 'none'}
						press
					/>
				</a>
				<h1 class="min-w-0 truncate text-[16px] font-semibold tracking-tight">{data.pageTitle}</h1>
				<!-- A gear, not a hamburger: everything behind it is settings and the
				     account. On the right, where the drawer comes in from. -->
				<button
					type="button"
					onclick={raise}
					aria-label="Settings and account"
					aria-expanded={open}
					aria-controls="app-sidebar"
					class={`-mr-2 ml-auto rounded-md p-2.5 hover:bg-raised hover:text-fg active:bg-raised ${
						settingsHere ? 'text-accent' : 'text-dim'
					}`}
				>
					<svg
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.8"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<circle cx="12" cy="12" r="3.2"></circle>
						<path
							d="M19.4 15a1.6 1.6 0 0 0 .32 1.77l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.6 1.6 0 0 0-1.77-.32 1.6 1.6 0 0 0-1 1.47V21a2 2 0 1 1-4 0v-.11a1.6 1.6 0 0 0-1.05-1.47 1.6 1.6 0 0 0-1.77.32l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.6 1.6 0 0 0 .32-1.77 1.6 1.6 0 0 0-1.47-1H3a2 2 0 1 1 0-4h.11a1.6 1.6 0 0 0 1.47-1.05 1.6 1.6 0 0 0-.32-1.77l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.6 1.6 0 0 0 1.77.32H9a1.6 1.6 0 0 0 1-1.47V3a2 2 0 1 1 4 0v.11a1.6 1.6 0 0 0 1 1.47 1.6 1.6 0 0 0 1.77-.32l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.6 1.6 0 0 0-.32 1.77V9a1.6 1.6 0 0 0 1.47 1H21a2 2 0 1 1 0 4h-.11a1.6 1.6 0 0 0-1.47 1z"
						></path>
					</svg>
				</button>
			</div>
		</header>

		<main
			bind:this={stage}
			class="min-w-0 flex-1 lg:min-h-0 lg:overflow-y-auto lg:overscroll-y-contain"
		>
			{@render children()}
		</main>
	</div>
</div>

<TabBar />

<style>
	/* Short and small: the reader has already waited, and a big slide on
	   hundreds of posters is a lot of compositing. layout.css's reduced-motion
	   rule cuts it to nothing. */
	/* :global because afterNavigate adds the class, so Svelte would drop the
	   rule as unused. */
	main:global(.entering) {
		animation: enter 160ms cubic-bezier(0.2, 0, 0, 1);
	}

	@keyframes enter {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
</style>
