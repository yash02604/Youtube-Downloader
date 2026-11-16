# Phase 4 implementation plan

1. Add backend-only requirements, settings and strict request/response schemas.
2. Add application factory with injected service/analyzer, isolated storage,
   cookie-free engine mode and bounded lifespan shutdown.
3. Adapt REST methods and sanitized error/snapshot responses to the service.
4. Stream existing bounded subscriptions via SSE with disconnect cleanup/resync.
5. Test offline with injected fakes and real service boundaries, run the full suite,
   compile/import checks, document local startup and deployment limits.
6. Commit locally, verify clean feature branch, then push without merging.

No frontend or download-setting changes. Core imports remain framework free.
