# Performance Phase 1

This phase targets desktop responsiveness without changing download semantics.

## Changes

- Download list refresh is event-driven instead of scanning the full model every 250 ms while idle.
- Progress/status refresh requests are coalesced through the existing size-1 queue.
- A 2-second fallback refresh remains to recover from a missed notification.
- FFmpeg/FFprobe and Deno discovery now runs on a background thread at startup instead of blocking Tkinter.
- Tool-discovery results are applied on the Tk event loop through a queue.

## Validation

- Python compilation passed for all copied application modules.
- Focused GUI responsiveness tests: 5 passed.

## GitHub status

The connected GitHub integration can read `yash02604/Youtube-Downloader`, but branch creation returned HTTP 403 (`Resource not accessible by integration`), so this phase was not pushed to the repository.
