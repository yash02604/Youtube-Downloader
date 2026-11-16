# Windows packaging

Build the one-folder application from the project root with Python and the
application requirements installed:

```powershell
python -m pip install -r packaging\requirements-build.txt
python -m pip install -r requirements.txt
.\packaging\build_windows.ps1
```

The build output is `dist\VideoDownloadManager\`. Run that folder on a clean
Windows machine without Python before distributing it. yt-dlp and its EJS
package are collected by the spec. Tkinter and Python are packaged by
PyInstaller. FFmpeg/ffprobe and Deno are external and can be selected in app
Settings; they are deliberately not bundled here.

After the executable has passed a clean-machine smoke test, compile
`VideoDownloadManager.iss` with Inno Setup. It installs under the current
user's Local AppData, creates a Start Menu shortcut and offers an optional
desktop shortcut. Uninstall removes application files only. User downloads,
history, state, and configuration live under the user's profile and are not
deleted by the installer script.

Current environment verification (2026-11-09): PyInstaller 6.22.3 built the
one-folder application, and Inno Setup 6.7.3 compiled the installer. The
packaged application launched from a temporary copied package folder and
displayed its main window and settings. A transient five-second
FFmpeg version-check timeout caused a false unavailable status after startup.
The app now caches successful checks keyed by both executable file sizes and
modification times; the rebuilt package's video-analysis dialog displayed both
configured paths, and its log confirmed validation reuse. The final installer
was compiled, installed, and uninstalled in a disposable location; a sentinel
outside the installation and the removal of its Start Menu group were verified.
This does not replace live workflow checks or a clean-machine test. The app has
no custom icon, code signing, auto-update, or bundled FFmpeg/Deno runtime.
