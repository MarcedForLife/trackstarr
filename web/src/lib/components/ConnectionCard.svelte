<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import PathMapList from '$lib/components/PathMapList.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import {
		fallbackName,
		nameProblem as validateName,
		testConnection,
		type ConnectionResult,
		type Service,
		type ServiceName
	} from '$lib/connections';
	import {
		button,
		dotted,
		field,
		fileRow,
		markDanger,
		markWorded,
		removeButton,
		rowMenu
	} from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';

	// One service: shut, a line saying whether it answers; open, its fields. Owns
	// its own check, so a slow Plex never holds up the Radarr card, and asks
	// again when a save touches its fields.
	let {
		service,
		settings,
		readOnly,
		cleared,
		onclear,
		opened = false,
		onname,
		onremove
	}: {
		service: Service;
		settings: SettingsDraft;
		readOnly: boolean;
		// Whether the reader emptied the stored credential. The page's, since an
		// empty field means "leave it alone" and a clear is the draft sending null.
		cleared: boolean;
		onclear: (yes: boolean) => void;
		// Open on arrival, with the first field focused: the card just added.
		opened?: boolean;
		// The editable display name; identity stays fixed.
		onname?: (text: string) => void;
		// Absent for a card the environment pins, which has no Remove.
		onremove?: () => void;
	} = $props();

	const draft = $derived(settings.draft);
	const baseline = $derived(settings.baseline);

	// Shut on arrival, since the shut row says which services work. The card
	// just added opens: it has nothing to say yet.
	// svelte-ignore state_referenced_locally
	let open = $state(opened);
	let result = $state<ConnectionResult | null>(null);
	let checking = $state(false);

	// A credential is on file and the reader has not emptied it.
	const kept = $derived(!!baseline[service.key]?.set && !cleared);
	// A cleared key counts as gone at once, or Test would answer for it.
	const configured = $derived(!!(draft[service.url] as string) && (!!draft[service.key] || kept));

	// Edits to a shut card still count towards the save bar, so the row says so.
	// The optional names fall back to '', which no setting is called.
	const edited = $derived(
		settings.anyChanged([
			service.url,
			service.key,
			service.map ?? '',
			service.publicUrl ?? '',
			service.nameField ?? ''
		])
	);

	const nameText = $derived(String(draft[service.nameField ?? ''] ?? ''));
	const nameProblem = $derived(validateName(nameText));
	const nameDesc = $derived(`Optional. Shown instead of ${fallbackName(service)}.`);

	// A card just added starts in its address, the one field it cannot do without.
	let addressInput = $state<HTMLInputElement>();
	let pathMap = $state<PathMapList>();
	onMount(async () => {
		if (!opened) return;
		await tick();
		addressInput?.focus();
	});

	// A result belongs to the exact draft and saved callback it tested. Editing
	// invalidates it without issuing a request on every keystroke.
	const input = $derived(
		JSON.stringify([
			service.name,
			draft[service.url],
			draft[service.key],
			service.map ? draft[service.map] : null,
			service.map ? baseline[service.map]?.value : null,
			kept,
			service.group === 'source' ? draft.WEBHOOK_URL : null,
			service.group === 'source' ? baseline.WEBHOOK_URL?.value : null
		])
	);
	let testedInput = '';
	let testTicket = 0;
	function invalidate() {
		if (testedInput === input) return;
		testedInput = input;
		testTicket++;
		result = null;
		checking = false;
	}
	$effect(() => {
		void input;
		untrack(invalidate);
	});

	// An answer arriving after the card has gone is dropped.
	let gone = false;

	/** Ask the service whether it is there, if there is enough to ask with.
	 * `minimumMs` holds the busy cue open that long, so a fast answer still
	 * reads as work. The floor runs alongside the request, not after it. */
	export async function test(minimumMs = 0) {
		invalidate();
		if (!configured) return;
		const ticket = ++testTicket;
		const asked = input;
		const current = () => !gone && ticket === testTicket && asked === input;
		const feedback = minimumMs
			? new Promise<void>((resolve) => setTimeout(resolve, minimumMs))
			: undefined;
		checking = true;
		try {
			const answer = await testConnection(
				service.name,
				(draft[service.url] as string) ?? '',
				// Only what was typed: an untouched field sent blank reads as cleared.
				(draft[service.key] as string) ?? ''
			);
			if (current()) result = answer;
		} catch {
			if (current()) {
				result = {
					ok: false,
					detail: 'Could not reach the service.',
					hint: '',
					webhook: '',
					webhook_detail: ''
				};
			}
		} finally {
			await feedback;
			if (current()) checking = false;
		}
	}

	// Once on arrival, for an admin; a viewer gets the stored state. From an
	// effect for the teardown, tracking nothing, or every keystroke would ask.
	$effect(() => {
		untrack(() => {
			if (!readOnly) test();
		});
		return () => {
			gone = true;
		};
	});

	// A save has landed, so the saved values matter now, and a touched *arr has a
	// fresh webhook connection.
	// svelte-ignore state_referenced_locally
	onDestroy(
		settings.onreset((saved) => {
			if (
				saved &&
				(service.url in saved ||
					service.key in saved ||
					(service.map !== undefined && service.map in saved) ||
					(service.group === 'source' && 'WEBHOOK_URL' in saved))
			) {
				// Even unchanged/redacted values can now refer to a new saved key.
				testTicket++;
				result = null;
				test();
			}
		})
	);

	const mappingEdited = $derived(!!service.map && settings.changed(service.map));

	const callbackEdited = $derived(service.group === 'source' && settings.changed('WEBHOOK_URL'));

	// Held through a re-test, so the glow does not blink off while checking.
	const answered = $derived.by(() => {
		if (!configured) return { label: 'Not set', class: 'text-faint', glow: '' };
		if (!result) return { label: 'Configured', class: 'text-dim', glow: '' };
		const danger = { class: 'text-danger', glow: 'var(--danger)' };
		const partly = { label: 'API connected', class: 'text-accent', glow: 'var(--accent)' };
		if (!result.ok) return { label: 'No answer', ...danger };
		// A green Connected on an *arr that cannot call back is the lie this
		// check exists to catch.
		if (callbackEdited) return partly;
		if (result.webhook === 'unreachable') return { label: 'No webhook', ...danger };
		if (service.group === 'source' && result.webhook !== 'connected') return partly;
		return { label: 'Connected', class: 'text-ok', glow: 'var(--ok)' };
	});
	const pill = $derived(checking ? { label: 'Checking', class: 'text-faint' } : answered);

	// What an *arr holding the wrong connection, or none, means for the reader.
	const WEBHOOK_STATE: Record<string, string> = {
		connected: 'Webhook connected',
		unreachable: 'Its webhook call never arrives',
		missing: 'No webhook connection yet',
		stale: 'Its webhook points somewhere else',
		unknown: 'Could not read its connections list'
	};

	// Unreachable is a fault, not a step before a save.
	function webhookTone(state: string): string {
		if (state === 'connected') return 'text-ok';
		return state === 'unreachable' ? 'text-danger' : 'text-dim';
	}

	function webhookNote(answer: ConnectionResult): string {
		if (callbackEdited) return 'Save the webhook address to test the new callback.';
		const state = WEBHOOK_STATE[answer.webhook] ?? '';
		if (!state || answer.webhook === 'connected' || answer.webhook === 'unknown') return state;
		// Saving would only rewrite what it already holds. The *arr's own words
		// below say what to change instead.
		if (answer.webhook === 'unreachable') return state;
		return `${state}. Trackstarr registers it on save.`;
	}

	const address = $derived(((draft[service.url] as string) ?? '').trim());
	const kind = $derived(service.type ?? (service.name as ServiceName));

	// The second line's words after the address.
	const told = $derived.by(() => {
		if (!result || checking) return [];
		const words: { text: string; tone: string }[] = [];
		if (!result.ok) words.push({ text: result.detail, tone: 'text-danger' });
		else if (result.webhook && result.webhook !== 'connected')
			words.push({ text: WEBHOOK_STATE[result.webhook] ?? '', tone: '' });
		else words.push({ text: result.detail, tone: '' });
		if (result.paths === 'attention')
			words.push({ text: 'Paths need attention', tone: 'text-accent' });
		if (result.paths === 'unknown') words.push({ text: 'Paths not checked', tone: 'text-accent' });
		return words.filter((word) => word.text);
	});

	// A slot for the clear button on every row, so the boxes end in line.
	const entryRow = 'flex w-full items-center gap-2';
	const gutter = 'flex w-7 flex-none items-center justify-end';
