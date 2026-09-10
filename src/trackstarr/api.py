"""The JSON API under /api/: one row per endpoint, and the handler behind it.

Every row states who may call it, so an endpoint cannot inherit a gate by
sitting below a check. :mod:`trackstarr.webhook` owns the socket and answers
through the handler each function here is passed.
"""

import collections
import hashlib
import json
import logging
import os
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, NamedTuple

from . import (
    __version__,
    config,
    connections,
    covers,
    events,
    holds,
    jobs,
    library,
    links,
    notify,
    ratings,
    retag,
    runlog,
    runs,
    sessions,
    settings,
    sweep,
    users,
)
from .arr import reregister_webhooks
from .client import API_ERRORS
from .processing import effective_dry_run

if TYPE_CHECKING:
    from .webhook import Handler

log = logging.getLogger(__name__)

#: Posters are keyed by title id and never change, so a browser keeps one for a
#: week without revalidating.
_COVER_CACHE = "private, max-age=604800, immutable"

#: Seconds between heartbeats on an idle stream. A closed tab resets the socket,
#: but a phone that dropped off wifi says nothing; heartbeats give TCP
#: unacknowledged bytes to time out on, so the thread is shed in minutes. Also
#: short enough that a proxy's idle timeout never fires.
_STREAM_HEARTBEAT = 20.0

#: The heartbeat frame.
_PING = b'data: {"kind": "ping"}\n\n'


def _queue_status() -> dict:
    """What is waiting, what is in progress, and whether a start may rewrite.

    ``queue`` counts every file still owed, from any source: queued deliveries,
    a sweep's unwalked remainder, a re-check's folders. See
    :func:`trackstarr.runs.workload`.

    ``may_rewrite`` tells the page whether REWRITE_MODE would downgrade an
    apply to a report; nothing else it can read says so.
    """
    waiting, working = runs.workload()
    return {
        "queue": waiting,
        "working": working,
        # Encodes in flight, as distinct from probes: what a stop would waste.
        "rewrites": runs.rewrites(),
        "parked": jobs.parked_count(),
        "may_rewrite": not effective_dry_run(False),
        "next_sweep": sweep.next_scheduled(),
        # Small and set by hand, so it rides the snapshot every page already
        # polls rather than needing a fetch of its own.
        "holds": holds.as_json(),
    }


#: Longest reason kept with a hold. A sentence, not a note.
_REASON_MAX = 120


def _under_media(path: str) -> bool:
    """Whether a path lies in a swept library.

    A hold is matched by prefix, so one on ``/`` would quietly stop the whole
    library being rewritten.
    """
    candidate = os.path.normpath(path)
    return any(
        candidate == root or candidate.startswith(os.path.join(root, ""))
        for root in (os.path.normpath(media) for media in config.current().MEDIA_DIRS)
    )


def _hold_targets(body: dict) -> tuple[list[tuple[str, str, str]], tuple[int, str] | None]:
    """What a hold request names, as (path, title id, name), or the refusal.

    Titles come from ``ids`` and resolve through the library, so an id can only
    ever reach a folder the library knows. ``paths`` is for a single file,
    which the overview names off a run, and is checked against MEDIA_DIRS.
    """
    wanted = body.get("ids")
    ids = [str(entry) for entry in wanted if str(entry)] if isinstance(wanted, list) else []
    named = body.get("paths")
    paths = [str(entry) for entry in named if str(entry)] if isinstance(named, list) else []
    if not ids and not paths:
        return [], (400, "name the titles or files to hold")
    found = [(title.folder, title.id, title.name) for title in library.selected(ids)]
    if len(found) != len(ids):
        return [], (404, "no such title")
    for path in paths:
        if not _under_media(path):
            return [], (400, "that file is not in a swept library")
        found.append((path, "", os.path.basename(path)))
    return found, None


def _subject(found: library.Title) -> links.Subject:
    """The few facts about a title that :mod:`trackstarr.links` needs."""
    return links.Subject(
        folder=found.folder,
        name=found.name,
        year=found.year,
        arr=found.arr.name if found.arr else "",
        slug=found.slug,
        imdb_id=found.imdb_id,
    )


def _event_files(entry: dict) -> list[str]:
    """The library files one history line names.

    A verdict names one file; a delivery names everything it queued; other
    kinds name none.
    """
    if isinstance(queued := entry.get("paths"), list):
        return [path for path in queued if isinstance(path, str) and path]
    path = entry.get("path")
    return [path] if isinstance(path, str) and path else []


