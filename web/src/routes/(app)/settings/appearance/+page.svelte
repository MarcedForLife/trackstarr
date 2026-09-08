<script lang="ts">
	import Page from '$lib/components/Page.svelte';
	import PalettePicker from '$lib/components/PalettePicker.svelte';
	import PosterPreview from '$lib/components/PosterPreview.svelte';
	import Section from '$lib/components/Section.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import Select from '$lib/components/Select.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import Slider from '$lib/components/Slider.svelte';
	import VerdictChips from '$lib/components/VerdictChips.svelte';
	import {
		display,
		EFFECTS,
		setArt,
		setEffects,
		setFilters,
		setMissing,
		setSheen,
		setSpread,
		setUnsupported,
		type Art,
		type Effects,
		type Sheen,
		type Shown,
		type Spread
	} from '$lib/display.svelte';
	import { VERDICTS } from '$lib/library';
	import {
		order,
		setGridFlow,
		setGridOrder,
		setStripOrder,
		SORTS,
		type Flow,
		type Sort
	} from '$lib/order.svelte';
	import { PALETTES, setTheme, theme, type ThemePreference } from '$lib/theme.svelte';

	const options = [
		{ value: 'light', label: 'Light' },
		{ value: 'dark', label: 'Dark' },
		{ value: 'system', label: 'System' }
	];

	const spreadOptions = [
		{ value: 'narrow', label: 'Narrow' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'wide', label: 'Wide' }
	];

	const sheenOptions = [
		{ value: 'none', label: 'None' },
		{ value: 'gloss', label: 'Gloss' },
		{ value: 'foil', label: 'Foil' },
		{ value: 'holo', label: 'Holo' }
	];

	const artOptions = [
		{ value: 'show', label: 'Show' },
		{ value: 'hide', label: 'Hide' }
	];

	// Shared by the two rows that hide a verdict from the grid.
	const shownOptions = [
		{ value: 'show', label: 'Show' },
		{ value: 'hide', label: 'Hide' }
	];

	// The grid's own flip button's words, since one control covers seven orders.
	const flowOptions = [
		{ value: 'desc', label: 'Descending' },
		{ value: 'asc', label: 'Ascending' }
	];

	function choose(value: string) {
		setTheme(value as ThemePreference);
	}

	// Thirteen rows grouped by subject, each group folding to the values it
	// holds. No group is named for a row inside it.
	const label = (options: { value: string; label: string }[], value: string) =>
		options.find((option) => option.value === value)?.label ?? value;

	const themeNote = $derived(
		`${label(options, theme.preference)} · ${label(PALETTES, theme.palette)}`
	);
	// Cover art is named only when off.
	const posterNote = $derived(
		display.art === 'hide'
			? `${label(EFFECTS, display.effects)} · no cover art`
			: label(EFFECTS, display.effects)
	);
	// Every verdict selected is the same grid as none, which the row calls All.
	const libraryNote = $derived.by(() => {
		const kept = display.filters.length;
		const filter = kept && kept < VERDICTS.length ? `${kept} verdicts` : 'All';
		return `${filter} · ${label(SORTS, order.grid)}`;
	});
	const overviewNote = $derived(label(SORTS, order.strip));
</script>

<Page
	eyebrow="Settings"
	title="Appearance"
	lead="How the web UI looks and what it opens on, in this browser."
