"""
DVD Server - Backend (MKV version)
Streams MKV files ripped from DVDs with chapter data, subtitle selection, and audio track selection.

Supports nested library layout:
    dvds/<Genre>/<DVD Folder>/*.mkv
    dvds/<DVD Folder>/*.mkv          (no genre -> "Uncategorized")
    dvds/<Genre>/<Sub>/<DVD>/*.mkv   (deeper nesting also works)

Also supports PNG+WebVTT image-cue subtitles produced by ripper.py's
--backend png-webvtt mode:
    <stem>.<lang>[.t<id>].vobsub.vtt          (WebVTT, cues point to PNGs)
    <stem>.<lang>[.t<id>].vobsub.images/*.png (one PNG per cue)

Image-based VTTs are served with their relative PNG paths rewritten to
absolute API URLs so the browser can resolve them regardless of the VTT
response's own URL. A dedicated /api/dvd/subtitle-image/... route serves
the individual PNGs, sandboxed to the title's own folder.
"""

from flask import Flask, jsonify, send_file, Response, request
from flask_cors import CORS
import os
import re
import json
import shutil
import threading
import subprocess
from pathlib import Path
from urllib.parse import quote
import logging

app = Flask(__name__)
CORS(app)

# Resolve every file path relative to this script, not the CWD
BASE_DIR = Path(__file__).parent.resolve()

# ---------- Logging ----------
_ACCESS_LINE_RE = re.compile(r'"\S+\s+\S+\s+HTTP/[\d.]+"\s+(\d{3})')


class _AccessLogFilter(logging.Filter):
    def filter(self, record):
        if record.name != "werkzeug":
            return True
        m = _ACCESS_LINE_RE.search(record.getMessage())
        if m:
            code = int(m.group(1))
            if 200 <= code < 300 or code == 304:
                return False
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("werkzeug").addFilter(_AccessLogFilter())

log = logging.getLogger("dvdserver")


# ---------- Configuration ----------
DVD_FOLDER = BASE_DIR / "../dvds"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".vob"}

