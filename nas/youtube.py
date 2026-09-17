#!/usr/bin/python3
# coding=utf-8

# Downloads the playlists listed in playlists_to_download.txt as mp3 files,
# one folder per playlist, under Output/.
#
# Downloader is yt-dlp (bin/yt-dlp_linux, a standalone binary). pytubefix was
# dropped: the last version that installs on this NAS's Python 3.8 is 9.3.0,
# and it can no longer enumerate playlists -- playlist.title still resolved but
# playlist.videos returned an empty list, which is what silently broke this
# script. moviepy is gone too; yt-dlp does the mp3 conversion and cover art
# through ffmpeg in one step, so there is no longer an mp4 staging folder.
#
# Bugs fixed at the same time:
#  1. An empty playlist enumeration used to be indistinguishable from "the
#     playlist is now empty", and the cleanup pass then deleted every mp3 it
#     had a staged mp4 for. That fired on 2026-04-24. Now a failed or empty
#     enumeration skips the playlist without deleting anything, and real
#     removals are capped by a sanity gate (see MAX_REMOVAL_* below).
#  2. Sequence-number prefixes are part of the filename, so when a playlist was
#     re-ordered upstream the same track was re-downloaded under a new number
#     and the old copy was left behind -- 103 of 207 files in Garage Music were
#     duplicates. Tracks are now matched by video id and re-numbered by
#     renaming, so re-ordering costs nothing and leaves no debris.
#  3. The folder timestamp trick ran once after the loop, so only the last
#     playlist got stamped. It now runs per playlist, with one day between
#     folders so the car lists them in playlists_to_download.txt order.
#
# Tracks are identified by YouTube video id, recorded in state/<playlist>.json.
# Titles change upstream and videos go private; neither should cost you a file
# you already have. Files whose video has become unavailable are kept as they
# are. Matching falls back to the sanitised title when an id is not on record,
# which is how the pre-existing library gets adopted on the first run.
#
# Nothing that took a download to produce is ever deleted. A track that leaves
# a playlist is moved to 'Removed tracks/<playlist>/' and comes back out of
# there if it is ever added again. The only files removed outright are
# redundant second copies of a track that is being kept anyway.
#
# Run with --dry-run to see what would happen without touching anything.

from __future__ import print_function

import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import eyed3
except ImportError:
    eyed3 = None


# ================= Settings ================================

# location of the script
PARENT_DIR = '/volume2/Storage/Music/Youtube'

# playlists text file that will be in the same folder as above
PLAYLISTS_FILE = 'playlists_to_download.txt'
PLAYLISTS_PATH = os.path.join(PARENT_DIR, PLAYLISTS_FILE)

# Directory with subfolders. One per playlist will be created.
OUTPUT_DIR = 'Output'
OUTPUT_FLD = os.path.join(PARENT_DIR, OUTPUT_DIR)

# video id -> filename, one json per playlist. Kept out of Output/ so the
# folders that get copied to the car USB stay pure mp3.
STATE_DIR = os.path.join(PARENT_DIR, 'state')

# Tracks that have left a playlist are moved here rather than deleted. One
# flat folder for all playlists, sitting inside Output/ so it travels to the
# car alongside them. It is only created once something is actually archived,
# and it is deliberately left unstamped: the playlist folders are dated from
# 2000 onwards, so this one always sorts last in the car.
# Sequence prefixes are dropped on the way in, since the number was a position
# in a playlist the track is no longer part of. If a track is added back to the
# playlist later it is moved out of here instead of being downloaded again.
REMOVED_DIR = os.path.join(OUTPUT_FLD, 'Removed tracks')

# Do you want to use sequence numbers for resulting mp3 files
# (001 - Song.mp3 as an example)
USE_SEQ_NUM = True

