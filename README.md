```markdown
# 📀 DVD Server

A self-hosted, lightweight media streaming server and web interface designed for streaming ripped DVD media (`.mkv`, `.mp4`, `.webm`, `.vob`). Features include multi-level directory navigation, chapter selection, audio track remuxing, image-based and text subtitle rendering, user profiles, watch progress persistence, and smart cache management.

---

## Key Features

* **Recursive Subfolder Navigation**: Organizes media in a collapsible tree UI supporting arbitrary directory depth (e.g., `Type/Genre/Subgenre/DVD_Title`).
* **Multi-Profile Support**: Built-in user profile management stored in a local SQLite database (`user_data.db`). Includes an isolated **Guest** mode that bypasses progress tracking.
* **Watch History & Resume**: Automatically saves playback positions per title and profile, visually flagging unwatched ($\bigcirc$), in-progress ($\Phi$), and completed ($\checkmark$) titles.
* **On-the-Fly Audio Remuxing**: Leverages `ffmpeg` to remux non-browser-supported audio formats (e.g., AC3, DTS) to standard stereo AAC on demand while maintaining original video streams.
* **Subtitle Rendering**: Supports external WebVTT/SRT subtitles as well as custom image-based VOBSUB overlays rendered directly in the video player interface.
* **Chapter Navigation**: Automatically parses and lists media chapters via `ffprobe` for quick section jumping.
* **Smart Cache Management**: Automated and manual cache clearing routines for background remux files (options for *On File End*, *On DVD Change*, or *Manual Only*).
* **Customizable UI**: Includes multiple built-in color themes (Terminal, Midnight, Light), a theater "Dim UI" toggle, and automatic cover art detection (`cover.png`).

---

## Directory Structure

Place your DVD folders and media files inside a `dvds` directory alongside the server files:

```text
.
├── dvds/                         # Media root folder
│   ├── Movies/
│   │   ├── Action/
│   │   │   └── Die Hard (1988)/
│   │   │       ├── cover.png    # Optional poster artwork
│   │   │       ├── title00.mkv
│   │   │       └── title01.mkv
│   │   └── Sci-Fi/
│   │       └── The Matrix (1999)/
│   │           └── main.mkv
│   └── TV Shows/
│       └── Drama/
│           └── Sopranos Season 1/
│               ├── s01e01.mkv
│               └── s01e02.mkv
├── dvd_server_backend.py
├── index.html
├── themes.css
└── user_data.db                 # Auto-created SQLite database

```

---

## Requirements & Prerequisites

* **Python**: `3.10+`
* **Media Utilities**: `ffmpeg` and `ffprobe` installed and added to your system `PATH` (or located in standard program directories like `C:\ffmpeg\bin`).

### Python Dependencies

Install required modules via `pip`:

```bash
pip install flask flask-cors waitress

```

---

## Getting Started

1. **Launch the Server**:
Run the backend script using Python:
```bash
python dvd_server_backend.py

```


2. **Access the Web Interface**:
Open your browser and navigate to:
```text
http://localhost:4251

```


*(The server also accepts local network connections on your LAN IP, e.g., `http://192.168.1.X:4251`)*.

---

## API Summary

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/dvds` | `GET` | Fetches the full library directory tree structure. |
| `/api/users` | `GET` / `POST` | Lists existing profiles or creates a new user profile. |
| `/api/progress` | `GET` / `POST` | Fetches or updates watch progress for a user and DVD title. |
| `/api/dvd/load/<path>` | `POST` | Probes media files and returns title and chapter metadata. |
| `/api/dvd/title/<idx>` | `POST` | Selects an active media title for streaming. |
| `/api/dvd/stream/<idx>` | `GET` | Streams video directly or outputs on-the-fly remuxed audio streams. |
| `/api/dvd/cache/clear` | `POST` | Flushes remuxed temporary video/audio cache files. |

---

## Configuration & Customization

* **Port & Server**: Handled via `waitress` on port `4251` by default. This can be configured at the bottom of `dvd_server_backend.py`.
* **Themes**: Additional UI themes can be added to `themes.css` using the standard `body.theme-<name>` CSS selector format.

```