def _attach_titles(entries: list[dict]) -> dict[str, dict]:
    """Tag each line with its title id and return the cards for those titles.

    A delivery spanning two titles is left untagged: a poster naming half of
    it is worse than none. One library call per page, and it never raises,
    since the history must draw without the *arrs.
    """
    owners, cards = library.cards_for_paths(
        path for entry in entries for path in _event_files(entry)
    )
    if not owners:
        return {}
    kept: dict[str, dict] = {}
    for entry in entries:
        found = {owners[path] for path in _event_files(entry) if path in owners}
        if len(found) == 1:
            title_id = found.pop()
            entry["title"] = title_id
            kept[title_id] = cards[title_id]
    return kept


def _whoami(signed_in: users.Account) -> dict:
    """The account shape /api/auth/me and a sign-in answer with."""
    return {
        "name": signed_in.name,
        "role": signed_in.role,
        "must_change": signed_in.must_change,
    }


class Access(Enum):
    """Who may call an endpoint, stated per route.

    VIEWER also takes a machine secret, which a read may carry instead of a
    cookie. SESSION is any signed-in account, even one that still owes a
    password change: the endpoints such an account must reach to make one.
    ADMIN is an admin whose password is not owed.
    """

    VIEWER = auto()
    SESSION = auto()
    ADMIN = auto()


#: A read is handed the query string, a write the account it is signed in as
#: with its body still unread.
type _GetHandler = Callable[[Handler, str], None]
type _PostHandler = Callable[[Handler, users.Account], None]


def _refusal(access: Access, signed_in: users.Account) -> tuple[int, str] | None:
    """Why a signed-in account may not have an endpoint, as the code and
    message to answer with, or None to serve it."""
    if access is Access.SESSION:
        return None
    if signed_in.must_change:
        # The bootstrap password sits in the docker log, so until it is
        # replaced it unlocks nothing but the change itself.
        return 403, "password change required"
    if access is Access.ADMIN and signed_in.role != "admin":
        return 403, "forbidden"
    return None


def serve_get(handler: Handler, path: str, query: str) -> None:
    """The API's reads, off :data:`_GET_ROUTES`.

    /api/auth/me answers ahead of the table: it is the one read about the
    caller rather than the service, so it takes no machine secret and an
    account that still owes a password change may ask it.
    """
    signed_in = handler.session()
    if path == "/api/auth/me":
        if signed_in:
            handler.send_json(_whoami(signed_in))
        else:
            handler.reply(401, "unauthorized")
        return
    route = _GET_ROUTES.get(path)
    # A path with no row is gated as a read, so an unauthenticated caller
    # cannot map the API by which paths answer 404.
    access = route.access if route else Access.VIEWER
    if signed_in is None:
        # A read may carry a machine secret instead of a cookie, so a
        # leaked *arr credential can watch but not write.
        if not handler.authorized():
            handler.reply(401, "unauthorized")
            return
    elif refusal := _refusal(access, signed_in):
        handler.reply(*refusal)
        return
    if route is None:
        handler.reply(404, "not found")
        return
    route.handler(handler, query)


def _serve_status(handler: Handler, query: str) -> None:
    handler.send_json({"version": __version__, **_queue_status()})


def _serve_runs(handler: Handler, query: str) -> None:
    """Every run and the queue. Readable by a viewer; only the buttons are
    an admin's."""
    handler.send_json({**runs.snapshot(), **_queue_status()})


def _serve_holds(handler: Handler, query: str) -> None:
    handler.send_json({"holds": holds.as_json()})


def _serve_settings(handler: Handler, query: str) -> None:
    handler.send_json(settings.snapshot())


def _serve_library(handler: Handler, query: str) -> None:
    """The whole shelf, for the grid.

    Tagged: the loader refetches this on every visit to the grid, and an
    unchanged library then crosses the wire as a header.
    """
    handler.send_json(library.shelf(), tag=True)
    # Prefetch posters never seen before the grid asks for them, even
    # behind a 304.
    covers.warm()


def _serve_summary(handler: Handler, query: str) -> None:
    """The overview's strip: a tally and a dozen posters. No warm-up."""
    order = urllib.parse.parse_qs(query).get("sort", [""])[0]
    handler.send_json(library.summary(order=order), tag=True)


def _serve_ratings(handler: Handler, query: str) -> None:
    """How many titles are scored. One small file, so the sweep page need
    not load the shelf to count."""
    handler.send_json(ratings.summary())


