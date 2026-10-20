#!/usr/bin/env python3
"""Export the signed-in user's Spotify playlist matching a keyword to CSV.

Only playlists in the authenticated user's own library are considered, so
the target is always a personal (possibly private) playlist rather than a
stranger's public one.

Usage:
    python export_playlist.py gavin
    python export_playlist.py gavin --output my.csv
    python export_playlist.py --list          # show your playlists, export nothing
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

VENV_DIR = Path(__file__).resolve().parent / ".venv"


def _ensure_dependencies() -> None:
    """Re-exec into the project virtualenv if spotipy isn't importable.

    The system interpreter on this machine is PEP-668 "externally managed",
    so dependencies live in the local .venv. If someone runs this script with
    a plain `python3`, transparently hand over to .venv/bin/python instead of
    failing with a confusing ImportError.
    """
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
            f"{VENV_DIR}.\nCreate it and install the dependencies:\n"
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
    DEFAULT_OUTPUT,
    SPOTIFY_CACHE,
    authorize_manually,
    build_spotify_client,
    describe_playlist,
    find_playlists_by_name,
    iter_playlist_tracks,
    iter_user_playlists,
    write_csv,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a personal Spotify playlist to CSV.",
    )
    parser.add_argument(
        "keyword",
        nargs="?",
        default="gavin",
        help="Substring to match in your playlist names (default: gavin).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List your playlists and exit without exporting.",
    )
    parser.add_argument(
        "--all-matches",
        action="store_true",
        help="Export every matching playlist (one CSV per playlist) instead of the first.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=SPOTIFY_CACHE,
        help="Path to the cached OAuth token.",
    )
    parser.add_argument(
        "--manual-auth",
        action="store_true",
        help=(
            "Authorize by pasting the redirect URL back in, instead of relying "
            "on a local browser (use this over SSH / headless)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        if args.manual_auth:
            authorize_manually(cache_path=args.cache)
        sp = build_spotify_client(cache_path=args.cache)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        playlists = list(iter_user_playlists(sp))
    except Exception as exc:  # network/auth errors
        print(f"error: could not load your playlists: {exc}", file=sys.stderr)
        return 1

    if args.list:
        print(f"Found {len(playlists)} playlist(s) in your library:\n")
        for playlist in playlists:
            print("  " + describe_playlist(playlist))
        return 0

    matches = find_playlists_by_name(playlists, args.keyword)
    if not matches:
        print(
            f"No playlist in your library has '{args.keyword}' in its name.",
            file=sys.stderr,
        )
        print("\nYour playlists:", file=sys.stderr)
        for playlist in playlists:
            print("  " + describe_playlist(playlist), file=sys.stderr)
        return 1

    if len(matches) > 1 and not args.all_matches:
        print(
            f"{len(matches)} playlists match '{args.keyword}':",
            file=sys.stderr,
        )
        for playlist in matches:
            print("  " + describe_playlist(playlist), file=sys.stderr)
        print(
            "\nRe-run with --all-matches to export all of them, "
            "or pick one by exact name.",
            file=sys.stderr,
        )
        return 1

    selected = matches if args.all_matches else matches[:1]
    exit_code = 0
    for playlist in selected:
        name = playlist.get("name") or "playlist"
        playlist_id = playlist.get("id")
        print(f"Exporting: {describe_playlist(playlist)}")

        try:
            tracks = list(iter_playlist_tracks(sp, playlist_id))
        except Exception as exc:
            print(f"error: could not read tracks for {name!r}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        output = args.output
        if args.all_matches and len(selected) > 1:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            output = output.with_name(f"{output.stem}_{safe}{output.suffix}")

        write_csv(tracks, output)
        print(f"  -> {len(tracks)} tracks written to {output}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
