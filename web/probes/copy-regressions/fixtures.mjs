import { fixtures } from '../stub.mjs';

// Each scenario owns its data, failure switches and request history.
export async function copyFixture() {
	const shape = await fixtures();
	shape.settings.settings.RADARR_API_KEY.set = true;
	const card = shape.library.titles.find((card) => card.id === shape.title.id);
	const folder = shape.title.folder;
	// The same series held by a remote Sonarr too: its 2160p files live there.
	const remote = '/remote/Severance';
	const held = [
		{ source: 'Sonarr', folder },
		{ source: 'Sonarr remote', folder: remote }
	];
	const files = Array.from({ length: 15 }, (_, at) =>
		['1080p', '2160p'].map((quality, copy) => {
			const name = `Show S01E${String(at + 1).padStart(2, '0')}.${quality}.mkv`;
			return {
				...shape.title.files[0],
				name,
				path: `${held[copy].folder}/Season 1/${name}`,
				source: held[copy].source,
				status: copy ? 'conform' : 'pending',
				why: {},
				planned: []
			};
		})
	).flat();
	shape.library.conflicts = [
		{
			folder,
			owner: 'sonarr',
			owner_label: 'Sonarr',
			others: ['sonarr-remote'],
			other_labels: ['Sonarr remote']
		}
	];
	shape.title.folders = held;
	card.files = files.length;
	card.variants = 2;
	// Another title to open while the first still has work in flight.
	const other = { ...card, id: 'arr:sonarr:99', name: 'Other title', variants: undefined };
	shape.library.titles.push(other);
	const pathHint =
		'Library roots: 1 of 2 visible and inside MEDIA_DIRS.\n/data/media/movies: visible as a directory to Trackstarr; inside MEDIA_DIRS.\n/data/media/4k: not visible as a directory to Trackstarr; check container mounts and permissions; outside MEDIA_DIRS; add this library root to MEDIA_DIRS for sweeps.';
	const requests = [];
	const state = {
		failMore: true,
		failRefresh: false,
		addSeason: false,
		reverse: false,
		missingSelected: false,
		webhookState: 'missing',
		moveGroup: false,
		emptyTitle: false
	};
	const answers = {
		'/api/library/work': { queued: [], active: [], pauses: [] },
		'/api/library/title': (request, response) => {
			const query = new URL(request.url, 'http://localhost').searchParams;
			const pages = Number(query.get('pages') ?? 1);
			if (query.get('id') === other.id)
				return {
					...shape.title,
					id: other.id,
					name: other.name,
					folders: [held[0]],
					files: [],
					total: 0
				};
			requests.push(pages);
			if (state.failRefresh || (pages > 1 && state.failMore)) {
				response.statusCode = 503;
				return { status: 'try again' };
			}
			const ordered = state.moveGroup ? [...files.slice(2), ...files.slice(0, 2)] : files;
			const shown = state.emptyTitle ? [] : ordered.slice(0, pages > 1 ? 30 : 28);
			if (state.addSeason && pages > 1)
				shown.push({
					...files[0],
					name: 'Show S02E01.mkv',
					path: `${folder}/Season 2/Show S02E01.mkv`
				});
			if (state.reverse) {
				for (let at = 0; at + 1 < shown.length; at += 2)
					[shown[at], shown[at + 1]] = [shown[at + 1], shown[at]];
			}
			return {
				...shape.title,
				total: state.emptyTitle ? 0 : files.length + (state.addSeason ? 1 : 0),
				files: shown.filter((file) => !state.missingSelected || file.path !== files[1].path)
			};
		},
		'/api/connections/test': () => ({
			ok: true,
			detail: 'Radarr answered',
			hint: pathHint,
			paths: 'attention',
			webhook: state.webhookState,
			webhook_detail: ''
		})
	};

	return { shape, card, folder, remote, files, other, state, requests, answers };
}
