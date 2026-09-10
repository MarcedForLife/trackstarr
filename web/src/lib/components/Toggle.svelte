<script lang="ts">
	let {
		on,
		labelledBy,
		describedBy,
		disabled = false,
		onchange
	}: {
		on: boolean;
		// Every switch sits in a SettingRow, whose paragraphs name it.
		labelledBy: string;
		describedBy: string;
		disabled?: boolean;
		onchange: (on: boolean) => void;
	} = $props();
</script>

<!-- 36x22 with an 18px knob is iOS's ratio at our scale; every number is even
     for 1x. The padding, cancelled by the margin, makes the target thumb-sized. -->
<button
	role="switch"
	aria-checked={on}
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	{disabled}
	onclick={() => onchange(!on)}
	class="group -m-3 flex-none p-3 disabled:opacity-50 sm:-m-2 sm:p-2"
>
	<span
		class={`relative block h-[22px] w-9 rounded-full ring-0 ring-line-strong transition-[background-color,box-shadow] duration-200 group-active:ring-4 ${
			on ? 'bg-accent' : 'bg-toggle-off'
		}`}
	>
		<!-- translate, not left: left is a layout property, so the browser reflows
		     the row every frame and the slide stutters on a phone. The knob has a
		     colour per track because one palette's on-track is near-white. -->
		<span
			class={`absolute top-0.5 left-0.5 h-[18px] w-[18px] rounded-full shadow-sm transition-[transform,background-color] duration-200 ${
				on ? 'translate-x-3.5 bg-knob-on' : 'translate-x-0 bg-knob'
			}`}
		></span>
	</span>
</button>