FFPROBE_CANDIDATES = [
    "ffprobe",
    r"D:\programs\ffmpeg\bin\ffprobe.exe",
    r"C:\ffmpeg\bin\ffprobe.exe",
    r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
]
FFMPEG_CANDIDATES = [
    "ffmpeg",
    r"D:\programs\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]
EXTRA_SEARCH_ROOTS = [r"D:\programs", r"C:\Program Files", r"C:\Program Files (x86)"]

TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac"}
# Minimum valid cache file size (bytes) - helps detect corrupted/incomplete files
MIN_CACHE_FILE_SIZE = 1024 * 1024  # 1 MB

# Folder names we never descend into during a library scan
SKIP_DIR_NAMES = {
    ".remux", ".subtitles", ".subtitle_work",
    ".git", ".svn", "__pycache__", "node_modules",
    "$RECYCLE.BIN", "System Volume Information",
}

# How deep the scanner will walk.  dvds/Genre/DVD  = depth 2
MAX_SCAN_DEPTH = 6

# Windows reserved device names - never allow these as a path component
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# -----------------------------------

def _client_ip():
    """Real client IP, honouring X-Forwarded-For when behind a proxy."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------- Path safety ----------

def _is_safe_path_component(part: str) -> bool:
    """A single path segment. Rejects traversal, drive letters, ADS, reserved names."""
    if not part or part in (".", ".."):
        return False
    if "\x00" in part or ":" in part:
        return False
    # Windows reserved device names (CON, PRN, ..., COM1, LPT1, ...)
    if part.split(".")[0].upper() in _WINDOWS_RESERVED:
        return False
    return True


def _safe_dvd_path(rel_name: str):
    """
    Resolve a client-supplied relative DVD name against DVD_FOLDER.
    Returns an absolute Path only if it stays strictly inside DVD_FOLDER.
    Returns None on ANY suspicious input (traversal, absolute path, symlink escape).
    """
    if not rel_name or not isinstance(rel_name, str):
        return None

    # Normalize backslashes so a Windows-style input can't smuggle a "..".
    rel_name = rel_name.replace("\\", "/").strip("/")
    if not rel_name:
        return None

    parts = rel_name.split("/")
    if any(not _is_safe_path_component(p) for p in parts):
        log.warning(f"Rejected unsafe path component: {rel_name!r}")
        return None

    root = DVD_FOLDER.resolve()
    try:
        candidate = (root / rel_name).resolve()
    except (OSError, RuntimeError) as e:
        log.warning(f"Path resolve failed for {rel_name!r}: {e}")
        return None

    # Final gate: candidate must still be inside root (this catches symlink escapes).
    try:
        candidate.relative_to(root)
    except ValueError:
        log.warning(f"Path escapes DVD_FOLDER: {rel_name!r} -> {candidate}")
        return None

    return candidate


# ---------- Cache validation helpers ----------

def _is_cache_valid(cache_file: Path) -> bool:
    if not cache_file.exists():
        return False
    size = cache_file.stat().st_size
    if size < MIN_CACHE_FILE_SIZE:
        log.warning(f"Cache file too small ({size} bytes): {cache_file}")
        return False
    return True


def _delete_cache_file(cache_file: Path) -> bool:
    try:
        if cache_file.exists():
            cache_file.unlink()
            log.info(f"Deleted incomplete cache: {cache_file.name}")
            return True
    except PermissionError as e:
        log.warning(f"Delete blocked (file in use): {cache_file.name} — {e}")
    except Exception as e:
        log.warning(f"Failed to delete cache file {cache_file}: {e}")
    return False


current_dvd = {
    "title": None,
    "dvd_path": None,
    "titles": [],
    "current_title": 0,
}


# ---------- Tool discovery ----------

def _find_tool(candidates, names, roots):
    for p in candidates:
        if os.path.isabs(p) and os.path.exists(p):
            return p
        found = shutil.which(p)
        if found:
            return found
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                if dirpath.count(os.sep) - root.count(os.sep) > 3:
                    dirnames[:] = []
                    continue
                for fname in names:
                    if fname in filenames:
                        return os.path.join(dirpath, fname)
        except Exception:
            pass
    return None


FFPROBE = _find_tool(FFPROBE_CANDIDATES, ["ffprobe.exe", "ffprobe"], EXTRA_SEARCH_ROOTS)
FFMPEG = _find_tool(FFMPEG_CANDIDATES, ["ffmpeg.exe", "ffmpeg"], EXTRA_SEARCH_ROOTS)


# ---------- Cache locking + remux ----------

_cache_locks = {}
_cache_locks_mutex = threading.Lock()


def _get_cache_lock(cache_file: Path) -> threading.Lock:
    key = str(cache_file.resolve())
    with _cache_locks_mutex:
        if key not in _cache_locks:
            _cache_locks[key] = threading.Lock()
        return _cache_locks[key]


def _remux_to_cache(path: Path, cache_file: Path, audio_idx: int,
                    passthrough: bool) -> bool:
    if _is_cache_valid(cache_file):
        return True
    if cache_file.exists():
        _delete_cache_file(cache_file)

    cmd = [FFMPEG, "-y", "-i", str(path),
           "-map", "0:v:0", "-map", f"0:a:{audio_idx}", "-c:v", "copy"]
    if passthrough:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-threads", "0"]
    cmd += [str(cache_file)]

    try:
        subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        log.warning(f"  Remux timed out: {cache_file.name}")
        _delete_cache_file(cache_file)
        return False
    except Exception as e:
        log.warning(f"  Remux failed: {e}")
        _delete_cache_file(cache_file)
        return False

    if not _is_cache_valid(cache_file):
        _delete_cache_file(cache_file)
        return False
    return True


def _cache_one_track(title, title_idx, audio_idx):
    """Background: remux a single alternate audio track. Serialized per file."""
    if not FFMPEG:
        return
    path = Path(title["path"])
    tracks = title.get("audio", [])
    if audio_idx <= 0 or audio_idx >= len(tracks):
        return

    track = tracks[audio_idx]
    passthrough = (track.get("codec") or "").lower() in (
        "aac", "mp3", "opus", "vorbis", "flac"
    )
    suffix = "copy" if passthrough else "aac"

    cache_dir = path.parent / ".remux"
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{path.stem}.a{audio_idx}.{suffix}.mp4"

    lock = _get_cache_lock(cache_file)
    if not lock.acquire(blocking=False):
        log.info(f"  Cache-track: '{path.name}' a{audio_idx} already in progress")
        return
    try:
        log.info(f"  Cache-track: remuxing '{path.name}' audio {audio_idx}...")
        if _remux_to_cache(path, cache_file, audio_idx, passthrough):
            mb = cache_file.stat().st_size // (1024 * 1024)
            log.info(f"  Cache-track: '{path.name}' a{audio_idx} ready ({mb} MB)")
        else:
            log.warning(f"  Cache-track: '{path.name}' a{audio_idx} failed")
    finally:
        lock.release()


# ---------- Library discovery ----------

def _scan_dvd_folders(root: Path):
    """
    Recursively find every folder under `root` that directly contains at least
    one video file.  Does NOT follow symlinks and never descends into dotted
    or skip-listed directories.

    Returns a list of (relative_name, absolute_path) tuples.  relative_name
    always uses "/" as separator, e.g. "Action/Hyperdrive(2006) disc_1".
    """
    root = Path(root).resolve()
    found = []
    if not root.is_dir():
        return found

    def walk(dir_path: Path, rel_parts, depth):
        if depth > MAX_SCAN_DEPTH:
            return
        try:
            entries = list(os.scandir(dir_path))
        except (PermissionError, OSError) as e:
            log.debug(f"Scan: cannot read {dir_path}: {e}")
            return

        has_video = False
        subdirs = []
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in SKIP_DIR_NAMES:
                continue
            try:
                if entry.is_symlink():
                    # Never follow symlinks: this is the #1 escape vector.
                    continue
                if entry.is_file(follow_symlinks=False):
                    if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                        has_video = True
                elif entry.is_dir(follow_symlinks=False):
                    subdirs.append(name)
            except OSError:
                continue

        # A top-level dir that only contains videos is not a "DVD" — the
        # library contract is that DVDs are folders, not bare files.
        if has_video and rel_parts:
            found.append(("/".join(rel_parts), dir_path))

        for sub in sorted(subdirs):
            walk(dir_path / sub, rel_parts + [sub], depth + 1)

    walk(root, [], 0)
    return found


def get_library():
    """
    Return the full library structure.

    Shape:
    {
      "dvds":   [ <flat list of every DVD> ],
      "genres": [ {"name": ..., "label": ..., "dvds": [...]}, ... ]
    }

    Each DVD entry:
      name          "Action/Hyperdrive(2006) disc_1"   (relative path, / separators)
      display_name  "Hyperdrive(2006) disc_1"
      genre         "Action"   ("" if the DVD sits directly under dvds/)
      subpath       ""         (any intermediate dirs between genre and DVD)
      path          absolute filesystem path
      cover         "/api/dvd/cover/Action/Hyperdrive(2006)%20disc_1" or null
    """
    DVD_FOLDER.mkdir(exist_ok=True)
    root = DVD_FOLDER.resolve()

    dvds = []
    for rel_name, abs_path in _scan_dvd_folders(root):
        parts = rel_name.split("/")
        genre = parts[0] if len(parts) > 1 else ""
        display_name = parts[-1]
        subpath = "/".join(parts[1:-1]) if len(parts) > 2 else ""
        cover = abs_path / "cover.png"
        # quote() with default safe="/" preserves path separators but
        # URL-encodes spaces, parens, etc.
        cover_url = f"/api/dvd/cover/{quote(rel_name)}" if cover.is_file() else None
        dvds.append({
            "name": rel_name,
            "display_name": display_name,
            "genre": genre,
            "subpath": subpath,
            "path": str(abs_path),
            "cover": cover_url,
        })

    # Group by genre, keeping the (already-sorted) scan order.
    by_genre = {}
    for dvd in dvds:
        by_genre.setdefault(dvd["genre"], []).append(dvd)

    genres = []
    if "" in by_genre:
        genres.append({"name": "", "label": "Uncategorized", "dvds": by_genre.pop("")})
    for g in sorted(by_genre.keys(), key=str.lower):
        genres.append({"name": g, "label": g, "dvds": by_genre[g]})

    return {"dvds": dvds, "genres": genres}


# ---------- Probing ----------

def _guess_lang_from_filename(name):
    """Extract a language code from a sidecar subtitle filename.

    Handles:
      <stem>.<lang>.<ext>
      <stem>.<lang>.<forced|sdh|hi>.<ext>
      <stem>.<lang>[.t<id>].vobsub.vtt      (our PNG+WebVTT convention)
    """
    lower = name.lower()
    m = re.search(r"\.([a-z]{2,3})(?:\.(?:forced|sdh|hi))?\.(?:srt|vtt)$", lower)
    if m:
        return m.group(1)
    m = re.search(r"\.([a-z]{2,3})(?:\.t\d+)?\.vobsub\.vtt$", lower)
    if m:
        return m.group(1)
    return "und"


def _probe_subtitles(mkv_path, streams):
    tracks = []
    mkv_path = Path(mkv_path)

    sub_n = 0
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        codec = (s.get("codec_name") or "unknown").lower()
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "und").lower()
        title = tags.get("title") or ""
        playable = codec in TEXT_SUB_CODECS
        if not title:
            title = f"Embedded #{sub_n + 1} · {codec}"
            if lang != "und":
                title = f"[{lang.upper()}] {title}"
        tracks.append({
            "id": f"embedded:{s.get('index')}",
            "source": "embedded",
            "stream_index": s.get("index"),
            "codec": codec,
            "language": lang,
            "title": title,
            "playable": playable,
            "image_based": False,
            "note": "" if playable else "Bitmap subtitle (VOBSUB/PGS) — not renderable in browser",
        })
        sub_n += 1

    for ext in (".vtt", ".srt"):
        for f in sorted(mkv_path.parent.glob(f"{mkv_path.stem}*{ext}")):
            if f.parent.name in (".subtitles", ".subtitle_work", ".remux"):
                continue

            # Our PNG+WebVTT output uses the ".vobsub.vtt" suffix. Detect it
            # so the frontend can switch to the image-cue renderer.
            is_vobsub_vtt = f.name.lower().endswith(".vobsub.vtt")

            # Also mark any VTT that contains image references (defensive:
            # users might rename files, or use a different ripper).
            if not is_vobsub_vtt and ext == ".vtt":
                try:
                    sample = f.read_text(encoding="utf-8", errors="replace")[:4096]
                    if (".png" in sample.lower()
                            and ("--> " in sample)
                            and ("<img" in sample.lower()
                                 or re.search(r'^\s*\S+\.png\s*$',
                                              sample, re.MULTILINE))):
                        is_vobsub_vtt = True
                except Exception:
                    pass

            tracks.append({
                "id": f"file:{f.name}",
                "source": "external",
                "filename": f.name,
                "codec": ext.lstrip("."),
                "language": _guess_lang_from_filename(f.name),
                "title": f.name,
                "playable": True,
                "image_based": is_vobsub_vtt,
                "note": ("Image-based cues (PNG) — rendered as overlay"
                         if is_vobsub_vtt else ""),
            })

    return tracks


def _probe_audio(streams):
    tracks = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "und").lower()
        title = tags.get("title") or ""
        codec = (s.get("codec_name") or "unknown").lower()
        channels = s.get("channels", 0)
        ch_str = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(channels, f"{channels}ch")

        if title:
            title = f"[{lang.upper()}] {title} ({ch_str})"
        else:
            title = f"[{lang.upper()}] {codec.upper()} {ch_str}"

        tracks.append({
            "index": len(tracks),
            "stream_index": s.get("index"),
            "codec": codec,
            "language": lang,
            "title": title,
            "channels": channels,
            "playable": codec in BROWSER_AUDIO_CODECS and channels <= 2,
        })
    return tracks


def probe_media(file_path):
    if not FFPROBE:
        log.error("ffprobe not found - cannot read media info")
        return {"duration": 0.0, "chapters": [], "subtitles": [], "audio": []}
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_chapters", "-show_streams", str(file_path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        data = json.loads(result.stdout or "{}")
    except Exception as e:
        log.error(f"ffprobe failed on {file_path}: {e}")
        return {"duration": 0.0, "chapters": [], "subtitles": [], "audio": []}

    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        pass

    chapters = []
    for i, ch in enumerate(data.get("chapters", []), start=1):
        try:
            start = float(ch.get("start_time", 0))
            end = float(ch.get("end_time", 0))
        except (TypeError, ValueError):
            continue
        title = (ch.get("tags") or {}).get("title") or f"Chapter {i:02d}"
        chapters.append({
            "number": i,
            "title": title,
            "start": start,
            "end": end,
        })

    if not chapters and duration > 0:
        chapters = [{"number": 1, "title": "Full Title", "start": 0.0, "end": duration}]

    streams = data.get("streams", [])
    return {
        "duration": duration,
        "chapters": chapters,
        "subtitles": _probe_subtitles(file_path, streams),
        "audio": _probe_audio(streams),
    }


def get_titles(dvd_folder):
    titles = []
    for f in sorted(Path(dvd_folder).iterdir()):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        info = probe_media(f)
        titles.append({
            "file": f.name,
            "path": str(f),
            "duration": info["duration"],
            "size": f.stat().st_size,
            "chapters": info["chapters"],
            "subtitles": info["subtitles"],
            "audio": info["audio"],
        })
    titles.sort(key=lambda t: t["duration"], reverse=True)
    return titles


# ---------- SRT -> WebVTT ----------

def srt_to_vtt(srt_text):
    if srt_text and srt_text[0] == "\ufeff":
        srt_text = srt_text[1:]
    srt_text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    if not srt_text.endswith("\n"):
        srt_text += "\n"
    body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_text)
    return "WEBVTT\n\n" + body


# ---------- Image-based WebVTT helpers ----------

# Matches a bare PNG path on its own line, or a PNG path inside an <img src="">.
# We only rewrite *relative* paths; absolute URLs, root-relative paths, and
# data: URIs are left alone.
_BARE_PNG_LINE_RE = re.compile(r'^[^\r\n]*?\.png[^\r\n]*$',
                               re.MULTILINE | re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'<img\s+src=(["\'])([^"\']+)\1',
                         re.IGNORECASE)


def _rewrite_vtt_image_paths(vtt_text: str, title_idx: int) -> str:
    """
    Rewrite relative PNG references inside a WebVTT file into absolute
    /api/dvd/subtitle-image/<title_idx>/... URLs, so the browser can fetch
    them even though the VTT itself was served from
    /api/dvd/subtitle/<idx>/<sub_id>.

    Handles three cue payload forms produced by ripper.py:
      1. Plain relative path:   images/0001.png
      2. <img src="..."> wrapper
      3. data:image/png;base64,...   (left unchanged)
    """
    prefix = f"/api/dvd/subtitle-image/{title_idx}/"

    def _absolute(path: str) -> str:
        p = path.strip()
        if not p.lower().endswith(".png"):
            return p
        if p.startswith(("/", "http://", "https://", "data:")):
            return p
        return prefix + quote(p, safe="/")

    # 1. <img src="..."> wrappers
    def _img_repl(m):
        q, p = m.group(1), m.group(2)
        return f'<img src={q}{_absolute(p)}{q}'

    vtt_text = _IMG_SRC_RE.sub(_img_repl, vtt_text)

    # 2. Bare PNG path on its own line
    def _line_repl(m):
        return _absolute(m.group(0))

    vtt_text = _BARE_PNG_LINE_RE.sub(_line_repl, vtt_text)

    return vtt_text


# ---------- Routes ----------

@app.route("/api/dvds", methods=["GET"])
def list_dvds():
    """Full library: flat DVD list + genre grouping."""
    return jsonify(get_library())


@app.route("/api/dvd/cover/<path:dvd_name>", methods=["GET"])
def get_dvd_cover(dvd_name):
    """Return cover.png from a DVD folder. dvd_name may contain slashes."""
    dvd_path = _safe_dvd_path(dvd_name)
    if dvd_path is None:
        return jsonify({"error": "Invalid DVD path"}), 400
    if not dvd_path.is_dir():
        return jsonify({"error": "DVD folder not found"}), 404
    cover = dvd_path / "cover.png"
    if not cover.is_file():
        return jsonify({"error": "Cover not found"}), 404
    return send_file(cover, mimetype="image/png", conditional=True)


@app.route("/api/dvd/load/<path:dvd_name>", methods=["POST"])
def load_dvd(dvd_name):
    dvd_path = _safe_dvd_path(dvd_name)
    if dvd_path is None or not dvd_path.is_dir():
        log.warning(f"Load failed: invalid or missing folder {dvd_name!r}")
        return jsonify({"error": "DVD folder not found"}), 404

    titles = get_titles(dvd_path)
    if not titles:
        log.warning(f"Load failed: no video files in '{dvd_name}'")
        return jsonify({"error": "No playable video files found"}), 500

    current_dvd["title"] = dvd_name
    current_dvd["dvd_path"] = str(dvd_path)
    current_dvd["titles"] = titles
    current_dvd["current_title"] = 0

    total_gb = sum(t.get("size", 0) for t in titles) / (1024 ** 3)
    log.info(f"Loaded DVD '{dvd_name}': {len(titles)} title(s), {total_gb:.2f} GB")
    return jsonify({"title": dvd_name, "titles": titles})


@app.route("/api/dvd/prewarm/<int:title_idx>", methods=["POST"])
def prewarm_title(title_idx):
    return jsonify({"started": False, "reason": "prewarm disabled; cache on play only"})


@app.route("/api/dvd/cache-track/<int:title_idx>/<int:audio_idx>", methods=["POST"])
def cache_track(title_idx, audio_idx):
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    tracks = title.get("audio", [])
    if audio_idx <= 0 or audio_idx >= len(tracks):
        return jsonify({"started": False, "reason": "default or invalid track"})
    log.info(f"cache_track: title={title_idx} audio={audio_idx} "
             f"tracks={len(tracks)} dvd={current_dvd.get('title')!r}")
    if not FFMPEG:
        return jsonify({"error": "ffmpeg not available"}), 500

    threading.Thread(
        target=_cache_one_track,
        args=(title, title_idx, audio_idx),
        daemon=True,
    ).start()
    return jsonify({"started": True, "audio_idx": audio_idx})


@app.route("/api/dvd/cache-status/<int:title_idx>/<int:audio_idx>", methods=["GET"])
def cache_status(title_idx, audio_idx):
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    tracks = title.get("audio", [])
    if audio_idx < 0 or audio_idx >= len(tracks):
        return jsonify({"ready": False, "reason": "invalid track"})

    if audio_idx == 0:
        return jsonify({"ready": True, "reason": "default track"})

    path = Path(title["path"])
    src_codec = (tracks[audio_idx].get("codec") or "").lower()
    passthrough = src_codec in ("aac", "mp3", "opus", "vorbis", "flac")
    suffix = "copy" if passthrough else "aac"
    cache_file = path.parent / ".remux" / f"{path.stem}.a{audio_idx}.{suffix}.mp4"

    return jsonify({"ready": _is_cache_valid(cache_file), "suffix": suffix})


@app.route("/api/dvd/current", methods=["GET"])
def get_current_dvd():
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    return jsonify({
        "title": current_dvd["title"],
        "titles": current_dvd["titles"],
        "current_title": current_dvd["current_title"],
    })


@app.route("/api/dvd/cache/info", methods=["GET"])
def cache_info():
    """Report cache contents for every DVD, recursively discovered."""
    root = Path(DVD_FOLDER).resolve()
    if not root.exists():
        return jsonify({"total_files": 0, "total_bytes": 0, "total_mb": 0, "dvds": []})

    dvds = []
    total_files = 0
    total_bytes = 0

    for rel_name, abs_path in _scan_dvd_folders(root):
        files = 0
        bytes_ = 0
        remux_bytes = 0
        sub_bytes = 0
        for sub, key in ((".remux", "remux"), (".subtitles", "subtitles")):
            d = abs_path / sub
            if not d.exists() or not d.is_dir():
                continue
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    sz = entry.stat().st_size
                except OSError:
                    continue
                files += 1
                bytes_ += sz
                if key == "remux":
                    remux_bytes += sz
                else:
                    sub_bytes += sz
        if files > 0:
            dvds.append({
                "name": rel_name,
                "files": files,
                "bytes": bytes_,
                "mb": bytes_ // (1024 * 1024),
                "remux_bytes": remux_bytes,
                "subtitles_bytes": sub_bytes,
            })
            total_files += files
            total_bytes += bytes_

    return jsonify({
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_mb": total_bytes // (1024 * 1024),
        "dvds": dvds,
    })


@app.route("/api/dvd/cache/clear", methods=["POST"])
def clear_cache():
    """Clear cache. ?dvd=NAME for one DVD (may contain slashes); otherwise all."""
    root = Path(DVD_FOLDER).resolve()
    if not root.exists():
        return jsonify({"removed": 0, "mb_freed": 0, "locked": [], "errors": []})

    target = request.args.get("dvd")
    if target:
        d = _safe_dvd_path(target)
        targets = [(target, d)] if d and d.is_dir() else []
    else:
        targets = _scan_dvd_folders(root)

    removed = 0
    bytes_freed = 0
    locked = []
    errors = []

    for rel_name, dvd_dir in targets:
        for sub in (".remux", ".subtitles"):
            d = dvd_dir / sub
            if not d.exists() or not d.is_dir():
                continue
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    sz = entry.stat().st_size
                    Path(entry.path).unlink()
                    removed += 1
                    bytes_freed += sz
                except PermissionError:
                    locked.append(f"{rel_name}/{entry.name}")
                except Exception as e:
                    errors.append(f"{rel_name}/{entry.name}: {e}")

    freed_mb = bytes_freed // (1024 * 1024)
    scope = f"'{target}'" if target else "all DVDs"
    log.info(f"Cache clear ({scope}): removed {removed} file(s), freed {freed_mb} MB"
             + (f", {len(locked)} locked" if locked else "")
             + (f", {len(errors)} error(s)" if errors else ""))

    response = {
        "removed": removed,
        "mb_freed": freed_mb,
        "locked": locked,
        "errors": errors,
    }

    if locked and removed == 0:
        return jsonify({
            **response,
            "error": "All cache files are locked",
            "hint": "Stop playback, then try again.",
        }), 409

    return jsonify(response)


@app.route("/api/dvd/title/<int:title_idx>", methods=["POST"])
def select_title(title_idx):
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        log.warning(f"Select failed: title index {title_idx} out of range")
        return jsonify({"error": "Invalid title index"}), 400

    current_dvd["current_title"] = title_idx
    t = current_dvd["titles"][title_idx]

    dur_min = t["duration"] / 60.0 if t["duration"] else 0
    log.info(f"Selected title {title_idx}: '{t['file']}' "
             f"({dur_min:.1f} min, {len(t.get('audio', []))} audio, "
             f"{len(t.get('subtitles', []))} sub) "
             f"[from {_client_ip()}]")

    return jsonify({
        "title_idx": title_idx,
        "file": t["file"],
        "duration": t["duration"],
        "chapters": t["chapters"],
        "subtitles": t.get("subtitles", []),
        "audio": t.get("audio", []),
        "stream_url": f"/api/dvd/stream/{title_idx}",
    })


@app.route("/api/dvd/stream/<int:title_idx>", methods=["GET"])
def stream_title(title_idx):
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    path = Path(title["path"])
    if not path.exists():
        log.warning(f"Stream failed: file missing {path}")
        return jsonify({"error": "File not found"}), 404

    try:
        audio_idx = int(request.args.get("audio", "0"))
    except (TypeError, ValueError):
        audio_idx = 0

    tracks = title.get("audio", [])
    if audio_idx < 0 or audio_idx >= max(1, len(tracks)):
        audio_idx = 0

    if audio_idx == 0:
        ext = path.suffix.lower()
        mimetype = {
            ".mkv": "video/x-matroska",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".vob": "video/mpeg",
        }.get(ext, "application/octet-stream")
        return send_file(path, mimetype=mimetype, conditional=True)

    if not FFMPEG:
        return jsonify({"error": "ffmpeg not found — cannot switch audio tracks"}), 500

    track = tracks[audio_idx] if audio_idx < len(tracks) else {}
    src_codec = (track.get("codec") or "").lower()
    audio_passthrough = src_codec in ("aac", "mp3", "opus", "vorbis", "flac")

    cache_dir = path.parent / ".remux"
    cache_dir.mkdir(exist_ok=True)
    suffix = "copy" if audio_passthrough else "aac"
    cache_file = cache_dir / f"{path.stem}.a{audio_idx}.{suffix}.mp4"

    lock = _get_cache_lock(cache_file)
    with lock:
        if _is_cache_valid(cache_file):
            log.info(f"Serving cached: {cache_file.name}")
            return send_file(cache_file, mimetype="video/mp4", conditional=True)
        log.info(f"Stream: remuxing title {title_idx} audio {audio_idx}...")
        if not _remux_to_cache(path, cache_file, audio_idx, audio_passthrough):
            return jsonify({"error": "Remux failed"}), 500

    return send_file(cache_file, mimetype="video/mp4", conditional=True)

@app.route("/api/dvd/vobsub-image/<int:title_idx>/<path:relpath>", methods=["GET"])
def get_vobsub_image(title_idx, relpath):
    """Serve a PNG from a title's <stem>.vobsub.images/ directory."""
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    mkv_path = Path(title["path"]).resolve()
    parent = mkv_path.parent

    relpath = relpath.replace("\\", "/").strip("/")
    if not relpath or ".." in relpath.split("/") or ":" in relpath:
        return jsonify({"error": "Invalid path"}), 400
    if not relpath.lower().endswith(".png"):
        return jsonify({"error": "Not a PNG"}), 400

    try:
        candidate = (parent / relpath).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "Invalid path"}), 400
    try:
        candidate.relative_to(parent)
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not candidate.is_file():
        return jsonify({"error": "Image not found"}), 404
    return send_file(candidate, mimetype="image/png")

