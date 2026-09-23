import type { TitleSession } from '$lib/title-browsing.svelte';
import { refusalText } from '$lib/api';
import { mark as now } from '$lib/clock.svelte';
import { poll, type Poller } from '$lib/poll';
import { getTitleWork, queueAction, type TitleWork, type FileState } from '$lib/queue';
import { getPauses, resume as resumePause, place as placePause, type Pause } from '$lib/pauses';
import { skipFile } from '$lib/runs';

export type FileActionAdapter = (
	path: string,
	standing: FileState,
	action: 'top' | 'skip' | 'pause' | 'resume',
	seconds?: number
) => { current: () => boolean; run: () => Promise<void> };

/** Accepted actions finish against captured targets; feedback belongs to one opening. */
export class TitleWorkController {
	work = $state<TitleWork>();
	workSeen = $state(0);
	workError = $state('');
	workBusy = $state(false);
	pauses = $state<Pause[]>([]);
	pauseBusy = $state('');
	pauseError = $state('');
	private workTicket = 0;
	private pauseTicket = 0;
	private actionTicket = 0;
	private opening: TitleSession | null = null;
	private disposed = false;
	open(session: TitleSession) {
		if (this.disposed) return;
		this.close();
		this.opening = session;
		this.work = undefined;
		this.workSeen = 0;
		this.workError = '';
		this.workBusy = false;
		this.pauses = [];
		this.pauseBusy = '';
		this.pauseError = '';
		this.workPoll = this.watch();
		void this.lookUpPauses();
		this.workPoll?.now();
	}
	close() {
		this.workPoll?.stop();
		this.workPoll = null;
		this.opening = null;
		this.actionTicket++;
		this.workTicket++;
		this.pauseTicket++;
	}
	dispose() {
		if (this.disposed) return;
		this.disposed = true;
		this.close();
	}
	private current(opening = this.opening) {
		return !!opening && !this.disposed && this.opening === opening && opening.current();
	}
	private action() {
		const opening = this.opening;
		const ticket = ++this.actionTicket;
		this.workTicket++;
		this.pauseTicket++;
		return () => this.current(opening) && ticket === this.actionTicket;
	}
	fileAction: FileActionAdapter = (path, standing, action, seconds) => {
		const current = this.action();
		const waiting = standing.waiting.map((item) => ({ ...item }));
		const running = standing.running.map((item) => ({ ...item }));
		if (current()) {
			this.workBusy = true;
			this.pauseError = '';
		}
		return {
			current,
			run: async () => {
				if (!current()) return;
				try {
					if (action === 'top') await queueAction('top', waiting);
					else if (action === 'resume') await resumePause({ paths: [path] });
					else {
						if (action === 'pause') await placePause({ paths: [path] }, seconds ?? 0);
						await this.cancelWork(waiting, running);
					}
				} finally {
					if (current()) {
						this.workBusy = false;
						await this.readWork();
					}
				}
			}
		};
	};
	async queueAct(action: 'top' | 'skip', onrefresh?: () => void) {
		if (!this.current()) return;
		const current = this.action();
		this.workBusy = true;
		this.pauseError = '';
		try {
			if (action === 'skip')
				await this.cancelWork(
					(this.work?.queued ?? []).map((item) => ({ ...item })),
					(this.work?.active ?? []).map((item) => ({ ...item }))
				);
			else await queueAction('top', this.work?.queued ?? []);
		} catch (error) {
			if (current()) this.pauseError = refusalText(error);
		} finally {
			if (current()) {
				this.workBusy = false;
				await this.readWork();
				if (current()) this.workPoll?.now();
			}
			onrefresh?.();
		}
	}

	private async cancelWork(waiting: TitleWork['queued'], running: TitleWork['active']) {
		if (waiting.length) await queueAction('skip', waiting);
		for (const item of running) await skipFile(item.run, item.path);
	}

	async readWork() {
		const opening = this.opening;
		if (!this.current(opening) || this.workBusy) return;
		const ticket = ++this.workTicket;
		const pauseRead = this.pauseTicket;
		const current = () => this.current(opening) && !this.workBusy && ticket === this.workTicket;
		try {
			const answer = await getTitleWork(opening!.id);
			if (!current()) return;
			// The queue letting go is the verdict changing. Read the new word
			// first, so the header goes from Processing to it rather than through
			// the one it had.
			if (
				(this.work?.queued.length || this.work?.active.length) &&
				!answer.active.length &&
				!answer.queued.length
			)
				await opening!.reload();
			if (current()) {
				this.work = answer;
				this.workSeen = now();
				if (pauseRead === this.pauseTicket) {
					this.pauseTicket++;
					this.pauses = answer.pauses;
				}
				this.workError = '';
			}
		} catch {
			if (current()) this.workError = 'Could not refresh queue status.';
		}
	}
	private workPoll: Poller | null = null;
	private watch() {
		return poll({
			ask: () => this.readWork(),
			ready: () => this.current() && !this.workBusy,
			pace: () => (this.work?.active.some((item) => item.skipped) ? 1000 : 5000),
			gap: 1000,
			kinds: ['runs', 'progress']
		});
	}
	// Quietly: a title that cannot be read for pauses still shows its files, and
	// the row is simply not offered.
	private async lookUpPauses() {
		const opening = this.opening;
		const ticket = ++this.pauseTicket;
		const current = () => this.current(opening) && ticket === this.pauseTicket;
		try {
			const found = await getPauses();
			if (current()) this.pauses = found;
		} catch {
			// A failed lookup must not erase a newer queue or mutation response.
			if (current()) this.pauses = [];
		}
	}

	async keep(seconds: number) {
		if (!this.current()) return;
		const current = this.action();
		const id = this.opening!.id;
		// Finish the requested cancellation even if another title opens meanwhile.
		const waiting = (this.work?.queued ?? []).map((item) => ({ ...item }));
		const running = (this.work?.active ?? []).map((item) => ({ ...item }));
		this.pauseBusy = String(seconds);
		this.workBusy = true;
		this.pauseError = '';
		try {
			const found = await placePause({ ids: [id] }, seconds);
			if (current()) this.pauses = found;
			await this.cancelWork(waiting, running);
		} catch (error) {
			if (current()) throw error;
		} finally {
			if (current()) {
				this.pauseBusy = '';
				this.workBusy = false;
				await this.readWork();
			}
		}
	}

	async release(pause?: Pause) {
		if (!this.current()) return;
		const current = this.action();
		this.pauseBusy = 'resume';
		this.workBusy = true;
		this.pauseError = '';
		try {
			const found = await resumePause(
				pause && !pause.title_id ? { paths: [pause.path] } : { ids: [this.opening!.id] }
			);
			if (current()) this.pauses = found;
		} catch (error) {
			if (current()) this.pauseError = refusalText(error);
		} finally {
			if (current()) {
				this.pauseBusy = '';
				this.workBusy = false;
				await this.readWork();
			}
		}
	}
}
