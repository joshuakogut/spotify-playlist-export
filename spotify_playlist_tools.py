"""Utilities for exporting a personal Spotify playlist to CSV.

Targets playlists owned by (or shared with) the authenticated user, so the
script uses the OAuth Authorization Code flow with private-playlist scopes
rather than client-credentials or public playlist search.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = REPO_ROOT / ".env"
SPOTIFY_CACHE = REPO_ROOT / ".cache" / "spotipy_token.json"
DEFAULT_OUTPUT = REPO_ROOT / "gavin_playlist.csv"

# Scopes required to read the signed-in user's private playlists.
SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-read-private",
    "user-read-email",
)

DEFAULT_COLUMNS = ("position", "artist", "title", "album", "track_uri")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def load_dotenv(path: Path = CREDENTIALS_FILE) -> dict[str, str]:
    """Read a minimal ``.env`` file into a dict without overriding os.environ."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        values[key] = value
        os.environ.setdefault(key, value)

    return values


def get_credentials(env: dict[str, str] | None = None) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from the environment or ``.env``."""
    env = env if env is not None else load_dotenv()
    client_id = env.get("SPOTIFY_CLIENT_ID") or os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = (
        env.get("SPOTIFY_CLIENT_SECRET") or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    )

    missing = [
        name
        for name, value in (
            ("SPOTIFY_CLIENT_ID", client_id),
            ("SPOTIFY_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing Spotify credentials: "
            + ", ".join(missing)
            + f". Add them to {CREDENTIALS_FILE} (see .env.example) or export them."
        )

    return client_id, client_secret



# --------------------------------------------------------------------------
# Spotify session
# --------------------------------------------------------------------------
def build_oauth_manager(
    cache_path: Path = SPOTIFY_CACHE,
    redirect_port: int = 8888,
) -> "spotipy.oauth2.SpotifyOAuth":
    """Create the OAuth manager used for the Authorization Code flow."""
    from spotipy.oauth2 import SpotifyOAuth

    client_id, client_secret = get_credentials()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=f"http://127.0.0.1:{redirect_port}/callback",
        scope=" ".join(SCOPES),
        cache_path=str(cache_path),
    )


def build_spotify_client(
    cache_path: Path = SPOTIFY_CACHE,
    redirect_port: int = 8888,
) -> "spotipy.Spotify":
    """Create an authorized Spotify client scoped to the user's own playlists.

    The first run performs the Authorization Code flow (opening a browser
    window); the resulting token is cached in ``cache_path`` for later runs.
    """
    import spotipy

    return spotipy.Spotify(
        auth_manager=build_oauth_manager(
            cache_path=cache_path, redirect_port=redirect_port
        )
    )


def authorize_manually(cache_path: Path = SPOTIFY_CACHE, redirect_port: int = 8888) -> str:
    """Run the OAuth flow manually by pasting the redirected URL back in.

    Useful over SSH or in a headless environment where no browser is available.
    Returns the authorization code extracted from the pasted URL.
    """
    auth_manager = build_oauth_manager(
        cache_path=cache_path, redirect_port=redirect_port
    )

    if auth_manager.cache_handler.get_cached_token():
        return ""

    auth_url = auth_manager.get_authorize_url()
    print("Open this URL in a browser and sign in:\n")
    print(f"  {auth_url}\n")
    print(
        "Your browser will redirect to a localhost URL that will fail to load.\n"
        "Copy the FULL address bar URL and paste it below."
    )

    try:
        pasted = input("Pasted URL: ").strip()
    except EOFError:
        pasted = ""
    if not pasted:
        raise RuntimeError("No URL provided.")

    code = auth_manager.parse_response_code(pasted)
    if not code:
        raise RuntimeError(
            "Could not find an authorization code in that URL. "
            "Make sure you pasted the full redirect URL."
        )

    auth_manager.get_access_token(code)
    return code


def iter_user_playlists(sp: "spotipy.Spotify") -> Iterator[dict[str, Any]]:
    """Yield every playlist in the signed-in user's own library."""
    offset = 0
    limit = 50
    while True:
        page = sp.current_user_playlists(limit=limit, offset=offset)
        items = page.get("items") or []
        if not items:
            return
        yield from items
        offset += len(items)
        if not page.get("next") or len(items) < limit:
            return


def find_playlists_by_name(
    playlists: Iterable[dict[str, Any]], keyword: str
) -> list[dict[str, Any]]:
    """Return playlists whose name contains ``keyword`` (case-insensitive)."""
    needle = keyword.casefold()
    matches = []
    for playlist in playlists:
        name = playlist.get("name") or ""
        if needle in name.casefold():
            matches.append(playlist)
    return matches


def describe_playlist(playlist: dict[str, Any]) -> str:
    owner = (playlist.get("owner") or {}).get("display_name") or (
        (playlist.get("owner") or {}).get("id") or "?"
    )
    return (
        f"{playlist.get('name')} | id={playlist.get('id')} | owner={owner} "
        f"| tracks={playlist.get('total_tracks')} | public={playlist.get('public')}"
    )


# --------------------------------------------------------------------------
# Track extraction
# --------------------------------------------------------------------------
@dataclass
class Track:
    position: int
    artists: list[str] = field(default_factory=list)
    title: str = ""
    album: str = ""
    uri: str = ""

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)


def extract_track(item: dict[str, Any], position: int) -> Track | None:
    """Convert a playlist item into a :class:`Track`, or ``None`` if not a track.

    Spotify's playlist-items endpoint returns the playable object under the
    ``item`` key (this replaced the older ``track`` key in 2025). Both shapes
    are handled here so the script works against either API version.
    """
    track = item.get("item")
    if not isinstance(track, dict):
        track = item.get("track")
    if not isinstance(track, dict):
        return None

    if track.get("type") != "track" or track.get("is_local"):
        return None

    artists = [
        artist.get("name", "")
        for artist in (track.get("artists") or [])
        if artist.get("name")
    ]
    album = ((track.get("album") or {}).get("name")) or ""

    return Track(
        position=position,
        artists=artists,
        title=track.get("name") or "",
        album=album,
        uri=track.get("uri") or "",
    )


def iter_playlist_tracks(sp: "spotipy.Spotify", playlist_id: str) -> Iterator[Track]:
    """Yield :class:`Track` objects for a playlist, following pagination."""
    offset = 0
    limit = 100
    position = 0
    while True:
        page = sp.playlist_items(
            playlist_id,
            limit=limit,
            offset=offset,
            additional_types="track",
        )
        items = page.get("items") or []
        if not items:
            return
        for item in items:
            position += 1
            track = extract_track(item, position)
            if track is not None:
                yield track
        offset += len(items)
        if not page.get("next") or len(items) < limit:
            return



# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------
def write_csv(
    tracks: Sequence[Track],
    output_path: Path = DEFAULT_OUTPUT,
    columns: Sequence[str] = DEFAULT_COLUMNS,
) -> Path:
    """Write ``tracks`` to ``output_path`` as CSV and return the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for track in tracks:
            writer.writerow(
                {
                    "position": track.position,
                    "artist": track.artist,
                    "title": track.title,
                    "album": track.album,
                    "track_uri": track.uri,
                }
            )
    return output_path