@app.route("/api/dvd/subtitle-image/<int:title_idx>/<path:filename>", methods=["GET"])
def get_subtitle_image(title_idx, filename):
    """Serve a PNG/JPG image used as a bitmap-subtitle overlay."""
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    # Reject traversal attempts
    if (not filename or filename.startswith("/")
            or "\\" in filename
            or any(part in ("", ".", "..") for part in filename.split("/"))):
        return jsonify({"error": "Invalid filename"}), 400

    title = current_dvd["titles"][title_idx]
    mkv_path = Path(title["path"])
    base_dir = mkv_path.parent.resolve()

    allowed = {
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif":  "image/gif",
        ".bmp":  "image/bmp",
    }
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        return jsonify({"error": "Unsupported image type"}), 400

    try:
        target = (base_dir / filename).resolve()
        target.relative_to(base_dir)
    except (OSError, ValueError):
        return jsonify({"error": "Invalid path"}), 400

    if not target.is_file():
        log.warning(f"Subtitle image missing: {target}")
        return jsonify({"error": "Image not found"}), 404

    return send_file(target, mimetype=allowed[suffix], conditional=True)

@app.route("/api/dvd/subtitle/<int:title_idx>/<path:sub_id>", methods=["GET"])
def get_subtitle(title_idx, sub_id):
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    mkv_path = Path(title["path"])
    tracks = title.get("subtitles", [])
    track = next((t for t in tracks if t["id"] == sub_id), None)
    if not track:
        available = [t["id"] for t in tracks]
        log.warning(f"Subtitle 404: requested={sub_id!r} title={title_idx} "
                    f"available={available}")
        return jsonify({
            "error": "Subtitle not found",
            "requested": sub_id,
            "available": available,
        }), 404

    if track["source"] == "external":
        src = mkv_path.parent / track["filename"]
        if not src.exists():
            log.warning(f"Sidecar subtitle missing: {src}")
            return jsonify({"error": "Subtitle file missing"}), 404

        if src.suffix.lower() == ".vtt":
            # Image-based VTTs need their relative PNG paths rewritten to
            # absolute URLs, because the browser resolves them against this
            # response's URL, not against the file on disk.
            if track.get("image_based"):
                try:
                    text = src.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    log.error(f"Could not read {src}: {e}")
                    return jsonify({"error": f"Could not read subtitle: {e}"}), 500
                text = _rewrite_vtt_image_paths(text, title_idx)
                return Response(text, content_type="text/vtt; charset=utf-8")
            return send_file(src, mimetype="text/vtt")

        # .srt sidecar -> convert to VTT
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.error(f"Could not read {src}: {e}")
            return jsonify({"error": f"Could not read subtitle: {e}"}), 500
        return Response(srt_to_vtt(text), content_type="text/vtt; charset=utf-8")

    if not track.get("playable"):
        return jsonify({
            "error": (
                f"Subtitle codec '{track['codec']}' is bitmap-based and cannot "
                "be shown in a browser. Extract it with OCR first."
            )
        }), 415

    if not FFMPEG:
        return jsonify({"error": "ffmpeg not found — cannot extract embedded subtitles"}), 500

    cache_dir = mkv_path.parent / ".subtitles"
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{mkv_path.stem}.stream{track['stream_index']}.vtt"

    if not cache_file.exists() or cache_file.stat().st_size == 0:
        log.info(f"Extracting embedded subtitle stream {track['stream_index']} "
                 f"from '{mkv_path.name}'...")
        try:
            result = subprocess.run(
                [FFMPEG, "-y",
                 "-i", str(mkv_path),
                 "-map", f"0:{track['stream_index']}",
                 "-c:s", "webvtt",
                 str(cache_file)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120,
            )
        except subprocess.TimeoutExpired:
            log.error("Subtitle extraction timed out")
            return jsonify({"error": "Extraction timed out"}), 500
        except Exception as e:
            log.error(f"Subtitle extraction failed: {e}")
            return jsonify({"error": f"Extraction failed: {e}"}), 500

        if result.returncode != 0 or not cache_file.exists():
            log.error(f"Subtitle extraction failed (ffmpeg {result.returncode}): "
                      f"{result.stderr[-300:]}")
            return jsonify({
                "error": "Failed to extract subtitle stream",
                "detail": result.stderr[-300:],
            }), 500

    return send_file(cache_file, mimetype="text/vtt")


@app.route("/api/dvd/subtitle-image/<int:title_idx>/<path:rel_path>", methods=["GET"])
def get_subtitle_image(title_idx, rel_path):
    """
    Serve a single PNG that belongs to an image-based VTT subtitle track.

    rel_path is relative to the title's own folder (the MKV's parent). The
    resolution is sandboxed: the final path must stay inside that folder,
    must end in .png, and must not contain traversal segments.
    """
    if not current_dvd["dvd_path"]:
        return jsonify({"error": "No DVD loaded"}), 404
    if not (0 <= title_idx < len(current_dvd["titles"])):
        return jsonify({"error": "Invalid title index"}), 400

    title = current_dvd["titles"][title_idx]
    mkv_dir = Path(title["path"]).parent.resolve()

    # Reject anything obviously wrong before touching the filesystem.
    if not rel_path or ".." in rel_path.split("/") or rel_path.startswith(("/", "\\")):
        return jsonify({"error": "Invalid path"}), 400
    if not rel_path.lower().endswith(".png"):
        return jsonify({"error": "Not a PNG"}), 400

    try:
        candidate = (mkv_dir / rel_path).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "Invalid path"}), 400

    # Final sandbox check: the resolved path must still be inside mkv_dir.
    try:
        candidate.relative_to(mkv_dir)
    except ValueError:
        log.warning(f"Subtitle image escapes title folder: {rel_path!r}")
        return jsonify({"error": "Invalid path"}), 400

    if not candidate.is_file():
        log.warning(f"Subtitle image not found: {candidate}")
        return jsonify({"error": "Image not found"}), 404

    return send_file(candidate, mimetype="image/png", conditional=True)