def _serve_stream(handler: Handler, query: str) -> None:
    """Server-sent events: a ``data:`` line per kind :mod:`notify`
    publishes, and a heartbeat while nothing does.

    Frames carry no payload; the page refetches through the endpoints
    above, which keeps the ETags working. The one answer without a
    Content-Length, so the connection must close with it. X-Accel-Buffering
    asks nginx to pass each line on as written.
    """
    subscription = notify.subscribe()
    if subscription is None:
        # The page falls back to polling.
        handler.reply(503, "too many open streams")
        return
    try:
        # Inside the try: a peer gone before the headers must still
        # unsubscribe, or one of MAX_STREAMS is lost until a restart.
        handler.close_connection = True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("Connection", "close")
        handler.end_headers()
        while True:
            kinds = subscription.take(_STREAM_HEARTBEAT)
            frames = "".join(f"data: {json.dumps({'kind': kind})}\n\n" for kind in kinds)
            # One write per wake, since Nagle is off.
            handler.wfile.write(frames.encode() if frames else _PING)
    # A closed tab, a sleeping phone or a proxy giving up all surface as a
    # failed write. Wider than handle()'s pair: a peer that stopped reading
    # ends as a socket timeout, not a reset.
    except OSError:
        log.debug("stream client went away")
    finally:
        notify.unsubscribe(subscription)


def _serve_run_log(handler: Handler, query: str) -> None:
    """What one file's worker logged.

    Separate from the snapshot every tab polls; fetched for the one row
    somebody opened. Empty is normal for a cached verdict or for lines
    since dropped.
    """
    asked = urllib.parse.parse_qs(query)
    run = asked.get("run", [""])[0]
    path = asked.get("path", [""])[0]
    if not run or not path:
        handler.reply(400, "run and path are required")
        return
    handler.send_json({"run": run, "path": path, "lines": runlog.lines(run, path)})


def _serve_title(handler: Handler, query: str) -> None:
    """One title's files, with what each is and what each would become."""
    wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
    titles = library.selected([wanted]) if wanted else []
    found = library.title(wanted) if titles else None
    if found is None:
        handler.reply(404, "no such title")
        return
    # Which services could offer a link is a settings read, so the sheet
    # draws its buttons at once. Resolving them means calling the media
    # servers, which is the second request.
    handler.send_json({**found, "servers": links.offered(_subject(titles[0]))})


def _serve_links(handler: Handler, query: str) -> None:
    """Where a title lives in Plex, Jellyfin and its *arr, for the sheet's
    Open in buttons.

    Its own request because it calls each media server. One that is off,
    unreachable or has no such item contributes no button.
    """
    wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
    found = library.selected([wanted]) if wanted else []
    if not found:
        handler.reply(404, "no such title")
        return
    handler.send_json({"links": links.for_title(_subject(found[0]))})


def _serve_cover(handler: Handler, query: str) -> None:
    """A title's poster, from our copy of the *arr's cache.

    Private and immutable in the browser: a grid asks for hundreds at once
    and a title's poster never changes. The ETag serves caches in between
    and the day the week runs out.
    """
    wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
    art = covers.cover(wanted) if wanted else None
    if art is None:
        # The grid draws its own tile. Cached briefly: the title is on
        # every reload.
        handler.send_response(404)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "private, max-age=600")
        handler.end_headers()
        return
    body, content_type = art
    tag = f'"{hashlib.blake2s(body, digest_size=8).hexdigest()}"'
    if handler.headers.get("If-None-Match") == tag:
        handler.send_response(304)
        handler.send_header("ETag", tag)
        handler.send_header("Cache-Control", _COVER_CACHE)
        handler.end_headers()
        return
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", _COVER_CACHE)
    handler.send_header("ETag", tag)
    handler.end_headers()
    handler.wfile.write(body)


def _serve_events(handler: Handler, query: str) -> None:
    """A page of the history, newest first, with the cursor for the next.

    ``before`` is a byte offset from a previous answer: the file is
    append-only, so an offset names the same line for ever and resuming
    costs no scan. ``next`` is null once the oldest event is handed over.
    ``since`` and ``until`` are ISO 8601 bounds on the window and page
    with ``before`` like the whole history does.
    """
    params = urllib.parse.parse_qs(query)
    try:
        limit = int(params.get("limit", ["100"])[0])
        before = int(params["before"][0]) if "before" in params else None
    except ValueError:
        handler.reply(400, "limit and before must be whole numbers")
        return
    if not 1 <= limit <= events.MAX_READ or (before is not None and before < 0):
        handler.reply(400, f"limit must be 1-{events.MAX_READ}, before cannot be negative")
        return
    try:
        since = events.moment(params["since"][0]) if "since" in params else None
        until = events.moment(params["until"][0]) if "until" in params else None
    except ValueError:
        handler.reply(400, "since and until must be ISO 8601 moments")
        return
    # Crossed bounds are momentary while somebody types: answer empty,
    # not 400.
    if since is not None and until is not None and since > until:
        handler.send_json({"events": [], "titles": {}, "next": None}, tag=True)
        return
    entries, cursor = events.read(limit, before, since, until)
    # Tagged like the shelf: the page refetches on every visit and the
    # lines rarely change. The cards let the feed draw a poster and raise
    # the library's title sheet.
    cards = _attach_titles(entries)
    handler.send_json({"events": entries, "titles": cards, "next": cursor}, tag=True)


