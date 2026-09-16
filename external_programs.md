# External Programs

The DVD Server is Python, but two of its features shell out to **ffmpeg**:

- Switching audio tracks (remuxing a non-default track into an MP4)
- Extracting embedded text subtitles into WebVTT

`ffprobe` (used to read durations, chapters, streams) ships in the **same
download** as `ffmpeg`. Install one, get both.

Everything else is a Python package — see `requirements.txt`.

---

## Linux (Debian / Ubuntu / Armbian / Raspberry Pi OS)

The preferred route; the package manager handles arch-specific builds.

    sudo apt update
    sudo apt install -y ffmpeg

Verify:

    ffmpeg -version
    ffprobe -version

Arch:

    sudo pacman -S ffmpeg

Fedora:

    sudo dnf install ffmpeg

### Static builds (if your distro's ffmpeg is stale)

John Van Sickle ships static, dependency-free builds for `amd64`,
`arm64`, and `armhf` — good for odd Armbian kernels where the packaged
build has trouble:

    https://johnvansickle.com/ffmpeg/

Download the tarball, extract, and drop `ffmpeg` / `ffprobe` into
`/usr/local/bin/` (already on `PATH`) or `~/.local/bin/`.

---

## Windows

### Option A — winget (Windows 10/11, easiest)

    winget install Gyan.FFmpeg

Restart your terminal afterward so `PATH` is refreshed.

### Option B — manual download (recommended for control)

Gyan Doshi's builds, the de-facto standard for Windows:

    https://www.gyan.dev/ffmpeg/builds/

Pick **ffmpeg-release-full.7z** (or `.zip`). Extract to something like:

    C:\ffmpeg\
        bin\
            ffmpeg.exe
            ffprobe.exe

Then add `C:\ffmpeg\bin` to your `PATH`:

1. Win+R → `sysdm.cpl` → Advanced → Environment Variables
2. Under "User variables" (or "System variables"), edit `Path`
3. Add `C:\ffmpeg\bin`
4. OK on every dialog
5. **Open a new terminal** — existing ones won't see the change

Verify in the new terminal:

    ffmpeg -version
    ffprobe -version

### Option C — Chocolatey

    choco install ffmpeg

### Option D — BtbN's builds (nightly)

    https://github.com/BtbN/FFmpeg-Builds/releases

Useful if you need a specific recent encoder. Same installation steps
as Option B.

---

## macOS

    brew install ffmpeg

Or static builds from:

    https://evermeet.cx/ffmpeg/

---

## Verifying the server sees it

Start the server. The banner prints the resolved paths:

    ============================================================
      DVD MKV Server
    ============================================================
    
    Base dir:    ...
    DVD Folder:  ...
    ffprobe:     /usr/bin/ffprobe
    ffmpeg:      /usr/bin/ffmpeg
    ...

If either says `NOT FOUND`, the server will run but you'll lose
audio-track switching and embedded subtitle extraction. Fix `PATH`
(or install ffmpeg) and restart.

### The server's search order

The backend tries, in order:

1. Bare `ffmpeg` / `ffprobe` on `PATH`
2. A small list of hard-coded Windows locations
   (`C:\ffmpeg\bin\...`, `C:\Program Files\ffmpeg\bin\...`, `D:\programs\...`)
3. A shallow walk of `D:\programs`, `C:\Program Files`, `C:\Program Files (x86)`

On Linux, step 1 is enough. The others are leftovers from the original
Windows-targeted build and are harmless no-ops.