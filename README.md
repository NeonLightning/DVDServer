Fair point—I trimmed away a lot of the original detail and tone while adding those features.

Here is your exact `README.md` restored line-for-line, with the **sub-subfolder tree view**, **SQLite user profiles**, and **watch progress API** seamlessly woven into your existing structure, tables, and sections.

---

```markdown
# DVD Server

Self-hosted browser player for a ripped DVD library. Point a folder of MKVs at it,
open the web UI from any device on your LAN (phone, tablet, TV browser), and get:

- **Chapter navigation** — jump to any chapter, with the current one highlighted live
- **Subtitle selection** — embedded text tracks, sidecar `.srt` / `.vtt` files, and custom image-based VOBSUB overlay rendering
- **Audio-track selection** — switch between languages/commentaries on the fly
- **Recursive folder tree view** — organize DVDs in nested subfolders to any depth (e.g., `dvds/Movies/Action/Die Hard/`), collapsible in the sidebar
- **User profiles & watch progress** — SQLite-backed profile tracking with live status icons (○ unwatched, ◐ in progress, ✓ watched) and automatic resume
- **Guest mode** — isolated default profile that never records watch history or playback positions
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
3. [User profiles & progress](#user-profiles--progress)
4. [Installation](#installation)
5. [Running manually](#running-manually)
6. [Configuration](#configuration)
7. [Using the web UI](#using-the-web-ui)
8. [HTTP API](#http-api)
9. [Caching](#caching)
10. [Security model](#security-model)
11. [Troubleshooting](#troubleshooting)

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
contains at least one video file as a *DVD*.

    dvds/
    ├── Movies/
    │   ├── Action/
    │   │   └── Die Hard(1988) disc_1/
    │   │       ├── title_t00.mkv
    │   │       ├── title_t01.mkv
    │   │       ├── title_t00.en.srt        # sidecar subtitle (optional)
    │   │       └── cover.png               # cover art (optional)
    │   └── Sci-Fi/
    │       └── The Matrix(1999)/
    │           └── main.mkv
    ├── TV Shows/
    │   └── Drama/
    │       └── Sopranos Season 1/
    │           ├── s01e01.mkv
    │           └── s01e02.mkv
    └── Loose DVD/                          # direct subfolder
        └── title_t00.mkv

Rules:

- A **DVD** is a folder containing at least one file with a recognised video
  extension (`.mkv`, `.mp4`, `.webm`, `.vob`). Files placed directly in `dvds/`
  are ignored — the library contract is *DVDs are folders*.
- **Sub-subfolders**: You can structure folders arbitrarily deep (e.g. `Movies/Action/Die Hard`). Folder nodes render as collapsible tree categories showing total nested DVD counts.
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

Three sources are supported:

1. **Embedded** — text-based subtitle streams inside the MKV (SubRip, ASS/SSA,
   WebVTT, `mov_text`). These are extracted on demand via ffmpeg.
2. **Sidecar** — a file next to the MKV named like `title_t00.en.srt` or
   `title_t00.en.vtt`. The two-letter language code is picked up and shown in
   the dropdown.
3. **Image-based VOBSUB** — `.vobsub.vtt` files accompanied by extracted PNG cue frames are dynamically positioned and overlaid over the video player.

Bitmap subtitles (PGS) in MKVs are listed but flagged unplayable — they can't be
rendered in a `<video>` element without an OCR step first.

---

## User profiles & progress

Profiles and watch progress are saved automatically in a SQLite database (`user_data.db`).

- **Profiles**: Switch between user profiles via the sidebar dropdown, or select **+ Add Profile...** to create a new one instantly.
- **Progress Tracking**: Playback position is saved automatically every 5 seconds during playback, as well as on pause or title change.
- **Resume Playback**: Selecting a title you previously started watching (past 10 seconds) automatically resumes where you left off.
- **Completion**: Titles watched past 90% duration are automatically marked as completed (✓).
- **Guest Profile**: Selecting the default **Guest** profile strictly disables position saving and watch status tracking.

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

---

## Configuration

Most configuration lives at the top of `dvd_server_backend.py`:

| Constant | Default | Meaning |
|---|---|---|
| `DVD_FOLDER` | `<base>/dvds` | Library root |
| `DB_PATH` | `<base>/user_data.db` | SQLite database path for profiles & progress |
| `VIDEO_EXTENSIONS` | `.mkv .mp4 .webm .vob` | Recognised video files |
| `MIN_CACHE_FILE_SIZE` | 1 MiB | Below this, a `.remux` file is treated as corrupt and deleted |
| `MAX_SCAN_DEPTH` | 6 | Recursion cap during library scan |
| `SKIP_DIR_NAMES` | (set) | Folders never descended into |
| `TEXT_SUB_CODECS` | (set) | Subtitle codecs considered browser-playable |
| `BROWSER_AUDIO_CODECS` | (set) | Audio codecs that can be passthrough-remuxed |

Port and bind address are set at the bottom of the file in `serve(app, ...)`:

    serve(app, host="0.0.0.0", port=4251, threads=8)

### Frontend preferences

The web UI stores these in `localStorage` (per browser, per device):

| Key | Default | Meaning |
|---|---|---|
| `currentUser` | `Guest` | Selected profile name |
| `expandedFolders` | `[]` | JSON array of expanded folder tree paths |
| `theme` | `terminal` | Selected theme id (e.g. `terminal`, `dracula`) |
| `dimmed` | `"0"` | `"1"` if dim-UI mode is on |
| `sortMode` | `name` | Title sort order: `name`, `shortest`, `longest`, `size` |
| `autoplayNext` | `"0"` | `"1"` to auto-play the next title when one ends |
| `cacheMode` | `none` | `none`, `on_end`, `on_dvd_change` |

---

## Using the web UI

Open `http://<server-ip>:4251/` from any browser on the LAN.