# yt-dlp standalone binary and its scratch space.
# TMPDIR matters: /tmp on this NAS is tmpfs mounted noexec, and the binary
# unpacks itself before running, so it fails there with
# "libz.so.1: failed to map segment from shared object".
YTDLP = os.path.join(PARENT_DIR, 'bin', 'yt-dlp_linux')
YTDLP_TMP = os.path.join(PARENT_DIR, 'bin', 'tmp')

# SynoCommunity ffmpeg. The stock /usr/bin/ffmpeg is a cut-down build.
FFMPEG_DIR = '/var/packages/ffmpeg/target/bin'

# Prefer the m4a/AAC stream, which is what the old script asked for by itag
# 140. yt-dlp's default 'bestaudio' picks Opus in webm instead. Measured on one
# hour-long mix the difference was modest -- 62 MB webm against 58 MB m4a -- so
# this is about matching the old pipeline, not about saving bandwidth. Falls
# back to bestaudio where no m4a exists.
# It does not make a run faster either: the LAME encode dominates and costs the
# same whatever the source format was. The setting that actually controls
# output size is AUDIO_QUALITY below.
AUDIO_SOURCE = 'bestaudio[ext=m4a]/bestaudio'

# The source is ~128 kbps AAC, so asking for a higher mp3 bitrate than that
# only produces larger files -- there is no quality left to recover.
AUDIO_QUALITY = '128K'

# The mp3 player in the car lists folders by date, oldest first. Each playlist
# folder is stamped one day after the previous one, so the order in
# playlists_to_download.txt is the order they appear in the car.
FOLDER_STAMP_START = datetime.datetime(2000, 1, 1, 0, 0, 0)

# Safety gate. A track that has vanished from the playlist is archived, but
# only if the number to archive stays under both limits -- a partial or
# throttled enumeration should never be able to empty the car's folder, even
# though the move itself is reversible. Redundant duplicate copies are not
# counted here; collapsing those is always safe.
MAX_REMOVAL_FRACTION = 0.10
MAX_REMOVAL_ABS = 5

DRY_RUN = '--dry-run' in sys.argv

# ================= End of Settings ================================


def sanitize_me(old_name):
    """Strip everything that is not Cyrillic, Latin, digits, space . ( ) -"""
    clean_name = re.sub(r"[^А-Яа-яЁёA-Za-z0-9 .\(\)-]+", "", old_name)
    return clean_name.strip()


def check_folder(path):
    if not os.path.exists(path):
        os.makedirs(path)
        print("New directory created: " + path)


def title_of(filename):
    """'003 - Song.mp3' -> 'Song'. Also accepts an unnumbered 'Song.mp3'."""
    stem = os.path.splitext(filename)[0]
    match = re.match(r'^\d{3} - (.*)$', stem)
    return match.group(1) if match else stem


def free_archive_path(folder, title):
    """Where to park a removed track, without overwriting an earlier copy."""
    candidate = os.path.join(folder, title + '.mp3')
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, '%s (%d).mp3' % (title, suffix))
        suffix += 1
    return candidate


def ytdlp_env():
    env = os.environ.copy()
    env['TMPDIR'] = YTDLP_TMP
    return env


def load_index(path):
    try:
        with open(path, 'r') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (IOError, ValueError):
        return {}


def save_index(path, index):
    check_folder(os.path.dirname(path))
    tmp = path + '.tmp'
    with open(tmp, 'w') as handle:
        json.dump(index, handle, indent=1, sort_keys=True, ensure_ascii=False)
    os.rename(tmp, path)


