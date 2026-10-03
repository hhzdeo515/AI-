# Next.js Assistant Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent frontend and durable-task work, with integration and final review.

**Goal:** Deliver a maintainable Next.js interface over the existing AI backend with durable tasks and a verified deployable application.
**Architecture:** Next.js static export and Flask API share one origin in the default production container. Python remains one process with bounded workers, SQLite and persistent volume.
**Tech Stack:** Next.js, React, TypeScript, Flask, LangGraph, SQLite, Waitress, Docker.
**Spec:** ../specs/2026-10-03-nextjs-assistant-design.md

## Global Constraints
- Preserve Chinese copy, real AI flows, original data and access-token protection.
- Never commit credentials or user content. Use isolated data and stubbed model calls for tests.
- Next.js output directory frontend/out; Flask FRONTEND_DIR selects this build.
- No hosted serverless Python task executor. Frontend may run on Vercel only with a real cloud backend.

## Review Focus
- Page reload and network loss retain the same task instead of duplicate model calls.
- Backend restart exposes interrupted work and avoids unsafe automatic external resubmission.
- Two requests on one session cannot mix LangGraph state.
- Unauthorized users cannot query tasks or private media; HTML stays escaped.
- Camera/microphone stop on close, hidden page and unmount; large uploads give usable errors.

### Task 1: Persistent task service
Files: lg_assistant/jobs.py, progress.py, web/app.py task routes, tests/test_jobs.py.
Consumes: existing parsed chat payload, _run_chat, SQLite and graph checkpointer.
Produces: existing /api/chat/async and /api/task contracts plus GET /api/tasks and POST /api/task/retry.
- [ ] Write failing durability, deduplication, restart, session-serialization and retry tests.
- [ ] Implement persisted task state, bounded execution and explicit recovery without exposing file paths.
- [ ] Persist progress snapshots and retain old APIs.
- [ ] Run targeted and existing Python tests.

### Task 2: Next.js interface
Files: frontend/package.json, lockfile, app/, components/, lib/, hooks/, tests/.
Consumes: current Flask APIs and Task 1 endpoints; auth session returns authenticated/auth_enabled, login accepts JSON token.
Produces: frontend/out static application and type-safe API/interaction components.
- [ ] Create tests for API errors, request construction, resumable task polling and media lifecycle.
- [ ] Build complete Chinese lens/ring interface, real uploads and recordings, practice controls, results/history/knowledge.
- [ ] Add explicit loading, empty, error and interrupted states, accessible labels and responsive layout.
- [ ] Run test, typecheck and production build.

### Task 3: Integration and deployment
Files: web/frontend.py, web/auth.py, production.py, Dockerfile, compose.yaml, deployment scripts, README and deployment docs.
Consumes: frontend/out and persisted task service.
Produces: same-origin production app with secure cookie auth, deploy checks and documented Vercel frontend alternative.
- [ ] Add failing tests for frontend asset serving, auth JSON session and production behavior.
- [ ] Mount Next.js export with legacy UI fallback; add JSON login/logout/session and no secret exposure.
- [ ] Add multi-stage Docker build, persistent volume, backup and online smoke scripts.
- [ ] Verify all tests, local browser, container and clean diff; obtain independent review.
- [ ] Commit and sync; deploy and verify cloud HTTPS when credentials/target exist, otherwise state exact missing connection.
