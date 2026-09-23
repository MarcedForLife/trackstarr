<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { logout, request, type Account } from '$lib/api';
	import Glyph from '$lib/components/Glyph.svelte';
	import Mark from '$lib/components/Mark.svelte';
	import NavIcon from '$lib/components/NavIcon.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import { LOCAL_SETTINGS, PAGE_TITLES, PRIMARY, routeOf, SERVICE_SETTINGS } from '$lib/nav';
	import { pause, running, watchPause } from '$lib/paused.svelte';
	import { keepFlag, storedFlag } from '$lib/prefs';
	import type { Pathname } from '$app/types';

	// Below lg the aside is an off-canvas drawer the layout opens; from lg up it
	// is the permanent rail and `open` means nothing.
	let {
		user,
		open = false,
		onclose
	}: { user: Account | null; open?: boolean; onclose?: () => void } = $props();

	const path = $derived(routeOf(page.url.pathname));

	const COLLAPSED_KEY = 'sidebar-collapsed';

	// Collapsing is a desktop affordance: the drawer is always full width.
	let collapsed = $state(storedFlag(COLLAPSED_KEY));

	function toggleCollapsed() {
		collapsed = !collapsed;
		keepFlag(COLLAPSED_KEY, collapsed);
	}

	let version = $state('');
	request<{ version: string }>('/api/status')
		.then((status) => (version = status.version))
		.catch(() => {
			/* the sidebar works without it */
		});

	// Shared with the phone's tab bar, which shows the same warning: one
	// interval between them however many are mounted. See $lib/paused.
	$effect(watchPause);

	const navClass = (active: boolean) =>
		`flex items-center gap-2.5 overflow-hidden rounded-lg px-3 py-2.5 text-sm font-medium whitespace-nowrap lg:py-1.5 ${
			active ? 'bg-accent-soft text-fg' : 'text-dim hover:bg-raised hover:text-fg'
		}`;
	const iconClass = (active: boolean) => `flex-none ${active ? 'text-accent' : ''}`;
	// Only the desktop rail hides its labels; the drawer always shows them.
	const labelClass = $derived(`transition-opacity duration-200 ${collapsed ? 'lg:opacity-0' : ''}`);
	const hideOnRail = $derived(collapsed ? 'lg:hidden' : '');
	const iconButton =
		'rounded-md p-2.5 text-faint hover:bg-raised hover:text-fg active:bg-raised lg:p-1.5';

	// The layout spends the drawer's entry, as for any link here; doing it here
	// with history.back() raced the navigation.
	async function signOut() {
		await logout();
		await goto(resolve('/login'), { invalidateAll: true });
	}
</script>

