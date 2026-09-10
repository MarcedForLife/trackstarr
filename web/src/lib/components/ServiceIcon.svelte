<script lang="ts">
	import type { MarkName } from '$lib/connections';

	// Each service's logo from Dashboard Icons, the set Homepage and Homarr use;
	// IMDb's wordmark is Simple Icons'. Each remains its owner's trademark. They
	// keep their own colours in both themes, which the fixed dark tile allows.
	// Sonarr's corner ticks are dropped: a quarter of a pixel at 20px.

	// `box` is the tile, `size` the mark. A pair rather than a ratio: a small tile
	// wants proportionally less padding.
	let { name, size = 20, box = 32 }: { name: MarkName; size?: number; box?: number } = $props();

	// The corner scales with the tile, as in controls.ts.
	const radius = $derived(Math.round(box * 0.3));

	// Fixed rather than tokenised, since not moving is the point. One tile colour
	// for all four: a tint per service made them look like four systems.
	const TILE = '#25272f';

	// IMDb's ground is part of its logo, so the tile becomes it.
	const badge = $derived(name === 'imdb');
	const ground = $derived(badge ? '#f5c518' : TILE);

	// The wordmark fills the tile at the 86% inset below; `size` has no say.
	const drawn = $derived(badge ? box : size);
</script>

<span
	aria-hidden="true"
	class="flex flex-none items-center justify-center"
	style={`background: ${ground}; width: ${box}px; height: ${box}px; border-radius: ${radius}px`}
