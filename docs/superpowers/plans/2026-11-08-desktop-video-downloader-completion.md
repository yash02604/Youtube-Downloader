# Desktop Video Downloader Completion Plan

> **For agentic workers:** Use the Superpowers test-first workflow for each behavior change. Execute tasks in order and verify each before proceeding.

**Goal:** Complete the existing Windows Tkinter downloader to a tested beta/release-candidate state without regressing HTTP resume or proven yt-dlp quality selection.

**Architecture:** Preserve `DownloadManager` for transfer and lifecycle ownership, add small media-validation and persistence helpers, keep GUI work on the Tk thread, and use yt-dlp/FFmpeg through argument/config APIs. Persist user settings and history under a Windows user-data directory while migrating old project-local JSON files safely.

**Tech Stack:** Python, Tkinter/ttk, yt-dlp and yt-dlp-ejs, FFmpeg/ffprobe, Deno, unittest, Pillow (available in the current environment).

**Spec:** User attachments `Pasted text.txt` (MCP/plugin execution policy) and `Pasted text.txt` (desktop downloader master completion request), supplied in this Codex task.

## Global Constraints

- Keep the HTTP transfer and resume algorithm unchanged unless a reproducing test demonstrates a targeted defect.
- Preserve yt-dlp `default` extras, Deno `js_runtimes`, FFmpeg discovery, and stream-copy merging.
- Best Available remains best video plus best audio; Best MP4 Compatible remains a separate compatibility option.
- Live YouTube traffic is excluded from unit tests and limited to the already verified local smoke artifact plus a written manual procedure unless a minimal authorized integration action is required.
- Optional subtitle, thumbnail, and metadata work must not be enabled implicitly or turn optional postprocessing failures into core media failures.
- Do not build a website, add torrent support, bundle external binaries, or expose credentials in diagnostics/logs.
- Use Python standard library and installed tools first; do not add pytest, a type checker, or a linter solely for this task.

## Review Focus

- Malformed, incomplete, or missing ffprobe output must never cause a false Completed state; timeout/missing ffprobe behavior is non-destructive and covered by mocks.
- User supplied destination and metadata-derived filenames must remain confined to the chosen directory, including Windows drive, UNC, reserved-name, and separator inputs.
- Late callbacks during cancellation, pause, retry, validation, and application shutdown must not overwrite terminal/new-generation state.
- Optional subtitle, thumbnail, and metadata failures must preserve a successfully downloaded media file and remain visible as optional-feature warnings.
- Settings/history migration and packaged resource lookup must work when the current directory is not the project directory.

### Execution status — 2026-11-09

| Requirement area | Status | Evidence / remaining gap |
| --- | --- | --- |
| FFmpeg/ffprobe discovery and real Best Available smoke | Real stream selection/output and packaged diagnostic verified | App history records selected 401 AV1 3840×2026@24 + 251 Opus; fresh ffprobe confirms both streams. yt-dlp's merger uses `-c copy`. A transient five-second version-check timeout caused a false unavailable status; successful validation is now reused while executable sizes/mtimes match. Rebuilt package log and analysis dialog confirm this behavior. |
| Media structure validation and lifecycle | Implemented + verified | Offline missing/invalid/timeout/stale/cancel tests; queue/generation race tests passed three repeated repetitions; removal race test passed five repetitions after waiting for worker cleanup. Live pause/resume and cancellation remain unchecked. |
| User-facing error categories | Implemented + verified | Category mapping tests cover network, access, format, runtime, processing, validation, disk, and permission cases; dialogs omit raw technical traces. |
| Subtitle download/embed | Implemented + offline tested | Manual and automatic captions are opt-in and selected from metadata languages; live subtitle smoke remains on the checklist. |
| Thumbnail save/embed/preview | Implemented + offline tested | Preview is user initiated, size bounded, no redirects, and Tk updates use the UI queue. Preview was not fetched from a live service in this run. |
| Metadata embedding | Implemented + offline tested | Opt-in yt-dlp metadata postprocessor; optional failures retain validated media and redact warning text. |
| Playlist selection and per-item jobs | Implemented + unit tested | Select All/Clear All and separate item state are present; a live playlist workflow remains on the manual checklist. |
| Queue controls and ordering | Implemented + verified | Pause/resume/retry/cancel and move-to-front/end actions; reprioritization removes stale entries. Timing tests passed three consecutive repetitions. |
| Settings, history, recovery, and paths | Implemented + verified | Migration uses app directory, defaults validate, writes are atomic, recovered work stays interrupted, filename/output confinement tests pass. |
| GUI and diagnostics | Implemented + visually inspected | Windows startup and settings dialog were rendered; no full end-to-end UI smoke of every control. Uses standard Tk/ttk and one main downloads view. |
| Logging and dependency declaration | Implemented + verified | Rotating redacted logs; Pillow declared for preview; offline tests pass. |
| Manual security review | Partially verified | DNS results are validated and pinned to the actual socket; IPv4/IPv6, rebinding, redirects, TLS downgrade, proxy, and verification-off cases are tested. Environment proxies are disabled for this downloader to preserve destination pinning. |
| CodeRabbit review | Environment blocked | No CodeRabbit CLI/plugin is available and the folder has no Git metadata. Independent Cavecrew review was run and its findings were checked. |
| Codex Security MCP | Workflow blocked | The one attempted scan could not be completed because its saved draft was incomplete. Per the continuation policy, it was not retried; this is not evidence about application security. |
| Windows executable / installer | Final build and disposable install/uninstall verified | PyInstaller 6.22.3 and Inno Setup 6.7.3 built successfully; the app launched from a temporary copied package folder and displayed UI/settings. Final installer install/uninstall passed; its shortcut was removed and an outside sentinel remained. A clean-machine test, signing, and update packaging remain out of scope. |
| Final automated verification | Implemented + verified | Fresh suite: 123 passed, 0 failed, 0 skipped. Format-selector tests: 18 passed. Queue/generation timing tests passed three repeats; the terminal-removal race test passed five repeats. Python source compilation/import passed. Loopback tests need normal localhost access. |

