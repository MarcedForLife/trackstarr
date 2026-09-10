<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError, changePassword } from '$lib/api';

	let current = $state('');
	let replacement = $state('');
	let confirmation = $state('');
	let error = $state('');
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (replacement !== confirmation) {
			error = 'The new passwords do not match.';
			return;
		}
		busy = true;
		error = '';
		try {
			await changePassword(current, replacement);
			await goto(resolve('/'), { invalidateAll: true });
		} catch (err) {
			if (err instanceof ApiError) {
				error =
					err.status === 403
						? 'The current password is wrong.'
						: 'The new password needs at least 8 characters.';
			} else {
				error = 'Could not reach the service.';
			}
		} finally {
			busy = false;
		}
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit}>
	<label class="flex flex-col gap-1 text-sm">
		Current password
		<input
			type="password"
			bind:value={current}
			autocomplete="current-password"
			required
			class="rounded-lg border border-line-strong bg-field px-3 py-2.5"
		/>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		New password
		<input
			type="password"
			bind:value={replacement}
			autocomplete="new-password"
			required
			minlength="8"
			class="rounded-lg border border-line-strong bg-field px-3 py-2.5"
		/>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		New password again
		<input
			type="password"
			bind:value={confirmation}
			autocomplete="new-password"
			required
			class="rounded-lg border border-line-strong bg-field px-3 py-2.5"
		/>
	</label>

	{#if error}
		<p class="text-sm text-danger">{error}</p>
	{/if}

	<button
		disabled={busy}
		class="rounded-lg bg-accent px-3 py-2.5 font-medium text-on-accent disabled:opacity-50"
	>
		Change password
	</button>
</form>