def serve_post(handler: Handler, path: str) -> None:
    """The API's writes, off :data:`_POST_ROUTES`, for sessions only.

    Each demands a JSON content type, which a cross-site form cannot send;
    with SameSite that is the whole CSRF (cross-site request forgery)
    defence. Signing in answers ahead of the table, being the one write
    with no account to gate. Every row states its own access, so a new
    endpoint cannot inherit one by sitting below a check.

    Each handler takes the account whether it needs the name or not, so
    the table has one shape.
    """
    if not (handler.headers.get("Content-Type") or "").startswith("application/json"):
        handler.refuse(415, "expected application/json")
        return
    if path == "/api/auth/login":
        _login(handler)
        return
    signed_in = handler.session()
    if signed_in is None:
        handler.refuse(401, "unauthorized")
        return
    route = _POST_ROUTES.get(path)
    # A path with no row is gated as a write, so 403-before-404 keeps a
    # viewer from learning which writes exist.
    if refusal := _refusal(route.access if route else Access.ADMIN, signed_in):
        handler.refuse(*refusal)
        return
    if route is None:
        handler.refuse(404, "not found")
        return
    route.handler(handler, signed_in)


def _refresh_ratings(handler: Handler, signed_in: users.Account) -> None:
    """Fetch IMDb's ratings dataset now rather than at the next tick.

    For the settings page after the scores are switched on. Skips the
    loop's two intervals: this was asked for.
    """
    if handler.read_json() is None:
        return
    if not config.current().IMDB_RATINGS:
        handler.reply(409, "title scores are switched off")
        return
    wanted = library.imdb_ids()
    if wanted is None:
        handler.reply(409, "an *arr could not be listed, so its titles would lose their scores")
        return
    if not wanted:
        handler.reply(409, "no title in the library carries an IMDb id")
        return
    try:
        ratings.refresh(wanted)
    except API_ERRORS as err:
        log.warning("imdb ratings: fetch from the web UI failed (%s)", err)
        handler.reply(502, "could not fetch the dataset from IMDb")
        return
    # The shelf built for imdb_ids() predates the new table. See
    # ratings.refresh_pass.
    library.forget()
    log.info("imdb ratings fetched from the web UI by %s", signed_in.name)
    # "676 scored" only means something beside how many were looked for.
    handler.send_json({**ratings.summary(), "titles": len(wanted)})


def _run_mode(handler: Handler, body: dict) -> str | None:
    """The body's report-or-apply mode, or None with the 400 already served.

    Report is the default, like the CLI without ``--apply``. REWRITE_MODE
    latches over either.
    """
    mode = str(body.get("mode") or "report")
    if mode in ("report", "apply"):
        return mode
    handler.reply(400, "mode is report or apply")
    return None


def _walk_refused(handler: Handler) -> bool:
    """Whether a walk cannot start now, with the refusal already served.

    Two walks would fight over the cache and pending.tsv, so the second is
    turned down naming the first. A re-check counts as a walk.
    """
    if runs.paused():
        handler.reply(409, "processing is paused. Resume it first")
        return True
    if existing := runs.cache_holder():
        handler.send_json(
            {"status": f"a {existing.kind} is already running", "run": existing.id}, 409
        )
        return True
    return False