def fetch_playlist(url):
    """Return (playlist_title, [(video_id, raw_title), ...], [unavailable_ids]).

    Returns None if the playlist could not be read. None means "no idea what is
    in this playlist" and must never be treated as "the playlist is empty".
    """
    cmd = [YTDLP, '--flat-playlist', '--dump-single-json',
           '--ignore-no-formats-error', '--no-warnings', url]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=ytdlp_env())
        out, err = proc.communicate()
    except OSError as error:
        print("=!=!=!=!=> Could not run yt-dlp:", error)
        return None

    if proc.returncode != 0:
        print("=!=!=!=!=> yt-dlp exited %d" % proc.returncode)
        print(err.decode('utf-8', 'replace').strip()[:2000])
        return None

    try:
        data = json.loads(out.decode('utf-8', 'replace'))
    except ValueError as error:
        print("=!=!=!=!=> Could not parse yt-dlp output:", error)
        return None

    entries = []
    unavailable = []
    for entry in data.get('entries') or []:
        if not entry:
            continue
        vid = entry.get('id')
        title = entry.get('title')
        if not vid:
            continue
        # Deleted and private videos come back as placeholders with no usable
        # title. They cannot be downloaded or matched by name, but a copy we
        # already hold is still perfectly good, so remember the id.
        if not title or title in ('[Deleted video]', '[Private video]'):
            unavailable.append(vid)
            continue
        entries.append((vid, title))

    return (data.get('title') or 'Unknown playlist', entries, unavailable)


def set_title_tag(path, title, position=None, total=None):
    """Write the title, and the track number the car stereo sorts on.

    The numeric prefix in the filename is not enough: head units that sort by
    ID3 rather than by FAT directory order see 200 files all claiming no track
    number, and play them in whatever order they were indexed.
    """
    if eyed3 is None:
        return
    try:
        audiofile = eyed3.load(path)
        if audiofile is None:
            return
        if audiofile.tag is None:
            audiofile.initTag()
        audiofile.tag.title = title
        if position is not None:
            audiofile.tag.track_num = (position, total)
        audiofile.tag.save()
    except Exception as error:
        print("     (could not set title tag: %s)" % error)


