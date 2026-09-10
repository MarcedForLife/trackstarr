"""The built web UI: which file a URL serves, and the gzip every answer shares.

Served without a secret; everything the pages show comes through /api/. The
gzip helpers live here because the bundles are what compression is for, and
:mod:`trackstarr.webhook` reuses them for the JSON answers.
"""

import gzip
import mimetypes
import os
import re
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .webhook import Handler

#: Below this a gzip header and trailer cost about what compression saves.
_GZIP_MIN = 1024

#: Where the curve flattens for JSON: a 500-title shelf shrinks 6.3x in about a
#: millisecond, and level 9 spends several more for a few hundred bytes.
_GZIP_LEVEL = 6

#: Static files worth compressing: markup, styles, bundles and SVG. Fonts and
#: pictures arrive compressed already.
_COMPRESSIBLE = re.compile(r"^(text/|application/(javascript|json|xml)|image/svg\+xml)")

#: Content Security Policy for the pages: this origin, plus `data:` for the
#: SVG placeholder posters and the woff2 faces Vite inlines. Styles allow
#: inline because Svelte's `style:` directives are inline attributes.
#:
#: `script-src` allows inline on purpose. The shell carries two inline scripts
#: (the pre-paint theme resolve and SvelteKit's boot), and omitting the
#: directive hands them to `default-src`, which blanks the app. Closing it
#: means hashing both at build time, where SvelteKit's changes every release.
#: The rest still holds: no third-party hosts, no fetch or form to anywhere
#: else, no <base> rewrite, and no framing.
_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def accepts_gzip(header: str) -> bool:
    """Whether an Accept-Encoding header asks for gzip.

    Not a substring test: ``gzip;q=0`` is a refusal.
    """
    for coding in header.split(","):
        name, _, params = coding.strip().partition(";")
        if name.strip().lower() != "gzip":
            continue
        quality = params.strip().lower()
        if not quality.startswith("q="):
            return True
        try:
            return float(quality[2:]) > 0
        # An unparseable quality on a coding the client named: take it as yes.
        except ValueError:
            return True
    return False


def gzipped(body: bytes, accept_encoding: str) -> bytes:
    """The body gzipped, or as it was when that would not help.

    ``mtime=0`` keeps the output deterministic; the default stamps the clock
    into the header.
    """
    if len(body) < _GZIP_MIN:
        return body
    if not accepts_gzip(accept_encoding):
        return body
    return gzip.compress(body, _GZIP_LEVEL, mtime=0)


def static_file(url_path: str) -> str | None:
    """The file WEB_DIR serves for a URL, or None to 404.

    Paths resolving outside WEB_DIR are refused. Extensionless misses fall
    back to index.html for the client-side router; a missing asset is a 404.
    """
    web_dir = config.current().WEB_DIR
    if not web_dir:
        return None
    root = os.path.abspath(web_dir)
    candidate = os.path.normpath(os.path.join(root, url_path.lstrip("/")))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "index.html")
    if os.path.isfile(candidate):
        return candidate
    if "." not in os.path.basename(candidate):
        fallback = os.path.join(root, "index.html")
        if os.path.isfile(fallback):
            return fallback
    return None


def serve_page(handler: Handler, path: str) -> None:
    """A file from the built UI, or the shell for client-side routes."""
    file_path = static_file(path)
    if not file_path:
        handler.reply(404, "not found")
        return
    try:
        with open(file_path, "rb") as static:
            body = static.read()
    except OSError:
        handler.reply(404, "not found")
        return
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    # 330KB of bundles gzips to 124KB. Fonts and images are compressed
    # already.
    accepted = handler.headers.get("Accept-Encoding") or ""
    sent = gzipped(body, accepted) if _COMPRESSIBLE.match(content_type) else body
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(sent)))
    handler.send_header("Vary", "Accept-Encoding")
    # nosniff stops a bundle running as another type; DENY and the CSP's
    # frame-ancestors stop clickjacking; same-origin keeps paths out of
    # outbound referers.
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "same-origin")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Content-Security-Policy", _CSP)
    if sent is not body:
        handler.send_header("Content-Encoding", "gzip")
    if "/immutable/" in file_path:
        # SvelteKit hashes these names, so a change is a new URL.
        handler.send_header("Cache-Control", "public, max-age=31536000, immutable")
    else:
        # The shell keeps its name across releases and names the hashed
        # bundles. A browser reusing a stored shell after an update loads
        # bundle URLs this image no longer has and gets a blank page, with
        # nothing running to notice. no-cache stores it but asks first.
        handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(sent)