def _recheck_titles(handler: Handler, signed_in: users.Account) -> None:
    """Re-probe the chosen titles now, ignoring the cache.

    A sweep over picked titles: same modes, REWRITE_MODE latch, pause and
    one-walk-at-a-time rule, but every file is re-probed since the stored
    verdict is usually what is in question. The body names title ids,
    resolved against the library, so it can reach nothing else.
    """
    body = handler.read_json()
    if body is None:
        return
    mode = _run_mode(handler, body)
    if mode is None:
        return
    wanted = body.get("ids")
    ids = [str(entry) for entry in wanted] if isinstance(wanted, list) else []
    if not ids:
        handler.reply(400, "name the titles to run")
        return
    if _walk_refused(handler):
        return
    chosen = library.selected(ids)
    if not chosen:
        handler.reply(404, "no such title")
        return
    run = events.run_id()
    # The run card's label: one title by name, more by count.
    label = chosen[0].name if len(chosen) == 1 else f"{len(chosen)} titles"
    log.info(
        "re-check of %d title(s) started from the web UI by %s (mode=%s)",
        len(chosen),
        signed_in.name,
        mode,
    )
    threading.Thread(
        target=_recheck_thread,
        args=([title.folder for title in chosen], mode != "apply", run, label),
        daemon=True,
        name="recheck",
    ).start()
    handler.send_json({"status": "started", "run": run, "titles": len(chosen)})


def _retag_targets(body: dict) -> tuple[list[tuple[str, int]], tuple[int, str] | None]:
    """The tracks a retag names, as (path, stream index), or the refusal.

    Paths rather than title ids, since a track is one stream of one file,
    and checked against MEDIA_DIRS as a hold on a file is.
    """
    named = body.get("tracks")
    if not isinstance(named, list) or not named:
        return [], (400, "name the tracks to change, as a path and a stream index each")
    if len(named) > retag.MAX_TARGETS:
        return [], (400, f"at most {retag.MAX_TARGETS} tracks at once")
    targets: list[tuple[str, int]] = []
    for entry in named:
        if not isinstance(entry, dict):
            return [], (400, "each track is a path and a stream index")
        path, index = entry.get("path"), entry.get("index")
        # bool is an int, so `true` would otherwise pass as stream 1.
        if not isinstance(path, str) or not isinstance(index, int) or isinstance(index, bool):
            return [], (400, "each track is a path and a stream index")
        if not _under_media(path):
            return [], (400, "that file is not in a swept library")
        # Normalised, since the sweep cache is keyed by the path as walked and a
        # spelling that misses its entry would read as a file never judged.
        path = os.path.normpath(path)
        if any(path == named_path for named_path, _ in targets):
            # The staleness check reads one snapshot of the cache, which the
            # first edit to a file puts out of date for the second.
            return [], (400, "one track per file per request")
        targets.append((path, index))
    return targets, None


def _retag_tracks(handler: Handler, signed_in: users.Account) -> None:
    """Change the language or flags of tracks in place and judge each file
    again.

    One edit for every track named, which is how a season's untagged
    original track is fixed in one press. Each file answers for itself.
    Refused during a walk, which would book over the fresh verdicts; a pause
    is no bar, since nothing here rewrites.
    """
    body = handler.read_json()
    if body is None:
        return
    try:
        edit = retag.parse_edit(body)
    except ValueError as err:
        handler.reply(400, str(err))
        return
    targets, refusal = _retag_targets(body)
    if refusal:
        handler.reply(*refusal)
        return
    if _cache_busy(handler, "Wait for it to finish"):
        return
    results = retag.apply_all(targets, edit, signed_in.name)
    tally = collections.Counter(str(result.status) for result in results)
    log.info("retag from the web UI by %s: %s, %s", signed_in.name, edit, dict(tally))
    handler.send_json({"status": "done", "results": [result.as_json() for result in results]})


def _cache_busy(handler: Handler, advice: str) -> bool:
    """Whether a walk holds the sweep cache, with the refusal already served.
    ``advice`` is what the caller can do about it."""
    if existing := runs.cache_holder():
        handler.send_json(
            {"status": f"a {existing.kind} is running. {advice}", "run": existing.id}, 409
        )
        return True
    return False


def _clear_library(handler: Handler, signed_in: users.Account) -> None:
    """Drop every stored verdict so the next sweep re-probes.

    Refused while a sweep or re-check runs: they hold the entries in memory
    and rewrite the file at each checkpoint, so a delete would be undone
    or would lose their work. Nothing else in STATE_DIR is touched.
    """
    # The body is unused but must leave the socket; the connection is
    # reused.
    if handler.read_json() is None:
        return
    if _cache_busy(handler, "Stop it first"):
        return
    dropped = library.clear()
    log.info("stored verdicts cleared from the web UI by %s", signed_in.name)
    handler.send_json({"status": "cleared", "dropped": dropped})


