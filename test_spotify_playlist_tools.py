"""Tests for spotify_playlist_tools (no network access required)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from spotify_playlist_tools import (
    Track,
    authorize_manually,
    extract_track,
    find_playlists_by_name,
    iter_playlist_tracks,
    iter_user_playlists,
    load_dotenv,
    write_csv,
)


def make_item(name, artists=("Artist",), is_local=False, uri="spotify:track:x"):
    return {
        "track": {
            "name": name,
            "type": "track",
            "is_local": is_local,
            "uri": uri,
            "artists": [{"name": a} for a in artists],
            "album": {"name": "Some Album"},
        }
    }


def make_new_item(name, artists=("Artist",), is_local=False, uri="spotify:track:x"):
    """Playlist item in the current (2025+) API shape, where the track lives
    under the ``item`` key instead of ``track``."""
    return {
        "added_at": "2026-01-01T00:00:00Z",
        "item": {
            "name": name,
            "type": "track",
            "is_local": is_local,
            "uri": uri,
            "artists": [{"name": a} for a in artists],
            "album": {"name": "Some Album"},
        },
    }


class FakeSpotify:
    """Minimal stand-in for spotipy.Spotify with the calls we make."""

    def __init__(self, playlist_pages, item_pages):
        self._playlist_pages = playlist_pages
        self._item_pages = item_pages
        self.playlist_item_calls = []

    def current_user_playlists(self, limit=50, offset=0):
        return self._playlist_pages[offset // limit]

    def playlist_items(self, playlist_id, limit=100, offset=0, additional_types=None):
        self.playlist_item_calls.append(playlist_id)
        return self._item_pages[offset // limit]


def test_find_playlists_by_name_is_case_insensitive():
    playlists = [
        {"name": "Gavin's Mixtape"},
        {"name": "Road Trip"},
        {"name": "my GAVIN favourites"},
    ]
    matches = find_playlists_by_name(playlists, "gavin")
    assert [p["name"] for p in matches] == [
        "Gavin's Mixtape",
        "my GAVIN favourites",
    ]


def test_find_playlists_by_name_no_match():
    assert find_playlists_by_name([{"name": "Road Trip"}], "gavin") == []


def test_iter_user_playlists_paginates():
    page1 = {"items": [{"id": f"p{i}"} for i in range(50)], "next": "url"}
    page2 = {"items": [{"id": "p50"}], "next": None}
    sp = FakeSpotify([page1, page2], [])

    ids = [p["id"] for p in iter_user_playlists(sp)]
    assert len(ids) == 51
    assert ids[-1] == "p50"


def test_extract_track_reads_artists_and_title():
    track = extract_track(make_item("Song", ("A", "B")), 3)
    assert track is not None
    assert track.artist == "A, B"
    assert track.title == "Song"
    assert track.album == "Some Album"
    assert track.position == 3


def test_extract_track_skips_local_tracks():
    assert extract_track(make_item("Local", is_local=True), 1) is None


def test_extract_track_handles_missing_track():
    assert extract_track({}, 1) is None
    assert extract_track({"track": {"type": "episode"}}, 1) is None


def test_extract_track_reads_new_item_key():
    """Spotify now nests the track under ``item`` rather than ``track``."""
    track = extract_track(make_new_item("Ember", ("Dance Gavin Dance",)), 1)
    assert track is not None
    assert track.title == "Ember"
    assert track.artist == "Dance Gavin Dance"
    assert track.album == "Some Album"


def test_extract_track_prefers_item_key_over_track_key():
    item = make_new_item("New", ("A",))
    item["track"] = {"name": "Old", "type": "track", "artists": [], "uri": "x"}
    track = extract_track(item, 1)
    assert track is not None
    assert track.title == "New"


def test_extract_track_skips_local_tracks_in_new_shape():
    assert extract_track(make_new_item("Local", is_local=True), 1) is None


def test_iter_playlist_tracks_handles_new_item_shape():
    page = {
        "items": [make_new_item(f"T{i}") for i in range(3)],
        "next": None,
    }
    sp = FakeSpotify([], [page])

    tracks = list(iter_playlist_tracks(sp, "playlist-id"))
    assert [t.title for t in tracks] == ["T0", "T1", "T2"]


def test_iter_playlist_tracks_paginates_and_skips_local():
    page1 = {"items": [make_item(f"T{i}") for i in range(100)], "next": "url"}
    page2 = {"items": [make_item("T100"), make_item("L", is_local=True)], "next": None}
    sp = FakeSpotify([], [page1, page2])

    tracks = list(iter_playlist_tracks(sp, "playlist-id"))
    assert len(tracks) == 101
    assert tracks[-1].title == "T100"
    assert sp.playlist_item_calls == ["playlist-id", "playlist-id"]


def test_write_csv_outputs_headers_and_rows(tmp_path: Path):
    tracks = [
        Track(position=1, artists=["A"], title="One", album="Al", uri="spotify:track:1"),
        Track(position=2, artists=["B", "C"], title="Two", album="Al2", uri="spotify:track:2"),
    ]
    out = write_csv(tracks, tmp_path / "out.csv")

    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["artist"] == "A"
    assert rows[1]["artist"] == "B, C"
    assert rows[1]["title"] == "Two"
    assert len(rows) == 2


def test_load_dotenv_reads_values_without_overriding_env(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nSPOTIFY_CLIENT_ID=abc123\nSPOTIFY_CLIENT_SECRET='sec'\nBAD_LINE\n"
    )
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)

    values = load_dotenv(env_file)
    assert values["SPOTIFY_CLIENT_ID"] == "abc123"
    assert values["SPOTIFY_CLIENT_SECRET"] == "sec"
    assert "BAD_LINE" not in values


def test_authorize_manually_extracts_code_and_caches_token(tmp_path, monkeypatch):
    """The pasted redirect URL must yield an auth code and store the token."""
    captured = {}

    class FakeAuthManager:
        cache_handler = type(
            "Handler", (), {"get_cached_token": staticmethod(lambda: None)}
        )()

        def get_authorize_url(self):
            return "https://accounts.spotify.com/authorize?client_id=fake"

        def parse_response_code(self, url):
            captured["url"] = url
            return "AUTH_CODE_123"

        def get_access_token(self, code):
            captured["code"] = code
            return {"access_token": "tok"}

    monkeypatch.setattr(
        "spotify_playlist_tools.build_oauth_manager", lambda **kw: FakeAuthManager()
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "http://127.0.0.1:8888/callback?code=AUTH_CODE_123")

    code = authorize_manually(cache_path=tmp_path / "token.json")

    assert code == "AUTH_CODE_123"
    assert captured["code"] == "AUTH_CODE_123"
    assert "AUTH_CODE_123" in captured["url"]


def test_authorize_manually_rejects_url_without_code(tmp_path, monkeypatch):
    class FakeAuthManager:
        cache_handler = type(
            "Handler", (), {"get_cached_token": staticmethod(lambda: None)}
        )()

        def get_authorize_url(self):
            return "https://accounts.spotify.com/authorize"

        def parse_response_code(self, url):
            return None

    monkeypatch.setattr(
        "spotify_playlist_tools.build_oauth_manager", lambda **kw: FakeAuthManager()
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "http://127.0.0.1:8888/callback?error=denied")

    with pytest.raises(RuntimeError, match="authorization code"):
        authorize_manually(cache_path=tmp_path / "token.json")


# --------------------------------------------------------------------------
# End-to-end CLI test (Spotify API is mocked)
# --------------------------------------------------------------------------
def test_cli_exports_matching_playlist_to_csv(tmp_path, monkeypatch):
    import export_playlist

    playlist_pages = [
        {
            "items": [
                {
                    "id": "pl_gavin",
                    "name": "Gavin's Picks",
                    "owner": {"display_name": "Josh", "id": "josh"},
                    "total_tracks": 2,
                    "public": False,
                },
                {
                    "id": "pl_other",
                    "name": "Chill Vibes",
                    "owner": {"display_name": "Josh", "id": "josh"},
                    "total_tracks": 1,
                    "public": True,
                },
            ],
            "next": None,
        }
    ]
    item_pages = [
        {
            "items": [
                make_item("Song A", ("Artist A",)),
                make_item("Song B", ("Artist B", "Guest")),
            ],
            "next": None,
        }
    ]
    sp = FakeSpotify(playlist_pages, item_pages)
    monkeypatch.setattr(export_playlist, "build_spotify_client", lambda **kw: sp)

    out = tmp_path / "out.csv"
    exit_code = export_playlist.main(["gavin", "--output", str(out)])

    assert exit_code == 0
    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert rows[0]["artist"] == "Artist A"
    assert rows[0]["title"] == "Song A"
    assert rows[1]["artist"] == "Artist B, Guest"


def test_cli_lists_user_playlists_without_exporting(tmp_path, monkeypatch, capsys):
    import export_playlist

    playlist_pages = [
        {
            "items": [
                {
                    "id": "pl_gavin",
                    "name": "Gavin's Picks",
                    "owner": {"display_name": "Josh", "id": "josh"},
                    "total_tracks": 2,
                    "public": False,
                }
            ],
            "next": None,
        }
    ]
    sp = FakeSpotify(playlist_pages, [])
    monkeypatch.setattr(export_playlist, "build_spotify_client", lambda **kw: sp)

    out = tmp_path / "out.csv"
    exit_code = export_playlist.main(["--list", "--output", str(out)])

    assert exit_code == 0
    assert "Gavin's Picks" in capsys.readouterr().out
    assert not out.exists()


def test_cli_errors_when_no_playlist_matches(tmp_path, monkeypatch, capsys):
    import export_playlist

    playlist_pages = [
        {
            "items": [
                {
                    "id": "pl_other",
                    "name": "Chill Vibes",
                    "owner": {"display_name": "Josh", "id": "josh"},
                    "total_tracks": 1,
                    "public": True,
                }
            ],
            "next": None,
        }
    ]
    sp = FakeSpotify(playlist_pages, [])
    monkeypatch.setattr(export_playlist, "build_spotify_client", lambda **kw: sp)

    exit_code = export_playlist.main(["gavin", "--output", str(tmp_path / "out.csv")])

    assert exit_code == 1
    assert "No playlist" in capsys.readouterr().err
