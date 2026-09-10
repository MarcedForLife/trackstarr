<script lang="ts">
	import PasswordForm from '$lib/components/PasswordForm.svelte';
</script>

<svelte:head><title>Change password · Trackstarr</title></svelte:head>

<main
	class="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 px-6 pt-[calc(1.5rem+env(safe-area-inset-top))] pb-[calc(1.5rem+env(safe-area-inset-bottom))]"
>
	<div>
		<h1 class="text-2xl font-semibold">Change password</h1>
		<p class="text-sm text-dim">
			The generated password sits in the service log, so replace it before anything else.
		</p>
	</div>
	<PasswordForm />
</main>