def download_track(vid, dest_path):
    """Download one video as mp3 with cover art. Returns True on success."""
    workdir = os.path.join(YTDLP_TMP, 'dl-%d' % os.getpid())
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    try:
        cmd = [YTDLP,
               '--no-playlist',
               '-f', AUDIO_SOURCE,
               '--extract-audio',
               '--audio-format', 'mp3',
               '--audio-quality', AUDIO_QUALITY,
               '--embed-thumbnail',
               '--embed-metadata',
               '--ffmpeg-location', FFMPEG_DIR,
               '--no-progress',
               '--no-warnings',
               '--retries', '3',
               '-o', os.path.join(workdir, 'track.%(ext)s'),
               'https://www.youtube.com/watch?v=' + vid]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=ytdlp_env())
        out, _ = proc.communicate()
        if proc.returncode != 0:
            print("=!=!=!=!=> download failed (exit %d)" % proc.returncode)
            print(out.decode('utf-8', 'replace').strip()[-1500:])
            return False

        produced = glob.glob(os.path.join(workdir, '*.mp3'))
        if not produced:
            print("=!=!=!=!=> yt-dlp reported success but produced no mp3")
            return False

        shutil.move(produced[0], dest_path)
        return True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def process_playlist(url, folder_stamp):
    """Returns a (downloaded, renamed, removed, failed) tuple."""
    result = fetch_playlist(url)
    if result is None:
        print("")
        print("!!! Could not read this playlist. Skipping it entirely -- no")
        print("!!! files added, renamed or deleted.")
        return (0, 0, 0, 0)

    playlist_title, entries, unavailable = result
    print("Playlist title: " + playlist_title)
    print("Videos in playlist: %d available, %d deleted or private"
          % (len(entries), len(unavailable)))
    print("")

    if not entries:
        print("!!! The playlist enumerated zero usable videos. Skipping it")
        print("!!! entirely rather than deleting the folder's contents.")
        return (0, 0, 0, 0)

    folder_name = sanitize_me(playlist_title)
    save_dir = os.path.join(OUTPUT_FLD, folder_name)
    check_folder(save_dir)
    index_path = os.path.join(STATE_DIR, folder_name + '.json')
    index = load_index(index_path)

    # What the folder should contain, in playlist order.
    wanted = []
    used_names = set()
    for position, (vid, raw_title) in enumerate(entries, start=1):
        clean_name = sanitize_me(raw_title) or vid
        # Two different videos can sanitise to the same name; keep them apart.
        if clean_name in used_names:
            clean_name = '%s (%s)' % (clean_name, vid)
        used_names.add(clean_name)
        prefix = str(position).zfill(3) + ' - ' if USE_SEQ_NUM else ''
        wanted.append((vid, clean_name, prefix + clean_name + '.mp3',
                       prefix + clean_name))

    on_disk = sorted(name for name in os.listdir(save_dir)
                     if name.lower().endswith('.mp3')
                     and os.path.isfile(os.path.join(save_dir, name)))
    by_title = {}
    for name in on_disk:
        by_title.setdefault(title_of(name), []).append(name)

    # Work out which file on disk belongs to each wanted track: by recorded
    # video id first, then by sanitised title for anything downloaded before
    # the index existed.
    claimed = set()
    new_index = {}
    plan = []          # (vid, current_name_or_None, final_name, tag_title)

    for vid, clean_name, filename, tag_title in wanted:
        current = None
        candidate = index.get(vid)
        if candidate and candidate in on_disk and candidate not in claimed:
            current = candidate
        else:
            for name in by_title.get(clean_name, []):
                if name not in claimed:
                    current = name
                    break
        if current:
            claimed.add(current)
            new_index[vid] = filename
        plan.append((vid, clean_name, current, filename, tag_title))

    # A video that went private or was deleted keeps whatever we already have,
    # under its existing name.
    protected = []
    for vid in unavailable:
        name = index.get(vid)
        if name and name in on_disk and name not in claimed:
            claimed.add(name)
            new_index[vid] = name
            protected.append(name)

    # Split what is left over into redundant copies and genuinely stale tracks.
    # Collapsing duplicates is safe either way -- they are byte-for-byte the
    # same track under two sequence numbers -- so it is not subject to the gate,
    # even when the title has left the playlist entirely.
    duplicates = []
    stale = []
    kept_titles = set()
    for name in on_disk:
        if name in claimed:
            continue
        title = title_of(name)
        if title in used_names or title in kept_titles:
            duplicates.append(name)
        else:
            kept_titles.add(title)
            stale.append(name)

    if protected:
        print("Keeping %d track(s) whose video is no longer available:"
              % len(protected))
        for name in protected:
            print("    " + name)
        print("")

    allowed = max(MAX_REMOVAL_ABS, int(len(wanted) * MAX_REMOVAL_FRACTION))
    archive_stale = len(stale) <= allowed
    if stale:
        print("%d track(s) are no longer in the playlist:" % len(stale))
        for name in stale:
            print("    " + name)
        if archive_stale:
            print("They are being moved to %s" % REMOVED_DIR)
        else:
            print("!!! That is more than the safety limit of %d, so they are"
                  % allowed)
            print("!!! being left in place. If the playlist really did shrink,")
            print("!!! move them by hand or raise MAX_REMOVAL_FRACTION.")
        print("")

    downloaded = restored = renamed = archived = failed = 0

    # Clearing out first, so the names these occupy are free to be reused by
    # the renumbering below.
    for name in duplicates:
        # A second copy of a track we are keeping anyway. Nothing to preserve.
        print('Removing duplicate copy: ' + name)
        if not DRY_RUN:
            try:
                os.remove(os.path.join(save_dir, name))
            except OSError as error:
                print("     (could not remove: %s)" % error)
        continue

    if archive_stale and stale:
        if not DRY_RUN:
            check_folder(REMOVED_DIR)
        for name in stale:
            target = free_archive_path(REMOVED_DIR, title_of(name))
            print('Archiving: %s -> %s' % (name, os.path.basename(target)))
            if not DRY_RUN:
                try:
                    shutil.move(os.path.join(save_dir, name), target)
                except (OSError, IOError) as error:
                    print("     (could not archive: %s)" % error)
                    continue
            archived += 1

    # Renumbering in two phases. Going straight to the final name would clobber
    # whichever track currently holds it, since a re-order is a permutation.
    moves = [(current, final) for _, _, current, final, _ in plan
             if current and current != final]
    if moves:
        for position, (current, final) in enumerate(moves):
            print('Renumbering: %s -> %s' % (current, final))
            renamed += 1
            if not DRY_RUN:
                os.rename(os.path.join(save_dir, current),
                          os.path.join(save_dir, '.reindex-%d' % position))
        if not DRY_RUN:
            for position, (_, final) in enumerate(moves):
                os.rename(os.path.join(save_dir, '.reindex-%d' % position),
                          os.path.join(save_dir, final))

    for count, (vid, clean_name, current, filename, tag_title) in \
            enumerate(plan, start=1):
        dest = os.path.join(save_dir, filename)
        if current:
            if current != filename and not DRY_RUN:
                set_title_tag(dest, tag_title, count, len(plan))
            continue

        # A track that was archived earlier and has since been put back in the
        # playlist comes out of the archive rather than off YouTube again.
        parked = os.path.join(REMOVED_DIR, clean_name + '.mp3')
        if os.path.exists(parked):
            print('=====> (%d/%d) Restoring from archive: %s'
                  % (count, len(plan), filename))
            if not DRY_RUN:
                try:
                    shutil.move(parked, dest)
                    set_title_tag(dest, tag_title, count, len(plan))
                    new_index[vid] = filename
                except (OSError, IOError) as error:
                    print("     (could not restore: %s)" % error)
                    failed += 1
                    continue
            restored += 1
            continue

        print('=====> (%d/%d) Downloading: %s' % (count, len(plan), filename))
        if DRY_RUN:
            downloaded += 1
            continue
        if download_track(vid, dest):
            set_title_tag(dest, tag_title, count, len(plan))
            new_index[vid] = filename
            downloaded += 1
        else:
            failed += 1

    if not DRY_RUN:
        save_index(index_path, new_index)
        # Stamp the folder so the car lists playlists in the order they appear
        # in playlists_to_download.txt. This has to happen per playlist -- it
        # used to run once, after the loop, so only the last one got stamped.
        mod_time = time.mktime(folder_stamp.timetuple())
        os.utime(save_dir, (mod_time, mod_time))

    return (downloaded, restored, renamed, archived, failed)