>
	<svg width={drawn} height={drawn} viewBox="0 0 512 512">
		{#if name === 'radarr'}
			<g transform="translate(70 21)">
				<path
					d="m10.3 59.8 3.9 372.4c-31.4 3.9-54.9-11.8-54.9-43.1l-3.9-309.7c0-98 90.2-121.5 145.1-82.3l278.3 160.7c39.2 27.4 47 78.4 27.4 113.7-3.9-27.4-15.7-43.1-39.2-58.8L53.4 36.2C29.9 20.6 10.3 24.5 10.3 59.8"
					fill="#fff"
				></path>
				<path
					d="M-13.2 451.8c23.5 7.8 47 3.9 66.6-7.8l321.5-188.2c19.6 27.4 15.7 54.9-7.8 70.6L96.5 483.2c-39.2 19.6-90.1 0-109.7-31.4"
					fill="#fff"
				></path>
				<path d="M80.9 342 273 232.3 84.8 126.4z" fill="#ffc230"></path>
			</g>
		{:else if name === 'sonarr'}
			<path
				d="M511.8 256c0 70.4-24.9 130.8-74.6 181.1-1.7 2-3.5 3.8-5.5 5.4-8.2 8-16.8 15.3-26 21.8Q341.05 512 256.3 512c-56.6 0-106.3-15.9-149.2-47.7-11.3-8-22-17.1-31.9-27.3C36.5 398.7 12.8 354 4 303.2c-1.7-9.9-2.9-20-3.4-30.2-.2-5.7-.4-11.3-.4-17 0-6 .1-11.7.4-17.1 0-.6.2-1.1.5-1.7 3.7-62.8 28.4-117 74.1-162.8C125.5 24.8 185.8 0 256.2 0c70.7 0 131 24.8 180.9 74.5q74.7 75.9 74.7 181.5"
				fill="#eee"
				fill-rule="evenodd"
				clip-rule="evenodd"
			></path>
			<path
				d="m459.7 100.3-52.9 52.9c-30.9 30.9-33.6 57.8-33.6 105.3 0 42.3 6.7 81.1 38.2 112.6 23 23 44.9 44.7 44.9 44.7-5.9 7.2-12.3 14.3-19.1 21.2-1.7 2-3.5 3.8-5.5 5.4-6 5.9-12.2 11.4-18.6 16.4l-41.4-41.4C334.9 380.6 305.6 377 257 377c-46.7 0-78.4 4.3-112.6 38.5-20.4 20.4-43.8 43.9-43.8 43.9-8.9-6.8-17.3-14.2-25.3-22.4-6.6-6.6-12.8-13.4-18.5-20.3 0 0 23.1-23.2 45.2-45.3 32.7-32.7 38-70.6 38-113 0-41.3-6.8-79.8-36.8-109.9C82.2 127.7 53.3 99 53.3 99c6.7-8.5 14-16.7 21.8-24.5 6.9-6.8 14-13.1 21.2-19l48 48c30.7 30.7 70 38.6 112.4 38.6 43.6 0 82.8-8.4 114.7-40.4C391 82.1 417 56.3 417 56.3c6.8 5.6 13.5 11.6 20.1 18.2 8.3 8.3 15.8 16.9 22.6 25.8"
				fill="#3a3f51"
				fill-rule="evenodd"
				clip-rule="evenodd"
			></path>
			<path
				d="M186 269.1c-.5-2.8-.8-5.5-.9-8.4-.1-1.6-.1-3.1-.1-4.7 0-1.7 0-3.2.1-4.7 0-.2 0-.3.1-.5 1-17.4 7.9-32.4 20.5-45.1 13.9-13.8 30.6-20.7 50.2-20.7s36.3 6.9 50.2 20.7c13.8 14 20.7 30.8 20.7 50.3s-6.9 36.2-20.7 50.2c-.5.5-1 1.1-1.5 1.5q-3.45 3.3-7.2 6-18 13.2-41.4 13.2c-23.4 0-29.4-4.4-41.3-13.2-3.1-2.2-6.1-4.7-8.9-7.6-10.8-10.6-17.3-22.9-19.8-37"
				fill="#0cf"
				fill-rule="evenodd"
				clip-rule="evenodd"
			></path>
		{:else if name === 'plex'}
			<path fill="#e5a00d" d="M256 70H148l108 186-108 186h108l108-186z"></path>
		{:else if name === 'imdb'}
			<!-- Only the letters of Simple Icons' badge; its frame is a third of a
			     pixel at 20px. Drawn 18.376 wide centred on (12.078, 11.99) in a 24
			     box, so 86% of the tile matches IMDb's own inset. -->
			<g transform="translate(256 256) scale(23.96) translate(-12.078 -11.99)">
				<path
					fill="#000"
					d="M4.7954 8.2603v7.3636H2.8899V8.2603h1.9055zm6.5367 0v7.3636H9.6707v-4.9704l-.6711 4.9704H7.813l-.6986-4.8618-.0066 4.8618h-1.668V8.2603h2.468c.0748.4476.1492.9694.2307 1.5734l.2712 1.8713.4407-3.4447h2.4817zm2.9772 1.3289c.0742.0404.122.108.1417.2034.0279.0953.0345.3118.0345.6442v2.8548c0 .4881-.0345.7867-.0955.8954-.0609.1152-.2304.1695-.5018.1695V9.5211c.204 0 .3457.0205.4211.0681zm-.0211 6.0347c.4543 0 .8006-.0265 1.0245-.0742.2304-.0477.4204-.1357.5694-.2648.1556-.1218.2642-.298.3251-.5219.0611-.2238.1021-.6648.1021-1.3224v-2.5832c0-.6986-.0271-1.1668-.0742-1.4039-.041-.237-.1431-.4543-.3126-.6437-.1695-.1973-.4198-.3324-.7456-.421-.3191-.0808-.8542-.1285-1.7694-.1285h-1.4244v7.3636h2.3051zm5.14-1.7827c0 .3523-.0199.5762-.0544.6708-.033.0947-.1894.1424-.3046.1424-.1086 0-.19-.0477-.2238-.1351-.041-.0887-.0609-.2986-.0609-.6238v-1.9469c0-.3324.0199-.5423.0543-.6237.0338-.0808.1086-.122.2171-.122.1153 0 .2709.0412.3114.1425.041.0947.0609.2986.0609.6032v1.8926zm-2.4747-5.5809v7.3636h1.7157l.1152-.4675c.1556.1894.3251.3324.5152.4271.1828.0881.4608.1357.678.1357.3047 0 .5629-.0748.7802-.237.2165-.1562.3589-.3462.4198-.5628.0543-.2173.0887-.543.0887-.9841v-2.0675c0-.4409-.0139-.7324-.0344-.8681-.0199-.1357-.0742-.2781-.1695-.4204-.1021-.1425-.2437-.251-.4272-.3325-.1834-.0742-.3999-.1152-.6576-.1152-.2172 0-.4952.0477-.6846.1285-.1835.0887-.353.2238-.5086.4007V8.2603h-1.8309z"
				></path>
			</g>
		{:else}
			<!-- Prefixed ids, or two <defs> called `a` on one page share a gradient. -->
			<linearGradient
				id="jellyfin-inner"
				x1="97.508"
				x2="522.069"
				y1="308.135"
				y2="63.019"
				gradientTransform="matrix(1 0 0 -1 0 514)"
				gradientUnits="userSpaceOnUse"
			>
				<stop offset="0" style="stop-color:#aa5cc3" />
				<stop offset="1" style="stop-color:#00a4dc" />
			</linearGradient>
			<linearGradient
				id="jellyfin-outer"
				x1="94.193"
				x2="518.754"
				y1="302.394"
				y2="57.278"
				gradientTransform="matrix(1 0 0 -1 0 514)"
				gradientUnits="userSpaceOnUse"
			>
				<stop offset="0" style="stop-color:#aa5cc3" />
				<stop offset="1" style="stop-color:#00a4dc" />
			</linearGradient>
			<path
				d="M256 196.2c-22.4 0-94.8 131.3-83.8 153.4s156.8 21.9 167.7 0-61.3-153.4-83.9-153.4"
				fill="url(#jellyfin-inner)"
			></path>
			<path
				d="M256 0C188.3 0-29.8 395.4 3.4 462.2s472.3 66 505.2 0S323.8 0 256 0m165.6 404.3c-21.6 43.2-309.3 43.8-331.1 0S211.7 101.4 256 101.4 443.2 361 421.6 404.3"
				fill="url(#jellyfin-outer)"
			></path>
		{/if}
	</svg>
</span>