>
	<Section heading="Colours" note={themeNote} spaced open>
		<SettingRow label="Theme" desc="System follows the operating system." stack>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					{options}
					{labelledBy}
					{describedBy}
					value={theme.preference}
					onchange={choose}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Palette"
			desc="The colours everything is drawn in. Each has a light and a dark version, and Theme picks which."
			align="start"
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<PalettePicker {labelledBy} {describedBy} />
			{/snippet}
		</SettingRow>
	</Section>

	<Section heading="Posters" note={posterNote} spaced>
		<!-- Above the three rows that govern it, belonging to none alone. -->
		<PosterPreview />

		<!-- A slider: the one setting whose answers are the same thing more of. -->
		<SettingRow
			label="Poster effects"
			desc="How far a poster leans, lifts and catches the light under a finger. Off keeps the grid flat, which is steadier on an older phone. Balatro is the most of all three."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Slider
					options={EFFECTS}
					{labelledBy}
					{describedBy}
					value={display.effects}
					onchange={(value) => setEffects(value as Effects)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Tilt spread"
			desc="How far the lean spreads to neighbouring posters. Narrow turns the next ones a little. Wide moves the whole row."
			nested
			dim={display.effects === 'off'}
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={spreadOptions}
					{labelledBy}
					{describedBy}
					value={display.spread}
					disabled={display.effects === 'off'}
					onchange={(value) => setSpread(value as Spread)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Sheen"
			desc="The highlight a poster catches as it turns. Gloss is plain, Foil adds one band of colour and Holo several. Each is a blended layer the compositor must keep, so None is the steadiest."
			nested
			dim={display.effects === 'off'}
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={sheenOptions}
					{labelledBy}
					{describedBy}
					value={display.sheen}
					disabled={display.effects === 'off'}
					onchange={(value) => setSheen(value as Sheen)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Cover art"
			desc="Hidden draws each title as its initials, so the library loads without hundreds of images."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={artOptions}
					{labelledBy}
					{describedBy}
					value={display.art}
					onchange={(value) => setArt(value as Art)}
				/>
			{/snippet}
		</SettingRow>
	</Section>

	<Section heading="Library" note={libraryNote} spaced>
		<!-- No counts: there is no shelf on this page. -->
		<SettingRow
			label="Library filter"
			desc="The verdicts the grid opens filtered to. None selected means All. The row above the grid still changes it for a visit. This is the starting point."
			full
		>
			{#snippet children({ labelledBy, describedBy })}
				<VerdictChips
					{labelledBy}
					{describedBy}
					chosen={display.filters}
					allHint="Open on every title, whatever state it is in."
					onchange={setFilters}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Missing titles"
			desc="Whether the grid shows titles Radarr or Sonarr track with nothing downloaded. Hidden shows the library as it is on disk. The Missing filter still reaches them."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={shownOptions}
					{labelledBy}
					{describedBy}
					value={display.missing}
					onchange={(value) => setMissing(value as Shown)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Unsupported titles"
			desc="Whether the grid shows titles whose files are all in a container the rules never rewrite, such as AVI. Nothing here can change them. The Unsupported filter still reaches them."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={shownOptions}
					{labelledBy}
					{describedBy}
					value={display.unsupported}
					onchange={(value) => setUnsupported(value as Shown)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Library order"
			desc="The order the grid opens in. The menu above the grid still changes it for a visit. This is the starting point."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Select
					options={SORTS}
					{labelledBy}
					{describedBy}
					value={order.grid}
					onchange={(value) => setGridOrder(value as Sort)}
				/>
			{/snippet}
		</SettingRow>

		<SettingRow
			label="Direction"
			desc="Which way that order runs. Descending leads with the most of whatever it sorts on: the newest, the biggest, the worst. Changing Library order resets this to that order's default."
			nested
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Segmented
					fill
					options={flowOptions}
					{labelledBy}
					{describedBy}
					value={order.gridFlow}
					onchange={(value) => setGridFlow(value as Flow)}
				/>
			{/snippet}
		</SettingRow>
	</Section>

	<Section heading="Overview" note={overviewNote} spaced>
		<SettingRow
			label="Overview strip"
			desc="Which titles the overview's row of posters leads with. It holds a dozen, so this picks which."
			stack
		>
			{#snippet children({ labelledBy, describedBy })}
				<Select
					options={SORTS}
					{labelledBy}
					{describedBy}
					value={order.strip}
					onchange={(value) => setStripOrder(value as Sort)}
				/>
			{/snippet}
		</SettingRow>
	</Section>
</Page>
