<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError, login } from '$lib/api';
	import Mark from '$lib/components/Mark.svelte';

	let username = $state('');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		try {
			await login(username, password);
			// The layout guard re-reads the session and routes a forced
			// password change to its own screen.
			await goto(resolve('/'), { invalidateAll: true });
		} catch (err) {
			if (err instanceof ApiError) {
				error =
					err.status === 429
						? 'Too many attempts. Wait a minute and try again.'
						: 'Wrong username or password.';
			} else {
				error = 'Could not reach the service.';
			}
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Sign in · Trackstarr</title></svelte:head>

<main
	class="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 px-6 pt-[calc(1.5rem+env(safe-area-inset-top))] pb-[calc(1.5rem+env(safe-area-inset-bottom))]"
>
	<div>
		<div class="flex items-center gap-2.5">
			<Mark size={20} class="text-accent" />
			<h1 class="text-2xl font-semibold tracking-tight">Trackstarr</h1>
		</div>
		<p class="mt-1 text-sm text-dim">Sign in</p>
	</div>

	<form class="flex flex-col gap-4" onsubmit={submit}>
		<label class="flex flex-col gap-1 text-sm">
			Username
			<input
				bind:value={username}
				autocomplete="username"
				required
				class="rounded-lg border border-line-strong bg-field px-3 py-2.5"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			Password
			<input
				type="password"
				bind:value={password}
				autocomplete="current-password"
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
			Sign in
		</button>
	</form>
</main>