@app.route("/<path:filename>", methods=["GET"])
def static_file(filename):
    allowed = {".css", ".js", ".html", ".ico", ".png", ".svg", ".woff2"}
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        return jsonify({"error": "Not found"}), 404
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        return jsonify({"error": "Not found"}), 404
    path = (BASE_DIR / filename).resolve()
    try:
        path.relative_to(BASE_DIR)
    except ValueError:
        return jsonify({"error": "Not found"}), 404
    if not path.is_file():
        return jsonify({"error": "Not found"}), 404
    mimetypes = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".html": "text/html",
        ".ico": "image/x-icon",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
    }
    return send_file(path, mimetype=mimetypes[suffix])


@app.route("/", methods=["GET"])
def index():
    return send_file(BASE_DIR / "index.html")


if __name__ == "__main__":
    os.makedirs(DVD_FOLDER, exist_ok=True)

    print("\n" + "=" * 60)
    print("  DVD MKV Server")
    print("=" * 60)
    print(f"\nBase dir:    {BASE_DIR}")
    print(f"DVD Folder:  {os.path.abspath(DVD_FOLDER)}")
    print(f"ffprobe:     {FFPROBE or 'NOT FOUND'}")
    print(f"ffmpeg:      {FFMPEG or 'NOT FOUND'}")
    print("\nOpen: http://localhost:4251")
    print("=" * 60 + "\n")

    from discovery import DiscoveryService

    discovery = DiscoveryService(port=4251, name="DVD MKV Server")
    discovery.start()

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=4251, threads=8)
    finally:
        discovery.stop()