def _login(handler: Handler) -> None:
    """Trade a name and password for a session cookie.

    Wrong name and wrong password answer alike, and an unknown name still
    costs one scrypt in verify(), so nothing reveals which accounts exist.
    The lock is checked first, or it would confirm a guess.
    """
    body = handler.read_json()
    if body is None:
        return
    name = str(body.get("username") or "")
    password = str(body.get("password") or "")
    if users.locked(name):
        handler.reply(429, "too many attempts. Wait a minute")
        return
    signed_in = users.verify(name, password) if name and password else None
    if not signed_in:
        if name:
            users.note_failure(name)
        log.warning("failed web sign-in for %r", name)
        handler.reply(401, "wrong username or password")
        return
    users.note_success(name)
    try:
        token = sessions.create(signed_in)
    except OSError as err:
        log.error("could not store a session: %s", err)
        handler.reply(500, "could not store the session")
        return
    handler.send_json(_whoami(signed_in), cookie=handler.cookie(token, sessions.TTL))


def _logout(handler: Handler, signed_in: users.Account) -> None:
    # The body is unused but must leave the socket; the connection is
    # reused.
    if handler.read_json() is None:
        return
    try:
        sessions.revoke(handler.session_token())
    except OSError as err:
        log.error("could not revoke a session: %s", err)
        handler.reply(500, "could not revoke the session")
        return
    handler.reply(200, "signed out", cookie=handler.cookie("", 0))


def _update_settings(handler: Handler, signed_in: users.Account) -> None:
    """Write the body's NAME -> value changes to settings.json and apply
    them live.

    null unsets a name. A change that would refuse startup is rolled back
    whole and answered with the CLI's messages. The account name goes to
    the history.
    """
    body = handler.read_json()
    if body is None:
        return
    try:
        problems = settings.update(body, by=signed_in.name)
    except OSError as err:
        log.error("could not write the settings: %s", err)
        handler.reply(500, "could not write the settings")
        return
    if problems:
        handler.send_json({"status": "invalid", "problems": problems}, 400)
        return
    log.info("settings updated: %s", ", ".join(sorted(body)))
    # A raised budget needs threads; a no-op when the pool already matches.
    jobs.start_workers()
    # The library memoises for minutes, and a corrected address must not
    # wait that long. Cost: one refetch.
    library.forget()
    # Likewise the media servers' answers about where titles live.
    links.forget()
    # An *arr that was unset or unreachable can answer for a poster now.
    covers.forget()
    if not _ARR_CONNECTION.isdisjoint(body):
        # Off the request thread: two round trips per *arr.
        threading.Thread(target=reregister_webhooks, daemon=True, name="reregister").start()
    handler.send_json(settings.snapshot())


def _test_connection(handler: Handler, signed_in: users.Account) -> None:
    """Ask one service whether it answers, using the page's unsaved values.

    A missing address or key falls back to the stored one, which is how an
    untouched password field travels. Admin-only matters here: the body
    names an address this container will fetch.
    """
    body = handler.read_json()
    if body is None:
        return
    name = str(body.get("service") or "")
    if name not in connections.BY_NAME:
        handler.reply(404, "no such service")
        return
    result = connections.check(name, str(body.get("url") or ""), str(body.get("key") or ""))
    handler.send_json(asdict(result))


def _check_sweep(handler: Handler, signed_in: users.Account) -> None:
    """When a schedule would next fire, and whether its dirs exist.

    A POST so the admin gate covers it, since the body names paths this
    container stats. Checks the page's unsaved values.
    """
    body = handler.read_json()
    if body is None:
        return
    dirs = body.get("dirs")
    result = sweep.check(
        str(body.get("at") or ""),
        [str(entry) for entry in dirs] if isinstance(dirs, list) else [],
        str(body.get("tz") or ""),
    )
    handler.send_json(asdict(result))


def _start_sweep(handler: Handler, signed_in: users.Account) -> None:
    """Sweep now on a thread, answering with the run id.

    The id lets the page follow this sweep even when a delivery lands
    beside it.
    """
    body = handler.read_json()
    if body is None:
        return
    mode = _run_mode(handler, body)
    if mode is None:
        return
    if _walk_refused(handler):
        return
    run = events.run_id()
    log.info("sweep started from the web UI by %s (mode=%s)", signed_in.name, mode)
    threading.Thread(
        target=_sweep_thread, args=(run, mode != "apply"), daemon=True, name="sweep-now"
    ).start()
    handler.send_json({"status": "started", "run": run})


