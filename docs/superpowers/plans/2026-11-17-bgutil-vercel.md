# Bounded bgutil script-provider experiment

1. Inspect the latest upstream release, requirements, script/provider version
   contract and documented client fallback. Pin plugin and source to 2.0.2.
2. Package only immutable server assets, license, frozen dependencies and Deno
   caches during the existing Vercel build. No daemon or request-time code
   downloads. Validate script imports/version without generating tokens.
3. Share one Vercel-only profile between analysis and downloads. Select Deno
   2.9.8, script provider and mweb. Observe provider invocation/validated return
   per instance without logging token contents. Desktop defaults stay intact.
4. Run the full Python and frontend gates once, compilation/import checks and
   browser smoke; review; commit and push only the feature branch. Inspect the
   cloud build and test each of the three specified public videos once.
5. If mweb is rejected before generation, test only the upstream-documented
   mweb,tv,web_safari fallback. Stop on generated-token plus LOGIN_REQUIRED.
   If two public analyses succeed, stop tuning and attempt one short 360p job
   without optional media processing. Otherwise leave the download gate closed.
6. Record exact runtime/provider evidence, test totals, readiness and commits.
   No Windows-versus-Vercel comparison, personal cookies, proxies, CAPTCHA,
   identity spoofing, static tokens or changes to processing/lifecycle/storage.
