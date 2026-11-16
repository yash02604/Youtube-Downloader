# Download service boundary — Phase 3

## Ownership and dependencies
Desktop -> core.service.DownloadService -> existing DownloadManager -> yt-dlp /
HTTP / FFmpeg / Deno. The service imports no UI or backend framework. Engine
execution remains single-source. The core manager facade remains compatible.

Service owns admission, public lifecycle methods, detached state queries,
destination policy, subscription fan-out and shutdown coordination. Engine owns
network/process execution, queue scheduling, retries, generation guards, partial
files, media validation and pause/cancel mechanics. core.persistence remains the
single storage implementation, using the engine lock and validators.

Public API: add_download(url, download_path='.', **existing_engine_options),
get_download(id), list_downloads(), pause(id), resume(id), cancel(id), remove(id),
subscribe(capacity=128), unsubscribe(subscription), shutdown(timeout=5).
Subscriptions expose get_nowait(), drain(consumer, limit=64), close(), len(),
closed, dropped and resync_required. get/list return detached engine snapshots;
engine references are exposed only as an internal compatibility escape hatch.
The desktop still uses the engine for priority/configuration and history actions,
but add/query/pause/resume/cancel/remove now go through the service.

## Bounded events
Default capacity: 128, configurable from 1 to 4096. Maximum subscriptions: 64.
Publishing enqueues plain immutable DownloadEvent values; consumer code never
runs on a download worker. Progress coalesces by job ID. Full buffers drop new
progress if there is no replaceable progress for that job. Lifecycle updates
evict progress first. If a buffer holds only lifecycle updates, the oldest is
evicted and resync_required becomes True; the new transition is retained.
Consumers must fetch list_downloads() to resynchronize when that flag is set.
This does not promise lossless lifecycle delivery to indefinitely stalled clients.

Unsubscribe/close clears buffers and removes registrations. Shutdown detaches
engine callbacks and closes all subscriptions. A future HTTP adapter must close
its subscription in a disconnect/finally handler. The global subscription cap
bounds memory even if a client fails to detach. drain isolates consumer errors
on the consumer's own thread. Direct queue reads can instead be used by async
adapters. The older callback-based DownloadEvents remains an internal bridge;
external service consumers only receive bounded buffers.

## Shutdown
The first shutdown call closes service admission and starts one coordinator.
Repeated calls wait for the same completion signal. A short separate shutdown
lock allows admission to close even while an existing command is finishing.
The coordinator waits for previously admitted commands, freezes engine queue
starts, clears deferred resume requests, and requests resumable pauses. Queued
jobs become Paused. Active jobs can remain Pausing until their worker observes
the signal. Workers are never killed. Generation and completion guards remain
unchanged. Engine persistence writes resumable state through the existing atomic
writer; final cleanup persists again after workers have stopped, closes HTTP
sessions, clears dead worker references and detaches event hooks.

shutdown returns True when cleanup finishes, False when its timeout expires or
cleanup fails. shutdown_error reports a coordinator failure. After timeout the
same daemon coordinator continues waiting; further shutdown calls can wait again.
The timeout bounds caller waiting, not the lifetime of an uninterruptible yt-dlp
or FFmpeg call. Persistence still has its existing logged-write-error behavior;
True is not a new durable-storage guarantee in the face of disk failure.

Desktop closure performs the wait on a background thread and polls from Tk;
widgets remain on the UI thread. After at most five seconds of service waiting,
the window exits even if a worker remains blocked. Restart recovery normalizes
unfinished persisted states as before. Abrupt process termination cannot promise
the very latest progress byte count; valid partial files are checked on recovery.

## Destination policies
DesktopDestinationPolicy retains arbitrary local folder selection.
RootedDestinationPolicy accepts logical relative folders below a configured root.
It rejects traversal, absolute/drive-relative/UNC paths, alternate stream colons,
Windows device names, trailing space/dot aliases and control characters. Both
slash styles normalize consistently. Resolved existing symlinks/junctions that
leave the allowed root are rejected. Engine final-output checks remain intact.

This is a policy boundary, not a filesystem sandbox: an untrusted local process
could replace a directory after validation. A remote adapter must use an
application-owned root with appropriate OS permissions, dedicated persistence
paths, and restrict any restored legacy jobs to that root. It must not expose the
internal engine escape hatch or raw filesystem paths as user-controlled inputs.

## Verification
Full suite: 152 run, 151 passed, 0 failures, 0 errors, 1 skipped. The skipped
case requires real Windows symlink creation privileges unavailable to this account.
Other tests cover both Windows and POSIX escape syntax, normalized relative
paths, API lifecycle, detached snapshots, state persistence, idempotent shutdown,
bounded timeout, blocked admitted commands, cleanup, subscription limits,
5,000-event coalescing, terminal retention and slow/broken consumer isolation.
Compilation/import checks: 34 files passed. Fresh-process core.service import
confirms neither tkinter nor fastapi is loaded. Existing tests remain intact.
No live network download, throughput change, installer build or manual Windows
interaction was performed. No automated regressions were found. No measured
performance improvement is claimed.

## Readiness: READY FOR MINIMAL FASTAPI ADAPTER
The six required boundaries now exist and are exercised: reusable lifecycle API,
plain detached snapshots, bounded subscriptions, bounded shutdown waiting,
remote destination policy, and framework-free imports. This is readiness for a
minimal adapter with the deployment restrictions above, not unrestricted public
hosting. No FastAPI dependency or frontend was added.

Remaining coupling: execution and queue scheduling still live in the legacy
engine; global configuration, history defaults, storage paths and priority/history
operations remain desktop oriented. Persistence relies on manager locks and
validators. A future adapter must supply authentication, URL admission rules,
request limits, dedicated root/storage, async queue consumption and disconnect
cleanup. It must handle shutdown(False) and resync_required explicitly.

## Manual Windows checklist (not yet verified)
- [ ] Startup and interactive window while tools are detected
- [ ] Add YouTube URL and inspect metadata without UI blocking
- [ ] Start and display progress
- [ ] Pause and resume partial media
- [ ] Cancel and remove entry
- [ ] Close with queued/running work
- [ ] Reopen and verify paused/interrupted recovery
- [ ] FFmpeg merge with valid video and audio streams
- [ ] Deno detection and status
- [ ] Playlist selection and progress

## Files
Added: core/service.py, core/event_bus.py, core/destination_policy.py,
tests/test_service.py, docs/DOWNLOAD_SERVICE.md.
Modified: core/events.py, download_manager.py, gui.py. Deleted: none.