</script>

<div class={`${fileRow()} relative py-3`} data-tint style:--tint={answered.glow || undefined}>
	<Disclosure
		id={`${service.name}-fields`}
		{open}
		ontoggle={() => (open = !open)}
		class="group flex w-full items-start text-left after:absolute after:inset-0 after:content-['']"
		align="start"
		panelClass="mt-3"
		beside={logo}
	>
		{#snippet summary()}
			<span class="block min-w-0 flex-1">
				<span class="flex items-baseline gap-1.5 text-[14px] leading-snug font-medium">
					<span class="truncate">{service.label}</span>
					{#if address}
						<span class={`flex-none text-[12.5px] ${pill.class}`}>{pill.label}</span>
					{/if}
					{#if edited}
						<span class="h-1.5 w-1.5 flex-none self-center rounded-full bg-accent-fill"></span>
						<span class="sr-only">Unsaved changes</span>
					{/if}
				</span>
				<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}>
					<!-- The address truncates only once it fills the line alone. -->
					{#if address}
						<span class="max-w-full flex-none truncate">{address}</span>
					{:else}
						<span class={pill.class}>{pill.label}</span>
					{/if}
					{#each told as word, at (at)}
						<span class={`min-w-0 truncate ${word.tone}`}>{word.text}</span>
					{/each}
				</span>
			</span>
		{/snippet}

		{#snippet after()}
			<button
				type="button"
				onclick={() => !checking && test(600)}
				disabled={readOnly || !configured}
				aria-label={checking ? `Checking ${service.label}` : `Test ${service.label}`}
				aria-busy={checking}
				title="Test"
				class={rowMenu}
			>
				<span class="flex" class:turning={checking}><Glyph name="refresh" size={14} /></span>
			</button>
		{/snippet}

		{#snippet panel()}
			<!-- The page's control column less the tray, row and box insets. -->
			<div
				class="rounded-lg border border-line bg-sunken px-3 [--control-column:calc(21rem-49px)] sm:px-4 [&>*:first-child]:border-t-0"
			>
				{#if result}
					<!-- Dimmed while a fresh answer is on its way, so a repeat press
					     landing on the same words still reads as having run. -->
					<div
						class={`border-t border-line py-3 text-[12.5px] transition-opacity duration-150 ${result.ok ? 'text-dim' : 'text-danger'} ${checking ? 'opacity-(--disabled)' : ''}`}
					>
						<p>{result.detail}</p>
						{#if result.ok && webhookNote(result)}
							<p class={`mt-1 ${webhookTone(result.webhook)}`}>{webhookNote(result)}</p>
						{/if}
						<!-- Unattributed: it follows the line it explains, and the *arr
						     does not always give words to quote. -->
						{#if result.webhook_detail}
							<p class="mt-1 text-faint">{result.webhook_detail}</p>
						{/if}
						{#if mappingEdited}
							<p class="mt-1 text-faint">
								Path checks use the saved mapping. Save your changes to test the new mapping.
							</p>
						{/if}
						{#if result.hint}
							<p class="mt-1 break-words whitespace-pre-line text-faint">{result.hint}</p>
						{/if}
					</div>
				{/if}

				<SettingRow
					name={service.url}
					label="Address"
					desc={`Where ${service.label} answers, as this container reaches it.`}
					stack
				>
					<!-- Four cards have an Address row, so the field's name says whose. -->
					{#snippet children({ describedBy })}
						<div class={entryRow}>
							<input
								bind:this={addressInput}
								value={(draft[service.url] as string) ?? ''}
								oninput={(event) => (draft[service.url] = event.currentTarget.value)}
								placeholder={service.placeholder}
								inputmode="url"
								autocapitalize="none"
								autocorrect="off"
								spellcheck="false"
								aria-label={`${service.label} address`}
								aria-describedby={describedBy}
								disabled={settings.envLocked(service.url)}
								class={field}
							/>
							<div class={gutter}></div>
						</div>
					{/snippet}
				</SettingRow>

				<SettingRow
					name={service.key}
					label={service.keyLabel}
					desc={kept
						? 'One is stored. Type a new one to replace it, or clear it to switch the service off.'
						: `Found in ${service.label}'s own settings.`}
					stack
				>
					{#snippet children({ describedBy })}
						<div class={entryRow}>
							<input
								value={(draft[service.key] as string) ?? ''}
								oninput={(event) => {
									draft[service.key] = event.currentTarget.value;
									onclear(false);
								}}
								type="password"
								placeholder={kept ? '••••••••' : 'Not set'}
								autocapitalize="none"
								autocorrect="off"
								spellcheck="false"
								autocomplete="off"
								aria-label={`${service.label} ${service.keyLabel.toLowerCase()}`}
								aria-describedby={describedBy}
								disabled={settings.envLocked(service.key)}
								class={field}
							/>
							<div class={gutter}>
								{#if kept}
									<button
										aria-label={`Clear the ${service.label} ${service.keyLabel.toLowerCase()}`}
										disabled={settings.envLocked(service.key)}
										onclick={() => {
											onclear(true);
											draft[service.key] = '';
										}}
										class={removeButton}
									>
										<Glyph name="cross" />
									</button>
								{/if}
							</div>
						</div>
					{/snippet}
				</SettingRow>

				{#if service.nameField}
					{@const name = service.nameField}
					<SettingRow {name} label="Name" desc={nameDesc} stack>
						{#snippet children({ describedBy })}
							<div class={entryRow}>
								<input
									value={nameText}
									oninput={(event) => onname?.(event.currentTarget.value)}
									placeholder={fallbackName(service)}
									maxlength="40"
									autocapitalize="words"
									autocorrect="off"
									spellcheck="false"
									aria-label={`${service.label} name`}
									aria-describedby={describedBy}
									aria-invalid={!!nameProblem}
									disabled={settings.envLocked(name)}
									class={field}
								/>
								<div class={gutter}></div>
							</div>
							{#if nameProblem}<p role="alert" class="mt-1.5 text-[12.5px] text-danger">
									{nameProblem}
								</p>{/if}
						{/snippet}
					</SettingRow>
				{/if}

				{#if service.publicUrl}
					{@const name = service.publicUrl}
					<SettingRow
						{name}
						label="Public address"
						desc={`Optional address to use for ${service.label} links, instead of the service address.`}
						stack
					>
						{#snippet children({ describedBy })}
							<div class={entryRow}>
								<input
									value={(draft[name] as string) ?? ''}
									oninput={(event) => (draft[name] = event.currentTarget.value)}
									placeholder={(draft[service.url] as string) || service.placeholder}
									inputmode="url"
									autocapitalize="none"
									autocorrect="off"
									spellcheck="false"
									aria-label={`${service.label} public address`}
									aria-describedby={describedBy}
									disabled={settings.envLocked(name)}
									class={field}
								/>
								<div class={gutter}></div>
							</div>
						{/snippet}
					</SettingRow>
				{/if}

				{#if service.map}
					{@const name = service.map}
					<SettingRow
						{name}
						label="Path map"
						desc={`Only needed when ${service.label} sees the library at a different path. Ours first.`}
					>
						<button
							type="button"
							onclick={() => pathMap?.add()}
							disabled={settings.envLocked(name)}
							aria-label={`Add a ${service.label} path pair`}
							class={button}
						>
							<Glyph name="plus" /> Add
						</button>
						{#snippet below({ labelledBy, describedBy })}
							<PathMapList
								bind:this={pathMap}
								{settings}
								{name}
								label={service.label}
								{labelledBy}
								{describedBy}
								{gutter}
							/>
						{/snippet}
					</SettingRow>
				{/if}

				{#if onremove}
					<div class="-mr-3 flex justify-end border-t border-line py-2 sm:-mr-4">
						<button
							type="button"
							class={`${markWorded} ${markDanger}`}
							disabled={readOnly}
							onclick={onremove}
							aria-label={`Remove ${service.label}`}>Remove</button
						>
					</div>
				{/if}
			</div>
		{/snippet}
	</Disclosure>
</div>

{#snippet logo()}
	<ServiceIcon name={kind} box={40} size={24} />
{/snippet}
