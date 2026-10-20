#!/usr/bin/env python3
"""Authorize the Spotify client once, using a local browser redirect.

Starts a tiny HTTP server on 127.0.0.1:8888 that captures the OAuth
redirect, opens Chrome to the Spotify authorize page, exchanges the returned
code for a token, and stores it in the token cache.

Usage:
    python authenticate.py
    python authenticate.py --no-browser    # print the URL instead
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VENV_DIR = Path(__file__).resolve().parent / ".venv"


def _ensure_dependencies() -> None:
    """Re-exec into the project virtualenv if spotipy isn't importable."""
    try:
        import spotipy  # noqa: F401
    except ImportError:
        pass
    else:
        return

    venv_python = VENV_DIR / "bin" / "python"
    if not venv_python.exists():
        raise SystemExit(
            "spotipy is not installed and no virtualenv was found at "
            f"{VENV_DIR}.\nCreate it with:\n"
            f"  python3 -m venv {VENV_DIR}\n"
            f"  {VENV_DIR}/bin/pip install spotipy"
        )

    if os.environ.get("_SPOTIFY_TOOLS_REEXEC") == "1":
        raise SystemExit(
            "spotipy is still missing inside the virtualenv. Run:\n"
            f"  {VENV_DIR}/bin/pip install spotipy"
        )

    os.environ["_SPOTIFY_TOOLS_REEXEC"] = "1"
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


_ensure_dependencies()

from spotify_playlist_tools import (  # noqa: E402
    SPOTIFY_CACHE,
    build_oauth_manager,
    get_credentials,
)

SUCCESS_PAGE = """<!doctype html>
<html><head><title>Spotify authorized</title>
<style>body{font-family:sans-serif;text-align:center;padding:4rem;background:#1ed760;color:#000}</style>
</head><body><h1>Authorization complete</h1>
<p>You can close this tab and return to the terminal.</p></body></html>"""


class CallbackHandler(BaseHTTPRequestHandler):
    """Captures the OAuth redirect query string."""

    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        query = parse_qs(urlparse(self.path).query)
        code = (query.get("code") or [None])[0]
        error = (query.get("error") or [None])[0]

        if code:
            CallbackHandler.result = {"code": code}
            body = SUCCESS_PAGE.encode()
            self.send_response(200)
        else:
            CallbackHandler.result = {"error": error or "missing_code"}
            body = b"<h1>Authorization failed</h1>"
            self.send_response(400)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize the Spotify client.")
    parser.add_argument("--port", type=int, default=8888, help="Local callback port.")
    parser.add_argument("--cache", type=Path, default=SPOTIFY_CACHE)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorize URL instead of opening a browser.",
    )
    args = parser.parse_args(argv)

    try:
        get_credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    auth_manager = build_oauth_manager(cache_path=args.cache, redirect_port=args.port)

    if auth_manager.cache_handler.get_cached_token():
        print("Already authorized. Token found in the cache.")
        print(f"  {args.cache}")
        return 0

    auth_url = auth_manager.get_authorize_url()

    server = HTTPServer(("127.0.0.1", args.port), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening Chrome to authorize...")
    print(f"  {auth_url}\n")
    if not args.no_browser:
        opened = False
        try:
            opened = webbrowser.open(auth_url, new=2)
        except Exception:
            opened = False
        if not opened:
            print("Could not open a browser automatically — open the URL above manually.")

    print("Waiting for the redirect (Ctrl+C to cancel)...")
    try:
        thread.join(timeout=300)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        server.server_close()
        return 1
    finally:
        server.server_close()

    result = CallbackHandler.result
    if "code" not in result:
        print(
            f"error: authorization failed ({result.get('error', 'no response')})",
            file=sys.stderr,
        )
        return 1

    auth_manager.get_access_token(result["code"])
    print("\nAuthorization successful.")
    print(f"Token cached in {args.cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
