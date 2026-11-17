# Downloader WebPage - Internet Download Manager

A Tkinter download manager with HTTP downloads, queue controls, and yt-dlp-based video support.

## Features

- ✅ **Smart Video Detection** - Automatically detects and analyzes video URLs from YouTube, Vimeo, TikTok, and more
- ✅ **Reliable HTTP Downloads** - Single-connection transfers with bounded retries and byte-range resume when the server provides a stable validator
- ✅ **Video Downloads** - Download videos from popular platforms using yt-dlp integration
- ✅ **Video inspection** - Read title, channel, duration, available resolutions, codecs, size estimates, and subtitle languages before downloading
- ✅ **Playlist selection** - Inspect a playlist and choose individual entries; downloads are queued as separate single-video jobs
- ✅ **Format choices** - Choose best available quality, Best MP4 Compatible, available resolutions, original best audio, or MP3 audio
- ✅ **Pause, Resume, and Cancel** - Control transfers without exposing partial files as completed downloads
- ✅ **Download Queue** - Manage multiple downloads with automatic queuing
- ✅ **Concurrent Downloads** - Global limit for the number of active downloads
- ✅ **Progress Reporting** - Track downloaded bytes, speed, progress, and ETA
- ✅ **Crash Recovery** - Previously active transfers restore with an explicit interrupted state
- ✅ **User-friendly GUI** - Clean interface with status indicators
- ✅ **Support for All File Types** - Downloads any file from the internet

HTTP partial data is kept in a `.part` file with validator metadata. A resume is
accepted only when the server confirms the requested range; if it ignores the
range or the resource changes, the transfer restarts from byte zero. Completed
files are finalized only after their received size matches the server's declared
size, when available. Persistent state and history are stored in the per-user
application data folder (`%APPDATA%\VideoDownloadManager` on Windows), with
settings in `app_config.json` and rotated logs under `logs\app.log`. Legacy
config, history, and state JSON files beside the source application or frozen
executable are copied there once when present; the originals are left in
place.
Cancellation retains HTTP partial data and validator metadata for a later manual
resume.

Multi-connection HTTP downloading and bandwidth throttling are not enabled in
this reliability phase. The current video-download controls use yt-dlp's
separate workflow and do not yet share all HTTP recovery guarantees.

## Video download behavior

Video metadata is requested with yt-dlp's `download=False` mode; inspecting a
video does not download its media. A direct video URL is treated as one video
even when it contains a YouTube `list` parameter. A playlist URL opens an entry
selection dialog, and only the selected entries are queued. Playlist progress
is shown per video and as an estimated equal-weight aggregate across selected
entries.

Best Available selects yt-dlp's best video-only format plus its best audio-only
format (`bv[acodec=none]+ba/b`) without a resolution or codec cap. Resolution
modes apply a maximum height and use the best available audio. Best MP4
Compatible is a separate, explicitly compatibility-limited choice. Best
Original Audio uses `bestaudio` and preserves the source format. MP3 High
Quality selects the best source audio and transcodes it to VBR quality 0.

FFmpeg and ffprobe are checked by invoking each executable. Discovery checks
the configured `ffmpeg_location` in the app config, bundled application
locations, the current process `PATH`, and (on Windows) the persisted user and
machine `PATH` plus WinGet package locations. This allows discovery to work
even when the GUI was launched before a PATH update. The resolved executable
paths are logged and passed to yt-dlp; FFprobe subprocess calls use the
resolved `ffprobe.exe`. Settings includes a Configure FFmpeg folder picker;
select the directory containing both `ffmpeg.exe` and `ffprobe.exe`, such as
`C:\ffmpeg\bin`. If separate streams cannot be merged because FFmpeg is
unavailable, the UI explains the quality fallback before continuing.
Normal video merging/remuxing uses stream copy; it does not re-encode video.
The download list shows the formats actually selected by yt-dlp, including
resolution, FPS, video codec, and audio codec when provided.