---

### Task 1: Media validation and diagnostic error model

**Files:** `download_manager.py`, `format_selector.py`, `error_handler.py`, `tests/test_video_features.py`.

- [ ] Add mock ffprobe tests for valid AV, missing streams, audio-only, zero-byte/part files, timeout, malformed JSON, missing executable, process error, cancellation-before-validation, and stale generation.
- [ ] Implement bounded JSON ffprobe validation; mark validation unavailable when the executable is missing; preserve invalid output for diagnosis and fail completion when required streams are absent.
- [ ] Add stable error categories/user-safe messages for ffmpeg missing/error, postprocessing, output validation, runtime, media availability/auth, disk, and permission failures.
- [ ] Run focused media tests and the full existing suite.

### Task 2: Subtitle, thumbnail, and metadata options

**Files:** `download_manager.py`, `video_analyzer.py`, `gui.py`, `config.py`, `requirements.txt`, `tests/test_video_features.py`.

- [ ] Add mocked tests for manual/automatic subtitle languages, opt-in download/embed, unavailable language, optional postprocessor failure, thumbnail options, and metadata embedding.
- [ ] Pass only explicit user selections to yt-dlp; use Pillow/Tk image references on the UI thread for previews; classify optional feature failures without failing core media.
- [ ] Keep remote thumbnail retrieval user-initiated/optional and validate its output path.
- [ ] Run focused tests and the full suite.

### Task 3: Playlist, queue, history, settings, and GUI completion

**Files:** `download_manager.py`, `config.py`, `gui.py`, `scheduler.py`, `tests/test_download_manager.py`, `tests/test_video_features.py`.

- [ ] Test playlist select/clear, duplicate entries, independent retries/cancel/failures, settings validation/migration, history recovery, queue limit/order, and remove-versus-delete behavior.
- [ ] Add user-data path migration, validated settings for defaults/concurrency/overwrite/quality/theme/optional media/history, and atomic history writes with useful safe fields.
- [ ] Add focused Settings, Diagnostics, Playlist, History, and Downloads views using existing ttk patterns; keep Tk calls on the main thread and add shutdown persistence/worker handling.
- [ ] Verify offline GUI/model helper tests and the existing regression suite.

### Task 4: Logging, dependency, path, and packaging audit

**Files:** `config.py`, `download_manager.py`, `error_handler.py`, `gui.py`, `memory_manager.py`, `requirements.txt`, packaging scripts/spec, `.gitignore`.

- [ ] Add tests for path confinement, drive/UNC/traversal names, reserved names, overwrite policy, atomic settings migration, log redaction, and application-root discovery.
- [ ] Replace uncontrolled routine prints with module logging while retaining user-safe GUI messages; remove only proven-unused dependencies.
- [ ] Add PyInstaller configuration/build instructions and an Inno Setup installer script if available; never include or uninstall user media.
- [ ] Build/test the executable when PyInstaller exists; report the installer as prepared but not compiled if Inno Setup is unavailable.

### Task 5: Documentation, smoke procedure, and independent audit

**Files:** `README.md`, `docs/manual-smoke-test.md`, changed source/tests.

- [ ] Document only implemented features, accurate best-effort pause/resume, all runtimes, settings/log/data paths, packaging, troubleshooting, and manual integration steps.
- [ ] Run full tests, compile every Python source, application imports, available diagnostics, and repeated timing-sensitive lifecycle tests.
- [ ] Run CodeRabbit if the CLI and repository prerequisites are available; run Codex Security review under its supported host workflow; verify all reported issues against code/tests.
- [ ] Perform a read-only final audit and classify readiness with explicit evidence and remaining gaps.