def _stop_run(handler: Handler, signed_in: users.Account) -> None:
    """Ask a run to wind up after the file it is on.

    Not a kill: the sweep still writes its cache and its summary, and the
    rewrite in flight still publishes. :func:`_abort` is the one that
    takes the machine back immediately.
    """
    body = handler.read_json()
    if body is None:
        return
    run = str(body.get("run") or "")
    if not run:
        handler.reply(400, "name the run to stop")
        return
    if not runs.stop(run):
        # Almost always a page acting on a run that has since finished.
        handler.reply(404, "no such run is going")
        return
    handler.reply(200, "stopping")


def _pause_run(handler: Handler, signed_in: users.Account) -> None:
    _pause(handler, signed_in, True)


def _resume_run(handler: Handler, signed_in: users.Account) -> None:
    _pause(handler, signed_in, False)


def _pause(handler: Handler, signed_in: users.Account, on: bool) -> None:
    """Pause or resume the sweep and the import workers.

    Answers with the next snapshot so the button's state comes from the
    service, not a guess.
    """
    if handler.read_json() is None:
        return
    (runs.pause if on else runs.resume)(signed_in.name)
    handler.send_json({**runs.snapshot(), **_queue_status()})


def _abort(handler: Handler, signed_in: users.Account) -> None:
    """Stop every run and kill the rewrites in flight.

    Rewrites are staged and published only once verified, so this costs
    the encode and never the library file. Unlike :func:`_pause`, nothing
    stops the next delivery starting.
    """
    if handler.read_json() is None:
        return
    # Runs first, or a killed rewrite's run would pick up the next file.
    # Keyed "stopped", not "runs": the page takes any answer with a `runs`
    # key for a snapshot.
    stopped = runs.stop_all()
    handler.send_json({"status": "stopping", "stopped": stopped, "rewrites": runs.abort()})


def _skip_file(handler: Handler, signed_in: users.Account) -> None:
    """Leave one of a run's files alone, killing its rewrite if it has one.

    A skip lasts as long as the run. Nothing stops the next sweep reaching
    the file, which is what :mod:`trackstarr.holds` is for.
    """
    body = handler.read_json()
    if body is None:
        return
    run = str(body.get("run") or "")
    path = str(body.get("path") or "")
    if not run or not path:
        handler.reply(400, "name the run and the file to skip")
        return
    where = runs.skip(run, path)
    if not where:
        handler.reply(404, "that run is not going to reach that file")
        return
    # Signalled once the skip is on the record, so a page refetching
    # mid-kill is told why the file stopped.
    killed = runs.abort(path) if where == "active" else 0
    events.record("skipped", run=run, path=path, by=signed_in.name)
    log.info("%s skipped by %s", path, signed_in.name)
    handler.send_json({"status": "skipped", "where": where, "rewrites": killed})


def _place_hold(handler: Handler, signed_in: users.Account) -> None:
    """Leave a title or a file alone until it lapses or somebody lifts it.

    Files are still probed, planned and reported while held; only the
    rewrite waits. ``seconds`` is how long, 0 for a hold only a person
    ends.
    """
    body = handler.read_json()
    if body is None:
        return
    seconds = body.get("seconds") or 0
    if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds < 0:
        handler.reply(400, "seconds is how long to hold it for, 0 for no end")
        return
    targets, refusal = _hold_targets(body)
    if refusal:
        handler.reply(*refusal)
        return
    if holds.full():
        handler.reply(409, f"{holds.MAX_HOLDS} things are already held. Lift one first")
        return
    reason = str(body.get("reason") or "")[:_REASON_MAX]
    try:
        for path, title, name in targets:
            holds.place(path, float(seconds), signed_in.name, reason, title, name)
    except OSError as err:
        log.error("could not write the holds: %s", err)
        handler.reply(500, "could not store the hold")
        return
    handler.send_json({"status": "held", "holds": holds.as_json()})


def _lift_hold(handler: Handler, signed_in: users.Account) -> None:
    """Let a held title or file be rewritten again. The next sweep reaches
    it; nothing is started here."""
    body = handler.read_json()
    if body is None:
        return
    targets, refusal = _hold_targets(body)
    if refusal:
        handler.reply(*refusal)
        return
    try:
        lifted = [
            path for path, _, _ in targets if holds.lift(path, signed_in.name) is not None
        ]
    except OSError as err:
        log.error("could not write the holds: %s", err)
        handler.reply(500, "could not lift the hold")
        return
    handler.send_json({"status": "lifted", "lifted": len(lifted), "holds": holds.as_json()})


