# Shared core extraction — 2026-11-16

## Architecture and dependency direction
Desktop GUI -> core.manager / core.events -> legacy download_manager engine ->
yt-dlp / FFmpeg / Deno. The engine imports core.models and core.persistence;
neither of those imports the engine. core.__init__ imports only events, avoiding
a circular import through the public facade.

- core/models.py owns DownloadPriority, DownloadJob, the runtime Download subclass,
  and the existing explicit job serialization schema. DownloadJob contains domain
  state; Download adds worker references, events, process and slot controls.
  to_dict/from_dict round-trip the legacy schema, ignoring unknown fields and
  requiring textual identity, URL and destination. Deserialization does not grant
  filesystem or URL access: recovery performs those safety checks separately.
- core/persistence.py owns atomic JSON writes, history loading/saving, legacy
  migration, state saving and restart recovery. It is a mixin whose host supplies
  lock, jobs, storage paths, serialization and safety validators. Atomic writes
  preserve the previous file on replacement failure and clean temporary files.
- core/events.py owns immutable plain-data events and subscriptions. kind remains
  compatible; event_type exposes the same value. Payloads include ID, status,
  stage, filename, byte counts, speed, ETA, progress, error fields, playlist title,
  index/count and timestamp. Progress snapshots are O(1) job lookups under lock.
- core/manager.py remains the public headless facade to the existing single engine
  and analyzer, with create_manager returning manager and event subscription hub.
  Desktop now imports its manager through this facade.
- download_manager.py retains network execution, scheduling, lifecycle generation
  protection, URL/filename security checks and state snapshot access.

## Event contract
Mappings support queued, metadata_started, metadata_ready, download_started,
progress, pausing, paused, resumed, cancelling, cancelled, processing, merging,
converting, completed, failed and interrupted. Existing status strings are kept
in status. Error maps to failed; Fetching info maps to metadata_started.
The adapter does not manufacture metadata_ready or resumed transitions when the
engine does not notify them; support for those names is tested as mapping only.
Subscribers execute synchronously on the notifying worker and must enqueue work
and return promptly. Desktop continues to use its bounded refresh queue.
Callbacks are not included in event payloads. No per-event logging is added.

## Compatibility and proof
On-disk state/history list schemas are unchanged; legacy migration preserves its
source and never replaces an existing destination. Recovery retains partial-file
size checks, safe filenames, redaction and Interrupted normalization. Existing
imports of Download and DownloadPriority from download_manager remain valid.
Five extracted storage/recovery methods are AST-identical to baseline; migration
only changes its atomic-write owner reference to DownloadPersistence.

Focused boundary/event validation passed before the final run. Final full suite:
139 passed, 0 failures, 0 errors, 0 skipped. Compilation/import checks passed for
30 Python files. Fresh-process core import loads neither tkinter nor fastapi.
Tests include schema roundtrip/defaults, malformed identity input, atomic write
failure, history/state recovery, corruption, migration, lifecycle mapping, event
errors/progress, subscription delivery and unsubscription. Existing HTTP,
playlist, stale-worker, quality and GUI responsiveness regressions remain green.

## Regressions and limitations
No automated regressions discovered. No network download or Windows manual UI
exercise performed; installer was not rebuilt. No performance improvement is
claimed. No telemetry/dependencies were introduced. Runtime deserialization is
not a security policy for remote users. The domain record does not itself
validate every legacy optional field; engine recovery retains its validation.

## FastAPI readiness: PARTIALLY READY
A single UI-independent engine, serializable events, models and reusable storage
now exist and load without UI frameworks. Remaining work before a minimal remote
adapter: rooted destinations, service lifetime/shutdown, bounded per-client event
queues, subscriber detachment from long-lived engines, richer lifecycle
notifications, and validation of API inputs. Global config and application-data
policy remain desktop oriented. No FastAPI or frontend was added.

## Files changed
Modified: download_manager.py, gui.py, core/events.py, tests/test_core_events.py.
Added: core/models.py, core/persistence.py, tests/test_core_boundaries.py,
docs/CORE_EXTRACTION.md. Deleted: none.