{#snippet heading(name: string)}
	{#if collapsed}
		<div class="mx-2 my-5 hidden border-t border-line lg:block"></div>
	{/if}
	<div
		class={`px-3 pt-5 pb-1.5 text-[11px] font-semibold tracking-wider text-faint uppercase ${hideOnRail}`}
	>
		{name}
	</div>
{/snippet}

<!-- The break between what the service does and what this browser and login
     do. A rule, not a second heading. -->
{#snippet divider()}
	<div class="mx-2 my-2.5 border-t border-line"></div>
{/snippet}

<!-- Inline rather than in NavIcon: nothing else draws these, so there is nothing
     for them to drift from. -->
{#snippet settingsIcon(href: Pathname, active: boolean)}
	<svg
		width="16"
		height="16"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="1.8"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={iconClass(active)}
	>
		{#if href === '/settings'}
			<line x1="4" y1="7" x2="20" y2="7"></line>
			<circle cx="15" cy="7" r="2.4"></circle>
			<line x1="4" y1="17" x2="20" y2="17"></line>
			<circle cx="8" cy="17" r="2.4"></circle>
		{:else if href === '/settings/rules'}
			<!-- A funnel: some tracks are kept, the rest let through. -->
			<path d="M3 5h18l-7 8v6l-4 2v-8z"></path>
		{:else if href === '/settings/sweep'}
			<circle cx="12" cy="12" r="9"></circle>
			<path d="M12 7v5.2l3.4 2"></path>
		{:else if href === '/settings/connections'}
			<path d="M9.5 14.5 5.8 18.2a3.1 3.1 0 0 1-4.4-4.4L5.1 10a3.1 3.1 0 0 1 4.4 0"></path>
			<path d="M14.5 9.5l3.7-3.7a3.1 3.1 0 0 1 4.4 4.4L18.9 14a3.1 3.1 0 0 1-4.4 0"></path>
		{:else if href === '/settings/appearance'}
			<circle cx="12" cy="12" r="9"></circle>
			<path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"></path>
		{:else}
			<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
			<circle cx="12" cy="7" r="4"></circle>
		{/if}
	</svg>
{/snippet}

<!-- The label comes from $lib/nav, which the page heading and browser title
     already read, so the three cannot disagree. -->
{#snippet settingsLink(href: Pathname)}
	{@const here = path === href}
	{@const label = PAGE_TITLES[href] ?? ''}
	<a
		href={resolve(href)}
		class={navClass(here)}
		title={collapsed ? label : undefined}
		aria-current={here ? 'page' : undefined}
	>
		{@render settingsIcon(href, here)}
		<span class={labelClass}>{label}</span>
	</a>
{/snippet}

<!-- Visibility hides the closed drawer from the tab order after its exit;
     the desktop rail stays visible regardless of the drawer state. -->
<aside
	id="app-sidebar"
	aria-label="Main"
	data-open={open}
	class={`mobile-drawer fixed inset-y-0 right-0 z-50 flex w-[17rem] flex-none flex-col overflow-x-hidden overflow-y-auto overscroll-contain border-l border-line bg-sunken p-3 pt-[calc(0.75rem+env(safe-area-inset-top))] pb-[calc(0.75rem+env(safe-area-inset-bottom))] duration-300 ease-out lg:visible lg:static lg:z-auto lg:h-dvh lg:translate-x-0 lg:border-r lg:border-l-0 lg:pt-3 lg:transition-[width] ${collapsed ? 'lg:w-16' : 'lg:w-60'}`}
>
	<div class={`flex items-center gap-2 pt-1 pb-3 ${collapsed ? 'px-3 lg:justify-center' : 'px-3'}`}>
		<!-- The mark and name link home. Hide the whole link on the collapsed
		     rail so the keyboard cannot land on an empty link. -->
		<a
			href={resolve('/')}
			class={`-mx-1 flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 ${hideOnRail}`}
		>
			<Mark
				size={22}
				class="flex-none text-accent-fill"
				motion={running.current ? 'process' : 'none'}
			/>
			<span class="truncate text-[17px] font-semibold tracking-tight whitespace-nowrap">
				Trackstarr
			</span>
		</a>
		<button
			onclick={toggleCollapsed}
			aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
			title={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
			class={`hidden lg:block ${iconButton}`}
		>
			<svg
				width="16"
				height="16"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
			>
				<rect x="3" y="4" width="18" height="16" rx="2"></rect>
				<line x1="9" y1="4" x2="9" y2="20"></line>
			</svg>
		</button>
		<button
			onclick={onclose}
			aria-label="Close the menu"
			class={`lg:hidden ${iconButton}`}
			data-drawer-close
		>
			<Glyph name="cross" size={18} />
		</button>
	</div>

	<nav class="flex flex-1 flex-col gap-0.5">
		<!-- Below lg the tab bar has these three. Kept in the DOM so the rail is
		     whole the moment a tablet crosses the breakpoint. -->
		<div class="contents max-lg:hidden">
			{#each PRIMARY as spot (spot.href)}
				{@const here = path === spot.href}
				<a
					href={resolve(spot.href)}
					class={navClass(here)}
					title={collapsed ? spot.label : undefined}
					aria-current={here ? 'page' : undefined}
				>
					<span class={iconClass(here)}><NavIcon icon={spot.icon} /></span>
					<span class={`flex-1 ${labelClass}`}>{spot.label}</span>
					{#if spot.href === '/' && pause.current}
						<!-- Shown on the collapsed rail too. -->
						<span
							title="Processing is paused"
							aria-label="Processing is paused"
							class="h-1.5 w-1.5 flex-none rounded-full bg-danger"
						></span>
					{/if}
				</a>
			{/each}
		</div>

		{@render heading('Settings')}
		{#each SERVICE_SETTINGS as href (href)}
			{@render settingsLink(href)}
		{/each}

		{@render divider()}
		{#each LOCAL_SETTINGS as href (href)}
			{@render settingsLink(href)}
		{/each}
	</nav>

	{#if version}
		<p class={`px-3 pb-1 font-mono text-[11px] whitespace-nowrap text-faint ${hideOnRail}`}>
			v{version}
		</p>
	{/if}
	<div
		class={`-mx-3 mt-2 flex items-center gap-2.5 border-t border-line px-4 pt-3 ${
			collapsed ? 'lg:flex-col lg:gap-1.5 lg:px-0' : ''
		}`}
	>
		<div
			class="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent"
			title={collapsed ? (user?.name ?? '') : undefined}
		>
			{user?.name?.[0]?.toUpperCase() ?? '?'}
		</div>
		<div class={`min-w-0 flex-1 leading-tight ${hideOnRail}`}>
			<p class="truncate text-[13px] font-medium">{user?.name}</p>
			<p class="text-[11px] text-faint">{user?.role}</p>
		</div>
		<ThemeToggle />
		<button onclick={signOut} aria-label="Sign out" title="Sign out" class={iconButton}>
			<svg
				width="16"
				height="16"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
				<polyline points="16 17 21 12 16 7"></polyline>
				<line x1="21" y1="12" x2="9" y2="12"></line>
			</svg>
		</button>
	</div>
</aside>
