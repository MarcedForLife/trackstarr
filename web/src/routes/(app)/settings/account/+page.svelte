<script lang="ts">
	import Page from '$lib/components/Page.svelte';
	import PasswordForm from '$lib/components/PasswordForm.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<Page eyebrow="Settings" lead={`Signed in as ${data.user?.name}.`}>
	<section class="mt-7 border-t border-line pt-4">
		<p class="text-[13px] font-semibold">Change password</p>
		<div class="mt-4 max-w-sm">
			<PasswordForm />
		</div>
	</section>
</Page>
