# Spotify Playlist Tools

Exports **your own** Spotify playlists to a local CSV, with the artist and
track title for every song.

The target playlist is found by matching a keyword against the playlists in
your personal library (the ones you own or that were shared with you), so it
works for private playlists and never picks up a stranger's public playlist.

## Setup

1. **Create a Spotify app** at <https://developer.spotify.com/dashboard>:
   - Click *Create app*, give it any name/description.
   - Add a **Redirect URI**: `http://127.0.0.1:8888/callback` (must match exactly).
   - Open *Settings* and copy the **Client ID** and **Client Secret**.

2. **Save your credentials**:

   ```bash
   cp .env.example .env
   $EDITOR .env      # paste your client id and secret
   ```

   The `.env` file is gitignored. You can instead export the two variables
   directly: `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.

3. **Install dependencies**:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install spotipy pytest
   ```

   This machine's system Python is PEP-668 "externally managed", so packages
   must go in the project virtualenv. `run.sh` creates the venv and installs
   the dependencies automatically if they are missing.

## Usage

Authorize once — this opens Chrome, you approve the app, and the token is
cached in `.cache/spotipy_token.json`:

```bash
./run.sh --auth
```

Then export the playlist:

```bash
./run.sh gavin
```

`./run.sh` wraps the virtualenv for you. The script also works when called
directly with the system Python (`python3 export_playlist.py gavin`) — it
detects the missing dependency and hands off to `.venv` automatically.

Useful flags:

| Flag | Purpose |
| --- | --- |
| `gavin` | Keyword to match in your playlist names (default: `gavin`). |
| `--list` | List all playlists in your library; export nothing. |
| `-o, --output PATH` | Output CSV path (default: `gavin_playlist.csv`). |
| `--all-matches` | Export every matching playlist, one CSV each. |
| `--manual-auth` | Authorize by pasting the redirect URL back in (headless/SSH). |
| `--cache PATH` | Location of the cached OAuth token. |

`authenticate.py` (via `./run.sh --auth`) is the recommended way to authorize:
it runs a local callback server and opens Chrome automatically. Use
`./run.sh gavin --manual-auth` if no browser is reachable.

If several playlists match the keyword, the script lists them and stops, so
you can pick the right one rather than silently exporting the wrong playlist.

## Output

`gavin_playlist.csv`:

```csv
position,artist,title,album,track_uri
1,Artist Name,Track Title,Album Name,spotify:track:...
```

Multi-artist tracks are comma-joined in the `artist` column. Local files
(uploads) are skipped, since they have no Spotify URI.

## Notes

- The OAuth scopes requested are `playlist-read-private`,
  `playlist-read-collaborative`, `user-read-private` and `user-read-email`.
  These are read-only and do not allow modifying your playlists.
- Playlists shared with you by other people also appear in your library and
  are therefore matchable; the `owner` column in the `--list` output shows
  who owns each one.
- The Spotify Web API no longer returns playlist items for playlists that a
  user has marked as inaccessible, so such entries may appear empty.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The tests mock the Spotify API, so they need no credentials and no network.
