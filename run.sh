#!/usr/bin/env bash
# Convenience launcher: always runs the script with the project virtualenv.
#
#   ./run.sh              -> export the "gavin" playlist
#   ./run.sh --list       -> list your playlists
#   ./run.sh gavin        -> export playlists matching "gavin"
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Creating virtualenv..." >&2
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install --quiet spotipy pytest
fi

if ! "$PY" -c "import spotipy" 2>/dev/null; then
  echo "Installing dependencies..." >&2
  "$DIR/.venv/bin/pip" install --quiet spotipy pytest
fi

# "./run.sh --auth" runs the interactive Spotify authorization helper.
if [[ "${1:-}" == "--auth" ]]; then
  shift
  exec "$PY" "$DIR/authenticate.py" "$@"
fi

exec "$PY" "$DIR/export_playlist.py" "$@"