def _change_password(handler: Handler, signed_in: users.Account) -> None:
    """Replace the caller's password after proving the current one.

    Every other session is revoked; the answer carries this browser's new
    one.
    """
    body = handler.read_json()
    if body is None:
        return
    if len(str(body.get("new") or "")) < users.MIN_PASSWORD_LEN:
        handler.reply(400, f"the new password needs {users.MIN_PASSWORD_LEN}+ characters")
        return
    if not users.verify(signed_in.name, str(body.get("current") or "")):
        handler.reply(403, "wrong password")
        return
    changed = replace(signed_in, must_change=False)
    try:
        users.set_password(signed_in.name, str(body["new"]))
        sessions.revoke_user(signed_in.name)
        token = sessions.create(changed)
    # ValueError: the account vanished mid-request (a concurrent `user rm`).
    except (OSError, ValueError) as err:
        log.error("could not change the password: %s", err)
        handler.reply(500, "could not change the password")
        return
    handler.send_json(_whoami(changed), cookie=handler.cookie(token, sessions.TTL))


class Route[Handle](NamedTuple):
    """One endpoint: who may call it, and what answers."""

    access: Access
    handler: Handle


#: The API's reads. A viewer's session or a machine secret; /api/auth/me is
#: served before this table.
_GET_ROUTES: dict[str, Route[_GetHandler]] = {
    "/api/status": Route(Access.VIEWER, _serve_status),
    "/api/runs": Route(Access.VIEWER, _serve_runs),
    "/api/runs/log": Route(Access.VIEWER, _serve_run_log),
    "/api/holds": Route(Access.VIEWER, _serve_holds),
    "/api/settings": Route(Access.VIEWER, _serve_settings),
    "/api/events": Route(Access.VIEWER, _serve_events),
    "/api/library": Route(Access.VIEWER, _serve_library),
    "/api/library/summary": Route(Access.VIEWER, _serve_summary),
    "/api/library/title": Route(Access.VIEWER, _serve_title),
    "/api/library/links": Route(Access.VIEWER, _serve_links),
    "/api/library/cover": Route(Access.VIEWER, _serve_cover),
    "/api/library/ratings": Route(Access.VIEWER, _serve_ratings),
    "/api/stream": Route(Access.VIEWER, _serve_stream),
}


#: The API's writes. Signing in is served before this table; every other write
#: is an admin's but the two an account owing a password change must reach.
_POST_ROUTES: dict[str, Route[_PostHandler]] = {
    "/api/auth/logout": Route(Access.SESSION, _logout),
    "/api/auth/password": Route(Access.SESSION, _change_password),
    "/api/settings": Route(Access.ADMIN, _update_settings),
    "/api/connections/test": Route(Access.ADMIN, _test_connection),
    "/api/sweep/check": Route(Access.ADMIN, _check_sweep),
    "/api/runs/start": Route(Access.ADMIN, _start_sweep),
    "/api/runs/stop": Route(Access.ADMIN, _stop_run),
    "/api/runs/pause": Route(Access.ADMIN, _pause_run),
    "/api/runs/resume": Route(Access.ADMIN, _resume_run),
    "/api/runs/abort": Route(Access.ADMIN, _abort),
    "/api/runs/skip": Route(Access.ADMIN, _skip_file),
    "/api/holds": Route(Access.ADMIN, _place_hold),
    "/api/holds/lift": Route(Access.ADMIN, _lift_hold),
    "/api/library/run": Route(Access.ADMIN, _recheck_titles),
    "/api/library/retag": Route(Access.ADMIN, _retag_tracks),
    "/api/library/clear": Route(Access.ADMIN, _clear_library),
    "/api/library/ratings": Route(Access.ADMIN, _refresh_ratings),
}


# No cover: a thread body around sweep(), which is covered directly.
def _sweep_thread(run: str, dry_run: bool) -> None:  # pragma: no cover
    """One sweep, off the request thread. The page follows it through /api/runs."""
    try:
        sweep.sweep(dry_run=dry_run, run=run)
    except Exception:
        log.exception("sweep started from the web UI failed")


# No cover: a thread body around recheck(), which is covered directly.
def _recheck_thread(  # pragma: no cover
    folders: list[str], dry_run: bool, run: str, label: str
) -> None:
    """One re-check, off the request thread. The page follows it through /api/runs."""
    try:
        sweep.recheck(folders, dry_run=dry_run, run=run, label=label)
    except Exception:
        log.exception("re-check started from the web UI failed")


#: Settings whose change re-registers the webhooks.
_ARR_CONNECTION = frozenset(
    {"RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY", "WEBHOOK_URL"}
)
