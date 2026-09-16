# DVD Server

Self-hosted browser player for a ripped DVD library. Point a folder of MKVs at it,
open the web UI from any device on your LAN (phone, tablet, TV browser), and get:

- **Chapter navigation** — jump to any chapter, with the current one highlighted live
- **Subtitle selection** — embedded text tracks and sidecar `.srt` / `.vtt` files
- **Audio-track selection** — switch between languages/commentaries on the fly
- **Genre grouping** — DVDs organized under `dvds/<Genre>/<DVD>/`, collapsible in the sidebar
- **Cover art** — drops a `cover.png` per DVD folder into the pre-play screen
- **Theming** — 13 built-in themes, auto-discovered from CSS
- **Auto-discovery** — advertises itself over mDNS (`_dvds._tcp.local.`) so client apps can find it
- **On-demand remux cache** — audio-track switching transparently re-encodes and caches the alternate track

Originally written for a Windows setup, now runs identically on Linux/Armbian
(verified on an Odroid N2) and Windows.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Library layout](#library-layout)
3. [Installation](#installation)
4. [Running manually](#running-manually)
5. [Configuration](#configuration)
6. [Using the web UI](#using-the-web-ui)
7. [HTTP API](#http-api)
8. [Caching](#caching)
9. [Security model](#security-model)
10. [Troubleshooting](#troubleshooting)
11. [Project files](#project-files)

---

## Quick start

**Linux / Armbian / Raspberry Pi OS:**

    git clone <your-repo> ~/dvdserver      # or just copy the files in
    cd ~/dvdserver
    chmod +x install.sh
    sudo ./install.sh

**Windows:**

    # Open PowerShell as Administrator
    cd C:\path\to\dvdserver
    powershell -ExecutionPolicy Bypass -File .\install.ps1

Both installers create the venv, install dependencies, and register a startup
service. After they finish, the banner prints the LAN URL — open it.

Drop your ripped DVDs into `dvds/` and reload the page.

---

## Library layout

The server scans `dvds/` **recursively** and treats any folder that directly
contains at least one video file as a *DVD*. Genre is the first path segment.

    dvds/
    ├── Action/
    │   ├── Die Hard(1988) disc_1/
    │   │   ├── title_t00.mkv
    │   │   ├── title_t01.mkv
    │   │   ├── title_t00.en.srt        # sidecar subtitle (optional)
    │   │   └── cover.png               # cover art (optional)
    │   └── Mad Max(1979)/
    │       └── title_t00.mkv
    ├── Comedy/
    │   └── Airplane!(1980)/
    │       └── title_t00.mkv
    └── Loose DVD/                      # no genre → appears as "Uncategorized"
        └── title_t00.mkv

Rules:

- A **DVD** is a folder containing at least one file with a recognised video
  extension (`.mkv`, `.mp4`, `.webm`, `.vob`). Files placed directly in `dvds/`
  are ignored — the library contract is *DVDs are folders*.
- **Genre** = the first path segment. Folders placed directly under `dvds/`
  show under "Uncategorized". Deeper nesting works; subdirectories between the
  genre and the DVD name are treated as additional path components but don't
  affect grouping.
- Scan depth is capped at 6 levels.
- The scanner **never follows symlinks** (see [Security model](#security-model)).
  To bring in an external library, use a bind mount instead:

      sudo mount --bind /mnt/media/dvds ~/dvdserver/dvds

- Directories beginning with `.` and a small skip-list (`.remux`,
  `.subtitles`, `.git`, `node_modules`, `$RECYCLE.BIN`, `System Volume
  Information`, …) are ignored during the scan.

### Cover art

Place a file named exactly `cover.png` inside a DVD folder. The web UI shows it
on the pre-play overlay. If absent, a 📀 placeholder is shown.

### Subtitles

Two sources are supported:

1. **Embedded** — text-based subtitle streams inside the MKV (SubRip, ASS/SSA,
   WebVTT, `mov_text`). These are extracted on demand via ffmpeg.
2. **Sidecar** — a file next to the MKV named like `title_t00.en.srt` or
   `title_t00.en.vtt`. The two-letter language code is picked up and shown in
   the dropdown.

Bitmap subtitles (VOBSUB, PGS) are listed but flagged unplayable — they can't be
rendered in a `<video>` element without an OCR step first.

---

## Installation

### Linux (Debian / Ubuntu / Armbian / Raspberry Pi OS)

Requirements:

- Python 3.10 or newer
- `python3-venv`
- `ffmpeg` (optional but strongly recommended — required for audio-track
  switching and embedded subtitle extraction)
- `systemd` (for the service; skip with `--no-service`)

The installer:

1. Verifies Python, installs `python3-venv` and `ffmpeg` via apt if missing
2. Creates a venv at `<install-dir>/venv`
3. Installs packages from `requirements.txt`
4. Creates `dvds/`
5. Writes a hardened systemd unit to `/etc/systemd/system/dvdserver.service`
6. Starts the service and prints the LAN URL

Options:

    sudo ./install.sh                        # defaults
    sudo ./install.sh --port 8080            # different port
    sudo ./install.sh --user neonlightning   # run as this user (default: SUDO_USER)
    sudo ./install.sh --dir /opt/dvdserver   # different install dir
    sudo ./install.sh --no-service           # skip systemd
    sudo ./install.sh --no-apt               # skip apt install step

To remove:

    sudo systemctl disable --now dvdserver
    sudo rm /etc/systemd/system/dvdserver.service
    sudo systemctl daemon-reload
    rm -rf ~/dvdserver                       # careful — this holds your dvds/

### Windows

Requirements:

- Python 3.10 or newer, with "Add to PATH" ticked during install
  (<https://www.python.org/downloads/windows/>)
- `ffmpeg` on `PATH` (optional but recommended — see `EXTERNAL_PROGRAMS.md`)
- Administrator rights **only** if you want the auto-start task

The installer:

1. Verifies Python; finds `python` or the `py` launcher
2. Warns if `ffmpeg` isn't on `PATH`
3. Creates a venv at `<install-dir>\venv`
4. Installs packages from `requirements.txt`
5. Creates `dvds\`
6. Writes a `run.cmd` convenience launcher
7. Registers a Scheduled Task that starts the server at user logon

Options:

    powershell -ExecutionPolicy Bypass -File .\install.ps1                    # full install
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoService         # no task
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Port 8080
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -ServiceName DvdSrv

To remove:

    Unregister-ScheduledTask -TaskName DvdServer -Confirm:$false
    # then delete the install directory

### macOS

No installer is provided, but the steps are the same as Linux:

    brew install python ffmpeg
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
    ./venv/bin/python3 dvd_server_backend.py

Run it under `launchd`, `tmux`, or `nohup` if you want it to persist.

---

## Running manually

Skip the service management entirely and just launch:

**Linux / macOS:**

    cd ~/dvdserver
    ./venv/bin/python3 dvd_server_backend.py

**Windows:**

    cd C:\path\to\dvdserver
    .\run.cmd

The banner prints the resolved paths for ffmpeg and ffprobe:

    ============================================================
      DVD MKV Server
    ============================================================
    
    Base dir:    /home/neonlightning/dvdserver
    DVD Folder:  /home/neonlightning/dvdserver/dvds
    ffprobe:     /usr/bin/ffprobe
    ffmpeg:      /usr/bin/ffmpeg
    
    Open: http://localhost:4251
    ============================================================

**If either binary says `NOT FOUND`** the server still runs, but audio-track
switching and embedded-subtitle extraction are disabled. See
`EXTERNAL_PROGRAMS.md`.

---

## Configuration

Most configuration lives at the top of `dvd_server_backend.py`:

| Constant | Default | Meaning |
|---|---|---|
| `DVD_FOLDER` | `<base>/dvds` | Library root |
| `VIDEO_EXTENSIONS` | `.mkv .mp4 .webm .vob` | Recognised video files |
| `MIN_CACHE_FILE_SIZE` | 1 MiB | Below this, a `.remux` file is treated as corrupt and deleted |
| `MAX_SCAN_DEPTH` | 6 | Recursion cap during library scan |
| `SKIP_DIR_NAMES` | (set) | Folders never descended into |
| `TEXT_SUB_CODECS` | (set) | Subtitle codecs considered browser-playable |
| `BROWSER_AUDIO_CODECS` | (set) | Audio codecs that can be passthrough-remuxed |

Port and bind address are set at the bottom of the file in `serve(app, ...)`:

    serve(app, host="0.0.0.0", port=4251, threads=8)

To change the port permanently, update both `serve(...)` and the
`DiscoveryService(port=...)` call just above it, then restart.

### Frontend preferences

The web UI stores these in `localStorage` (per browser, per device):

| Key | Meaning |
|---|---|
| `theme` | Selected theme id (e.g. `terminal`, `dracula`) |
| `dimmed` | `"1"` if dim-UI mode is on |
| `sortMode` | Title sort order: `name`, `shortest`, `longest`, `size` |
| `autoplayNext` | `"1"` to auto-play the next title when one ends |
| `cacheMode` | `none`, `on_end`, `on_dvd_change` |
| `expandedGenres` | JSON array of expanded genre names |

Clear them all with `localStorage.clear()` in the browser console.

---

## Using the web UI

Open `http://<server-ip>:4251/` from any browser on the LAN.

### Sidebar

- **Library** — genres collapsed by default; click a genre header to expand.
  Expanded state is remembered.
- **Cache** — total disk used by remuxed audio tracks and extracted subtitles.
  Each DVD has its own "Clear" button; "Clear All" wipes everything.
- **Auto-Clear Cache** — pick one:
  - **Never** — cache persists until you manually clear it
  - **On File End** — clear this DVD's cache when the video finishes playing
  - **On DVD Change** — clear the previous DVD's cache when you load a new one
- **Theme** — 13 palettes, discovered automatically from `themes.css`.
  Add a new one by copying any `body.theme-xyz { ... }` block.
- **Dim UI** — darkens everything except the video player.

### Player

- **Pre-play overlay** shows cover art, title, and a big Play button
- **Chapter panel** on the right; click any chapter to jump
- **Title dropdown** to pick a different title within the DVD
- **Audio / Subs dropdowns** — selection while paused is deferred until playback
  starts, so no wasted work remuxing a track you didn't want
- **Autoplay toggle** — automatically advance to the next title at end-of-file
- **Sort dropdown** — how titles are ordered in the dropdown

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` / `K` | Play / pause |
| `←` / `→` | Seek −10s / +10s |
| `↑` / `↓` | Volume up / down |
| `M` | Mute |
| `F` | Toggle fullscreen |

---

## HTTP API

All endpoints return JSON. Base path is `/api`.

### Library

**`GET /api/dvds`**

Full library listing plus genre grouping.

    {
      "dvds": [
        {
          "name":         "Action/Die Hard(1988) disc_1",
          "display_name": "Die Hard(1988) disc_1",
          "genre":        "Action",
          "subpath":      "",
          "path":         "/abs/path/to/dvds/Action/Die Hard(1988) disc_1",
          "cover":        "/api/dvd/cover/Action/Die%20Hard(1988)%20disc_1"
        }
      ],
      "genres": [
        { "name": "Action", "label": "Action", "dvds": [ ... ] },
        { "name": "",       "label": "Uncategorized", "dvds": [ ... ] }
      ]
    }

`cover` is `null` if there's no `cover.png` in the folder.

**`GET /api/dvd/cover/<path:dvd_name>`**

Returns the `cover.png` inside a DVD folder. `dvd_name` may contain slashes
(as in the `name` field above). Returns `404` if the folder or cover is missing.

### Loading

**`POST /api/dvd/load/<path:dvd_name>`**

Loads a DVD, probes every video file inside it, and returns titles with
chapter, subtitle, and audio metadata. This can take a few seconds the first
time — `ffprobe` reads each file end-to-end.

    { "title": "Action/Die Hard(1988) disc_1", "titles": [ ... ] }

**`GET /api/dvd/current`**

Returns the currently loaded DVD and its titles, or `404` if none.

**`POST /api/dvd/title/<int:title_idx>`**

Selects a title within the loaded DVD. Returns chapter, subtitle, and audio
metadata plus a `stream_url`.

    {
      "title_idx": 0,
      "file":      "title_t00.mkv",
      "duration":  7925.4,
      "chapters":  [ { "number": 1, "title": "Opening", "start": 0.0, "end": 312.5 }, ... ],
      "subtitles": [ ... ],
      "audio":     [ ... ],
      "stream_url": "/api/dvd/stream/0"
    }

### Streaming

**`GET /api/dvd/stream/<int:title_idx>[?audio=N]`**

Streams the title. With `audio=0` or omitted, the original MKV is served
directly. With `audio=N` (N > 0), the server remuxes the selected audio track
into an MP4 cache file on first request, then serves it.

Supports HTTP `Range` — seeking works in every modern browser.

**`GET /api/dvd/subtitle/<int:title_idx>/<path:sub_id>`**

Returns WebVTT text. `sub_id` is the `id` field from the subtitle metadata
(either `embedded:<stream_index>` or `file:<filename>`).

Returns `415` if the subtitle is a bitmap format that can't be rendered in a
browser.

### Cache management

**`POST /api/dvd/cache-track/<int:title_idx>/<int:audio_idx>`**

Kicks off a background remux of one alternate audio track. Idempotent — if the
cache is already valid, returns immediately. Response:

    { "started": true, "audio_idx": 2 }

**`GET /api/dvd/cache-status/<int:title_idx>/<int:audio_idx>`**

Reports whether the track's cache file exists and is valid.

    { "ready": true, "suffix": "aac" }

**`GET /api/dvd/cache/info`**

Disk usage per DVD. The `dvds[].name` field uses the same relative-path
format as `/api/dvds`.

    {
      "total_files": 12,
      "total_bytes": 10587612,
      "total_mb":    10,
      "dvds": [
        { "name": "Action/Die Hard(1988) disc_1", "files": 3, "bytes": 3145728, "mb": 3, ... }
      ]
    }

**`POST /api/dvd/cache/clear[?dvd=<name>]`**

Deletes cache files. With `?dvd=` clears one DVD (URL-encode the name; slashes
are fine), without it clears everything.

Returns `409` if every candidate file is locked (Windows: file open in a
player; Linux: file open in a process that didn't release it). Close the video
and retry.

### Client-app discovery

Advertises `_dvds._tcp.local.` over mDNS/Bonjour while running. TXT records:

| Key | Meaning |
|---|---|
| `name` | Human-readable server name |
| `version` | Protocol version |
| `api` | Entry-point endpoint (`/api/dvds`) |
| `http` | Always `"1"` — hint that it speaks HTTP |

Android app authors: `NsdManager` on Android, `NWBrowser` on iOS/macOS,
`avahi-browse -t _dvds._tcp` on Linux.

---

## Caching

The server writes two kinds of files inside each DVD folder. Both are safe to
delete at any time — the MKV is never touched.

### `.remux/`

Contains remuxed MP4s of non-default audio tracks.

    title_t00.a1.aac.mp4     # audio track 1, re-encoded to AAC
    title_t00.a2.copy.mp4    # audio track 2, passed through untouched

Naming: `<stem>.a<idx>.<suffix>.mp4` where `<suffix>` is `copy` for codecs the
browser can play directly (AAC, MP3, Opus, Vorbis, FLAC) or `aac` for anything
else. The video stream is **always** copied bit-for-bit (`-c:v copy`), so this
is fast and lossless for video.

**First-play latency:** the first time you select an alternate audio track, the
server remuxes it on the fly. For a 2-hour movie this usually takes 20–60
seconds on an Odroid N2. Once cached, subsequent selections are instant.

Background caching is triggered automatically when you change the audio track
in the UI — the cache is built even if you switch away before it finishes.

### `.subtitles/`

Contains WebVTT extractions of embedded text subtitles.

    title_t00.stream3.vtt    # stream index 3, extracted as WebVTT

Extraction happens on first request for that subtitle. Once cached, the file is
served directly.

### Validity heuristic

A cache file smaller than `MIN_CACHE_FILE_SIZE` (1 MiB) is treated as a
corrupted/incomplete file and deleted before reuse. This catches the common
case of a server crash or power loss mid-remux.

### Disk space

A remux is roughly the size of the original video plus a small AAC audio
stream. If you have a large library and frequently switch audio tracks, the
cache can get big. The **Cache** panel in the sidebar shows exactly how much
each DVD consumes, with per-DVD and global clear buttons.

### Auto-clear modes

- **Never** — cache persists forever
- **On File End** — clears this DVD's cache when the video ends
- **On DVD Change** — clears the previous DVD's cache when you load a new DVD

Both auto modes skip the confirm dialog and silently ignore locked files
(they'll be cleaned up the next time you clear).

---

## Security model

The server is designed for a **trusted LAN**. It has no authentication and no
TLS. Do not expose port 4251 to the internet. If you need remote access, put
it behind a VPN (WireGuard, Tailscale) or a reverse proxy with auth.

### Path traversal protection

Every user-supplied path is passed through `_safe_dvd_path()` before touching
the filesystem. It:

1. Rejects empty input
2. Normalises backslashes so `..\..\etc` can't smuggle a traversal on Linux
3. Splits on `/` and validates each segment via `_is_safe_path_component()`
4. Calls `Path.resolve()` (resolves symlinks and `..`)
5. Verifies the resolved path is a descendant of `DVD_FOLDER` via
   `relative_to()`

`_is_safe_path_component()` additionally rejects:

- Empty strings, `.`, `..`
- NUL bytes
- `:` (Windows drive letters and NTFS alternate data streams)
- Windows reserved device names: `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`,
  `LPT1`–`LPT9`

The static-file route (`/<path:filename>`) applies the same containment check.

### Symlinks are never followed

- The library scanner uses `os.scandir` and skips every entry where
  `is_symlink()` is true.
- Directory listings use `follow_symlinks=False`.
- Cache cleanup only unlinks files with `follow_symlinks=False`.

This means **a symlink inside `dvds/` pointing to `/etc` or `C:\Windows` will
not be scanned, listed, or served**. It also means you can't symlink external
media into `dvds/` — use a bind mount instead.

### Cache files

Only files inside a DVD folder's `.remux/` and `.subtitles/` subdirectories
are ever deleted by the cache-clearing endpoints. The `.mkv` (and any other
source media) is never touched.

### Recommended deployment

- **Firewall:** allow `4251/tcp` from your LAN only
- **Reverse proxy (if you want HTTPS):** nginx/Caddy in front, with basic auth
- **Remote access:** VPN, not port forwarding
- **Shared NAS:** the server is read-only against the library except for the
  cache directories it writes

---

## Troubleshooting

### "MISSING FUNCTIONS: [...]" in the browser console

The JS bundle is corrupted. Restore `index.html` from a known-good copy. The
sanity check runs on every page load and refuses to start if any of the eleven
core functions are absent.

### Service won't start (`status=217/USER`)

The `User=` in `/etc/systemd/system/dvdserver.service` doesn't exist. Edit the
unit and change it to a real account:

    sudo systemctl edit --full dvdserver.service
    # fix the User= line, save, exit
    sudo systemctl daemon-reload
    sudo systemctl restart dvdserver

### Service won't start (`status=200/CHDIR`)

`WorkingDirectory=` points at a directory that doesn't exist or isn't readable
by the service user. Check the three path-valued lines agree:

    systemctl cat dvdserver | grep -E 'WorkingDirectory|ExecStart'

### Service exits immediately, log shows `ModuleNotFoundError: No module named 'discovery'`

`discovery.py` isn't in the working directory. Either copy it there or remove
the `from discovery import ...` block from `dvd_server_backend.py`.

### "ffmpeg not found" in the banner

Audio-track switching and embedded-subtitle extraction are disabled. Install
ffmpeg — see `EXTERNAL_PROGRAMS.md`.

### Audio track switch hangs forever

The remux is running in the foreground on the first play. Check
`journalctl -u dvdserver -f` (Linux) or the console (Windows) for `Remux ...`
log lines. For a large file on a slow disk this can take a minute. Subsequent
plays of the same track use the cache.

If it never completes, check disk space — remux files are roughly the size of
the original video.

### Subtitles show "not playable"

The subtitle track is bitmap-based (VOBSUB from a DVD, or PGS from Blu-ray).
Browsers cannot render these. Options:

1. Extract the subtitles to text with OCR (`Subtitle Edit` on Windows,
   `vobsub2srt` on Linux) and drop the resulting `.srt` next to the MKV.
2. Find a text-based subtitle file online and drop it next to the MKV.

### Cache clear returns `409 All cache files are locked`

A cache file is currently open — either a browser is playing it, or a process
(e.g. an antivirus scanner on Windows) is holding it. Close the video, wait a
few seconds, retry. On Windows, `Sysinternals handle.exe` can identify the
holder:

    handle.exe title_t00.a1.aac.mp4

### Genre header clicks do nothing

Check the browser console. Most likely cause: an old cached version of
`index.html`. Hard-reload with `Ctrl+Shift+R` (or `Cmd+Shift+R` on macOS).

### "Expanded genres" state got corrupted

Clear the frontend state:

    localStorage.removeItem("expandedGenres");
    location.reload();

Or clear everything with `localStorage.clear()`.

### Videos stutter or won't play

The server streams whatever the browser requests. MKV containers vary in what
codecs they hold:

- **H.264 + AAC** — plays everywhere
- **H.265 / HEVC** — plays in Safari and Edge on Windows; Chrome and Firefox
  need hardware support
- **VP9 / AV1** — plays in modern browsers
- **MPEG-2 (raw DVD video)** — plays in Chrome and Edge only
- **AC3 / DTS / TrueHD audio** — plays in Chrome and Edge, not Firefox

If a title won't play, try a different browser. The server isn't transcoding
video — it's just serving the bytes.

### Stream URL works in curl but video shows a decoder error

The browser negotiated a codec it can't decode. Check the "Audio" dropdown —
tracks marked "(not browser-playable)" would fail. Choose a track that isn't
flagged.


### Dependencies at a glance

**Python** (`requirements.txt`):

- `Flask` — HTTP routing, `send_file` with Range support
- `flask-cors` — permissive CORS for browser clients
- `waitress` — production WSGI server (pure Python, runs on ARM)
- `zeroconf` — mDNS/Bonjour service advertisement

**External binaries** (`EXTERNAL_PROGRAMS.md`):

- `ffprobe` — reads duration, chapters, and stream metadata
- `ffmpeg` — remuxes alternate audio tracks, extracts embedded subtitles

Both come from the same upstream package.

---
