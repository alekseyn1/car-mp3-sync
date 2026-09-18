#!/bin/bash
# Daily driver for youtube.py. Safe to run from cron or Task Scheduler.
#
# Downloading is done by bin/yt-dlp_linux, a standalone binary, so the venv is
# only here for eyed3 (used to write the numbered title tag). moviepy and
# pytubefix are no longer needed -- see the header of youtube.py.
#
# Pass --dry-run through to see what would change without touching anything:
#   ./download.sh --dry-run

set -u

BASE=/volume2/Storage/Music/Youtube
VENV="$BASE/yt_venv"
PY="$VENV/bin/python3"
YTDLP="$BASE/bin/yt-dlp_linux"

# /tmp is tmpfs mounted noexec on this NAS. The yt-dlp binary unpacks itself
# before running, so without this it dies with
# "libz.so.1: failed to map segment from shared object".
export TMPDIR="$BASE/bin/tmp"
mkdir -p "$TMPDIR"

# Create the venv only if it is missing -- no reason to reinstall every night.
if [ ! -x "$PY" ]; then
    echo "Creating virtualenv at $VENV"
    python3 -m venv "$VENV"
fi

if ! "$PY" -c 'import eyed3' 2>/dev/null; then
    echo "Installing eyed3"
    "$PY" -m pip install --quiet eyed3
fi

# Fetch yt-dlp if it is not there yet.
if [ ! -x "$YTDLP" ]; then
    echo "Fetching yt-dlp"
    mkdir -p "$BASE/bin"
    curl -sL -o "$YTDLP" \
        https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux
    chmod +x "$YTDLP"
fi

# YouTube changes often enough that a stale yt-dlp is the most likely cause of
# a failed run. Non-fatal: if the update fails, carry on with what we have.
"$YTDLP" -U || echo "yt-dlp self-update failed, continuing with $("$YTDLP" --version)"

# -u because stdout is a pipe here, and block buffering makes a long run look
# hung for minutes at a time when you tail the log.
"$PY" -u "$BASE/youtube.py" "$@" 2>&1 | tee "$BASE/log.txt"

# tee always succeeds, so report the script's own exit status.
exit "${PIPESTATUS[0]}"