def main():
    if not os.path.exists(YTDLP):
        print("yt-dlp is missing at " + YTDLP)
        print("Install it with:")
        print("  mkdir -p %s" % os.path.dirname(YTDLP))
        print("  curl -sL -o %s \\" % YTDLP)
        print("    https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux")
        print("  chmod +x %s" % YTDLP)
        return 1

    check_folder(OUTPUT_FLD)
    check_folder(YTDLP_TMP)
    check_folder(STATE_DIR)

    if DRY_RUN:
        print("*** DRY RUN - nothing will be downloaded, renamed or deleted ***")
        print("")

    with open(PLAYLISTS_PATH, 'r') as handle:
        urls = [line.strip() for line in handle if line.strip()]

    totals = [0, 0, 0, 0, 0]
    for pcount, url in enumerate(urls, start=1):
        print("==============================================================")
        print("Processing Playlist {}: {}".format(pcount, url))
        print("")
        stamp = FOLDER_STAMP_START + datetime.timedelta(days=pcount - 1)
        counts = process_playlist(url, stamp)
        totals = [a + b for a, b in zip(totals, counts)]
        print("")

    print("==============================================================")
    print("Downloaded %d, restored %d, renumbered %d, archived %d, failed %d"
          % tuple(totals))
    return 1 if totals[4] else 0


if __name__ == '__main__':
    sys.exit(main())
