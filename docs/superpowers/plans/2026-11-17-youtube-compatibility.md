# YouTube compatibility diagnosis and remediation

Goal: identify the actual preview extraction failure without changing desktop
selection, media processing, lifecycle, authentication or storage architecture.

1. Add a web-only analysis wrapper with an allowlisted yt-dlp logger and
   structured public errors. Add tests for redaction and explicit categories.
   Update the frontend to trust only recognized analysis codes, preserving
   generic lifecycle conflicts. Deploy diagnostics before runtime configuration.
2. Inspect the deployed safe version/runtime/provider/extractor observations
   for canonical video Auvd4NPXHnI. Do not infer an IP block or PO-token cause
   from a broad HTTP status or missing formats.
3. Add Deno-first, Node >=22 fallback discovery for web analysis only. Retain
   desktop discover_deno behavior. Verify installed EJS visibility and actual
   provider execution with an offline controlled diagnostic. Disable remote
   component downloads in the explicit web profile.
4. Run full Python/frontend tests, lint, types, production build and browser
   smoke; push feature branch without force or merge. Test deployed health,
   normal public video, short public video and the canonical reported video.
5. Only investigate/integrate a PO-token provider if active EJS plus explicit
   upstream evidence satisfies the user's provider gate. No cookies, CAPTCHA,
   identity spoofing, proxy rotation or static tokens. Stop on verification or
   rate limiting; do not retry identical blocked requests repeatedly.
6. Only after repeatable analysis success attempt one tiny 360p download.
   Record exact evidence, versions, test totals, commits and readiness in
   docs/YOUTUBE_COMPATIBILITY.md.