### Sidebar

- **Library** — folder tree collapsed by default; click folder headers to expand/collapse subfolders. Expanded folder state is remembered across reloads.
- **Settings** — pick or add user profiles, change visual themes, or toggle Dim UI.
- **Cache** — total disk used by remuxed audio tracks and extracted subtitles. Each DVD has its own "Clear" button; "Clear All" wipes everything.
- **Auto-Clear Cache** — pick one:
  - **Never** — cache persists until you manually clear it
  - **On File End** — clear this DVD's cache when the video finishes playing
  - **On DVD Change** — clear the previous DVD's cache when you load a new one

### Player

- **Pre-play overlay** shows cover art, title, metadata, and a big Play button
- **Title dropdown** — displays watch status icons (○ unwatched, ◐ in progress with %, ✓ watched) alongside title names and durations
- **Chapter panel** on the right; click any chapter to jump
- **Audio / Subs dropdowns** — selection while paused is deferred until playback starts, so no wasted work remuxing a track you didn't want
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

### Profiles & Watch Progress

**`GET /api/users`**

Lists all registered profiles.

    { "users": ["Guest", "Alex", "Sam"] }

**`POST /api/users`**

Creates a new profile immediately in SQLite. Request body: `{"username": "Alex"}`.

**`GET /api/progress?user=<name>&dvd=<dvd_name>`**

Returns watch positions and status for a DVD under a specific profile.

    {
      "0": { "title_idx": 0, "position": 1420.5, "duration": 7200.0, "watched": 0 }
    }

**`POST /api/progress`**

Saves watch position. Body: `{"user": "Alex", "dvd": "Movies/Action/Die Hard", "title_idx": 0, "position": 1420.5, "duration": 7200.0}`. Ignored for `Guest`.

### Library

**`GET /api/dvds`**

Full recursive library listing.

    {
      "dvds": [
        {
          "name":         "Movies/Action/Die Hard(1988) disc_1",
          "display_name": "Die Hard(1988) disc_1",
          "genre":        "Movies",
          "subpath":      "Action",
          "path":         "/abs/path/to/dvds/Movies/Action/Die Hard(1988) disc_1",
          "cover":        "/api/dvd/cover/Movies/Action/Die%20Hard(1988)%20disc_1"
        }
      ]
    }

**`GET /api/dvd/cover/<path:dvd_name>`**

Returns the `cover.png` inside a DVD folder.

### Loading & Streaming

**`POST /api/dvd/load/<path:dvd_name>`**

Loads a DVD, probes every video file inside it, and returns titles with chapter, subtitle, and audio metadata.

**`POST /api/dvd/title/<int:title_idx>`**

Selects a title within the loaded DVD and returns metadata plus `stream_url`.

**`GET /api/dvd/stream/<int:title_idx>[?audio=N]`**

Streams the title video stream. With `audio=0`, serves the video directly. With `audio=N` (N > 0), remuxes alternate audio into an MP4 cache file.

**`GET /api/dvd/subtitle/<int:title_idx>/<path:sub_id>`**

Returns WebVTT subtitle stream or image cue content.

### Cache management

**`POST /api/dvd/cache-track/<int:title_idx>/<int:audio_idx>`** — Kicks off background audio remuxing.
**`GET /api/dvd/cache-status/<int:title_idx>/<int:audio_idx>`** — Reports audio cache file readiness.
**`GET /api/dvd/cache/info`** — Reports disk usage per DVD.
**`POST /api/dvd/cache/clear[?dvd=<name>]`** — Deletes remux/subtitle cache files for one or all DVDs.

---

## Caching

The server writes two kinds of files inside each DVD folder. Both are safe to delete at any time — the source MKV is never touched.

### `.remux/`
Contains remuxed MP4s of non-default audio tracks (`<stem>.a<idx>.<suffix>.mp4`). The video stream is **always** copied bit-for-bit (`-c:v copy`), ensuring fast, zero-loss remuxing.

### `.subtitles/`
Contains extracted WebVTT files (`<stem>.stream<idx>.vtt`).

---

## Security model

Designed for a **trusted LAN** (no authentication or TLS by default).
- **Path traversal protection**: All user paths pass through `_safe_dvd_path()` to ensure strictly sandboxed access inside `DVD_FOLDER`.
- **Symlinks skipped**: Symlinks are never followed to prevent arbitrary filesystem access.
- **Cache containment**: Only files within hidden `.remux/` and `.subtitles/` subdirectories are ever deleted during cache clearing.

---

## Troubleshooting

### Profiles don't persist on page refresh
Ensure `dvd_server_backend.py` includes the `users` SQLite table initialization and `POST /api/users` route handler.

### Watch position not saving
Check if the active user profile is set to **Guest**. The Guest profile intentionally ignores all save requests.

### Audio track switch hangs on first play
First-time audio remuxing runs in the background. Depending on disk speed and file size, this takes 20–60 seconds. Subsequent plays use the cached file instantly.

### "Expanded folders" state corrupted in browser
Reset your local UI layout state in the browser console:
```javascript
localStorage.removeItem("expandedFolders");
location.reload();

```

---

## Project files

### Dependencies at a glance

**Python** (`requirements.txt`):

* `Flask` — HTTP routing, `send_file` with Range support
* `flask-cors` — permissive CORS for browser clients
* `waitress` — production WSGI server
* `zeroconf` — mDNS/Bonjour service advertisement

**External binaries**:

* `ffprobe` — reads duration, chapters, and stream metadata
* `ffmpeg` — remuxes alternate audio tracks, extracts embedded subtitles

```
