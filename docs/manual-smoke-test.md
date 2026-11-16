# Manual and integration smoke checklist

These checks use the desktop UI and live extractors. Keep them out of the
offline unit suite. Use content you are authorized to download, save to a
temporary test folder, and remove only that test output when finished.

## One-time baseline result

The user-selected YouTube URL `https://www.youtube.com/watch?v=xvT1jH8B9AM`
was inspected with yt-dlp metadata and `Best Available`. The selector was
`bv[acodec=none]+ba/b`; yt-dlp selected format `401` (AV1, 3840×2026, 24 FPS)
plus format `251` (Opus, about 138.8 kb/s). The downloaded
`smoke_test/xvT1jH8B9AM_quality_smoke_test.mkv` was probed as AV1 3840×2026 at
24/1 plus Opus audio. FFmpeg merge uses stream copy; no video re-encode was
observed. This is a recorded real run, not an automated test.

## Checklist for a Windows desktop release candidate

1. Download a public 1080p video with the 1080p profile. Confirm the selected
   video format height does not exceed 1080 and audio exists.
2. Download a public high-resolution video with Best Available. Before
   confirming, record the selector and the selected yt-dlp video/audio format
   IDs from the app log or `info_dict`; verify both remain separate inputs.
3. Probe the final file and compare resolution, FPS, and codec names to the
   selected input formats.
4. Download Best Original Audio and verify the source codec is preserved.
5. Download MP3 High Quality and verify MP3 output; confirm the source format
   was the best available audio before conversion.
6. Pause during transfer, wait for the attempt to stop, then resume. Confirm a
   new generation starts, compatible partial data is reused where yt-dlp can,
   and stale progress does not overwrite the resumed item.
7. Cancel during transfer. Confirm the item stays Cancelled, partial files are
   retained, and it never becomes Completed from a late callback.
8. Inspect and queue selected playlist entries. Clear the selection, select a
   subset, and verify each item has independent state and failure handling.
9. Opt in to a listed subtitle language, download it, then separately verify
   subtitle embedding with FFmpeg available.
10. Request the on-demand thumbnail preview, then opt in to saving and
    embedding a thumbnail and metadata. Confirm optional failures show a
    warning while validated media remains Completed.
11. Restart while work is active. Confirm active work restores as Interrupted
    and can be resumed manually.
12. Temporarily configure a directory without FFmpeg/ffprobe. Confirm status is
    unavailable and Best Available explains the merge fallback before queueing.
13. Temporarily configure an invalid Deno path. Confirm the diagnostic reports
    unavailable and ordinary non-YouTube downloads still work.
14. Inspect a private, deleted, or malformed URL. Confirm the app reports an
    item-level error without affecting other queue/playlist entries.

For a final media probe, use the discovered executable path (or the configured
path) and inspect streams, not the filename or UI summary:

```powershell
& 'C:\path\to\ffprobe.exe' -v error -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,bit_rate -of default=noprint_wrappers=1 'C:\path\to\FINAL_FILE.mkv'
```

When testing stream-copy behavior, inspect the yt-dlp postprocessor log and the
FFmpeg command line. A normal merge/remux should use stream copy (`-c copy`)
and must not invoke a video encoder such as `libx264` or `libaom-av1`.

## Environment status

The high-quality download and ffprobe verification above have been completed.
Fresh metadata for the same video exposes format 401 (AV1, 3840×2026, 24 FPS)
and format 251 (Opus), and the app's completed-run history records these as the
separate selected inputs. A fresh ffprobe of the resulting MKV confirms AV1
3840×2026 at 24/1 plus Opus. The installed yt-dlp FFmpeg merger uses `-c copy`,
so its normal merge/remux path copies streams rather than encoding video.

The packaged app launched from a temporary copied package folder and displayed
both configured executable paths in its video-analysis dialog. Investigation found
an intermittent five-second `ffmpeg -version` timeout after an earlier
successful check. The app now reuses successful validation while both
executable files retain the same size and modification time; the rebuilt
package's log confirmed reuse and the dialog showed both paths. Queue/generation
race tests passed three repeated runs, and the removal test passed five repeats
after waiting for worker cleanup. Live pause/resume, cancellation, audio
conversion, playlist, subtitle, thumbnail, and restart workflows remain
unchecked. The project remains BETA READY pending these items.