YouTube JavaScript challenges use Deno when a supported executable is found.
Discovery checks `"deno_location"` in the app config, bundled application
locations, `%USERPROFILE%\.deno\bin`, `%LOCALAPPDATA%\deno\bin`, and `PATH`;
set the config value to `deno.exe` or its containing directory. The executable
must run and meet yt-dlp's minimum supported version. The yt-dlp `default` extra
installs a matching local `yt-dlp-ejs` package. Remote EJS components remain
disabled.

Pause stops the current yt-dlp attempt through a cooperative hook request;
resume starts a new execution and relies on yt-dlp's own partial-file
continuation. This is best-effort, not an in-process pause guarantee:
interrupted fragmented downloads may not retain reusable fragments, and pause
may take effect only after an active post-processing step. yt-dlp retries are
bounded and limited to network, timeout, and temporary HTTP failures. The app
does not claim exact fragment-resume behavior across all extractors. The video
information dialog offers opt-in subtitle download/embedding for
metadata-listed languages, on-demand thumbnail preview, thumbnail
saving/embedding, and metadata embedding.
Embedding requires FFmpeg. Optional embed failures remain visible as warnings
when the downloaded audio/video validates. Preview images are fetched only
when requested; the fetch is size-limited, does not follow redirects, and
rejects non-public network destinations.

Completed yt-dlp outputs are checked with ffprobe for the expected audio and
video streams when ffprobe is available. A successful probe that finds missing
required streams fails the item and keeps the output for diagnosis. If ffprobe
is missing or cannot run, the download completes with validation marked
unavailable instead of claiming a successful probe.

## Installation

1. Install Python 3.9 or higher
2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the application:
```bash
python main.py
```

Settings includes download location, concurrency, quality defaults, subtitle
and thumbnail defaults, FFmpeg/Deno configuration, and a credential-free
diagnostics copy action. Right-click a download to retry/resume, move it to the
front or end of the queue, open a completed file/folder, or remove its entry;
removing an entry does not delete its media. Logs are stored under
`%APPDATA%\VideoDownloadManager\logs\app.log` with rotation. The GUI uses
standard-library Tkinter/ttk; a rendered
review on a normal Windows desktop is still required before release.

## Windows packaging

The repository includes a PyInstaller one-folder spec and an Inno Setup script.
The app does not bundle FFmpeg or Deno. Install/configure them separately when
needed for stream merging, MP3 conversion, JavaScript challenges, and optional
embedding. See `packaging/README.md`. A one-folder executable and installer
have been built and installed in a disposable test location. A transient
five-second FFmpeg validation timeout caused one false unavailable status;
the app now reuses successful checks for unchanged executables, and the rebuilt
package showed both tool paths in the video-analysis dialog. Keep the project
at BETA READY until the remaining live workflow checks are complete.

## How to Use

1. **For Regular Files:**
   - Paste any direct download URL
   - Select download location
   - Start the download; HTTP transfers use one connection and validated resume when the server supports it

2. **For Videos:**
   - Paste video URL (YouTube, Vimeo, TikTok, etc.)
   - The app inspects metadata before downloading; select entries for playlists
   - Choose video quality or switch to Audio Only (MP3 conversion requires FFmpeg)
   - Select download location and confirm the video information dialog

3. **Download Management:**
   - Use pause/resume buttons to control downloads
   - View progress, speed, and ETA in real-time
   - Right-click for retry, queue order, open file/folder, or remove entry
   - Remove entry keeps the downloaded media on disk

## Tests

Run the offline suite with `python -m unittest discover -s tests`. Its HTTP
integration tests bind localhost; if a restricted sandbox blocks loopback,
run those tests in an environment that permits local sockets.

## Supported Video Platforms

- YouTube
- Vimeo
- TikTok
- Dailymotion
- Facebook Videos
- Instagram Videos
- And many more (via yt-dlp)

## Local web interface

The optional Next.js frontend and FastAPI backend run separately from the desktop
application. See [the local web workflow](docs/WEB_FRONTEND.md) for setup,
validation and limitations. The API remains for local development; completed
files are not served to the browser yet.
