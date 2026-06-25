# Changelog

All notable changes to Argus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.1.0-alpha.4] — 2026-06-24

### Added

- **Environments — per-user data isolation.** Each user has one or more named environments (e.g. `default`, `dev`, `prod`); all recognition data — identities, detections, enrolled faces, source images — is scoped to one environment and never visible from another. Switch instantly via the top-nav picker (the active environment is remembered across sign-out). Manage them at `/environments` (create/rename/delete). **API keys are environment-scoped** — a key reads and writes only its environment's data, regardless of the browser session.
- **Suggested people (face clustering)** — the `/clusters` page groups unlabeled faces (matching nobody enrolled) into "probably the same person" clusters by similarity, so you can name a group and enroll everyone in it at once. Faces are individually selectable: deselect a wrong face (remove), select part of a group (split), or select across groups (merge). **Dismiss** hides faces from suggestions without deleting them; **Delete** removes the crops permanently. Tunable `face.cluster_threshold` setting. Over the API: `GET /api/clusters`; name a selection via `POST /api/detections/label`. Computed on demand, stores nothing.
- **Stateless Test** — `/test` page and `POST /api/test` check whether an image contains people or objects without storing, enrolling, or matching anything; returns bounding boxes + counts, rendered with overlays in the UI. `?type=faces|objects|all`; a missing model is skipped (reported via an `available` flag) rather than erroring. **Batch variant** `POST /api/test/batch` tests many images (multipart files or JSON `image_urls`/`image_base64`) in one call; the Test page accepts multiple images.
- **Integration helpers for client systems:**
  - **`external_ref`** — an opaque, caller-owned correlation id on identities and source images. Settable on detect (`external_ref` field) and enroll, queryable (`GET /api/identities?external_ref=`, `GET /api/images?external_ref=`), settable on an existing identity (`PUT /api/identities/{id}/external_ref`), and echoed in responses. Lets a client map its own ids to Argus's without name-matching. Argus never interprets it.
  - **Change feed** — `GET /api/changes?since=<cursor>` returns identity/detection created/relabeled/deleted events for delta sync, so a client learns what changed without re-scanning. Detection events carry the source image's `external_ref`.
  - **Capabilities** — `GET /api/capabilities` reports usable detection types, active models, supported formats, pagination limits, and which integration features the build exposes.
  - **Batch operations** — `POST /api/detections/label` (relabel many, per-item results), `POST /api/detections/query` (read current state of many), `POST /api/detections/dismiss`, `POST /api/detections/delete`.
- **Object bounding boxes on the tag page** — `/tag/{id}` now draws object boxes (blue) alongside face boxes; click an object box to correct its class label via the shared label endpoint.
- **API key rename + key hint** — keys can be renamed on the Account page, and each key shows a `argus_…xxxxxxxx` hint (last 8 chars) so you can tell them apart. Keys are created with an environment selector.
- `system.auto_approve_users` setting (default `true`) — new accounts are approved immediately on sign-up with no admin gate. Set to `false` to require admin approval before the account can sign in. First registered account (the admin) is always auto-approved regardless.
- **SQLite WAL mode** — `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000` on every connection. Concurrent reads no longer block on an in-progress write; write contention retries for up to 5 seconds. Improves throughput with multiple simultaneous API clients.
- **Async detection job queue** — add `?async=true` to any `POST /api/detect/faces|objects|all` call for an immediate `{"job_id": ..., "status": "pending"}` response instead of blocking on inference. Poll with `GET /api/jobs/{job_id}`, list with `GET /api/jobs`, delete with `DELETE /api/jobs/{job_id}`. Backed by a new `jobs` table; no external queue process.

### Changed

- **User management moved from Account to the Settings page** (admin-only, under System) — approve/revoke/restore/delete accounts alongside the auto-approve toggle. Admin account actions redirect back to `/settings`.
- **All native browser dialogs replaced with in-app modals** — `alert()`/`confirm()` are gone; destructive actions use a styled confirm modal, notices use a message modal.
- The environment switcher hides on pages where it doesn't apply (Settings, Models, Account), which instead show a "Manage environments" link; the picker now closes on an outside click.
- Dashboard identity cards no longer show a references count (confusing next to detections); the count remains on the identity gallery page.

### Fixed

- **Deleting the `default` environment no longer resurrects it on restart.** The startup migration now seeds `default` only for users who have *no* environment, instead of re-adding it by name every boot — so a deliberately-deleted `default` stays gone while other environments exist.
- **Average match strategy now rebuilds the face index correctly.** Representative (centroid) embeddings are recomputed on every index build instead of only when missing, fixing empty/stale suggestions and similarity scores after switching to the Average strategy.
- Identity rename (`PUT /api/identities/{id}`) with a name that collides with another identity returns `409` and surfaces the error inline instead of failing opaquely.
- Switching environments could hit a `UNIQUE constraint` error on `identities`/`source_images` carried over from the pre-environment schema; a one-time migration recreates those tables with environment-scoped uniqueness.
- The environment-switcher button is now legible in the light theme.

---

## [0.1.0-alpha.3] — 2026-06-23

### Changed

- The identity gallery header ("N detections · M references") now updates **live** when you delete a detection, bulk-remove, or bulk-reassign — no page reload. Removing a crop decrements the detection count, and if it was an enrolled reference, the reference count too.
- **"Delete all identity data" moved from Settings to the Account page.** Identity data is per-user, but Settings is now admin-only — so the wipe action lives on Account, where every user can clear their *own* data (the `DELETE /api/identities` endpoint is already user-scoped). Wording updated to "all of your … data".
- **Settings and Models are now admin-only.** Both are instance-global (settings and the model registry are shared by every account in a single Argus instance), so only the admin (the first registered account) can view or change them. Non-admin accounts no longer see the Settings/Models nav links, are redirected away from those pages, and get `403` from `/api/settings/*` and `/api/models/*`. This prevents a secondary/test account from changing thresholds, the active model, or the match strategy for everyone.
- Every face surface now shows **match similarity** instead of face-detection confidence (det_score), which was being mistaken for identity certainty:
  - **Gallery** badge — each crop's similarity to the person's reference set.
  - **Review** card — best-match similarity (subtitle reworded from "lowest confidence first").
  - **Detect** page overlay — faces show similarity; objects keep their score (they have no similarity).
  - **Tag** page tooltip — "Name (NN% match)".
  - `GET /api/identities/{id}/gallery` items gained a `similarity` field; the detect API already returns `similarity` per face.
  - Object detections continue to show their detection score (objects have no identity/similarity).

### Fixed

- **Reassigning, rejecting, or relabeling a face now removes the previous identity's reference** for that crop. Previously, moving a face from person A to B (or rejecting it) left A's embedding behind as an orphan — inflating A's reference count *and* keeping a wrong face in A's reference set where it polluted matching. This was the main source of recurring "N detections · M references (M > N)" mismatches during normal review/correction. The old identity's representative is recomputed and the match index refreshed.
- Re-detecting with `replace=true` and `DELETE /api/images/{id}` now also remove references enrolled from the cleared crops (and refresh the match index), instead of leaving them orphaned. Previously a rescan-with-replace or source delete could leave a reference with no crop (showing e.g. "1 detection · 2 references") — and, worse, keep a deleted/wrong face in the reference set where it polluted matching.
- Startup now reconciles **orphaned references** — `face_embeddings` whose source crop no longer has a detection (left behind by older builds that deleted a detection without removing its reference). Affected identities' representatives are recomputed. Fixes "1 detection · 3 references" style mismatches.
- The cover photo (and gallery star) no longer jumps to the newest detection as more faces are matched. When no cover is explicitly set, the **oldest** detection is used as a stable default — consistently for both the dashboard thumbnail and the gallery star — instead of the most-recent one, which shifted on every new match.
- Detect with an inline `label` (human-asserted identity) now reports `similarity: 1.0` instead of the incidental match score against the prior reference set. A manually-named upload isn't a match — it's ground truth — so it no longer shows a misleading sub-100% confidence.
- Deleting a detection now also removes any reference embedding enrolled from its crop and recomputes the identity's representative, so the reference count stays consistent with the gallery (previously a deleted reference crop left an orphan embedding — count said N but only N-1 crops were marked). The cover photo was already cleared on delete via the foreign key.
- Human review actions (confirm, reassign, label) now enroll the face embedding **unconditionally** instead of gating on `face.auto_enroll_threshold`. The threshold was compared against the face-*detection* quality score (not match similarity), so ordinary faces (score < 0.92) never enrolled on confirm — meaning repeatedly confirming a person never improved their match score. The threshold now applies only to the automatic auto-confirm path (no human in the loop).
- macOS / Apple Silicon: `faiss-cpu` and `torch` (YOLO object detection) each vendor their own OpenMP runtime and segfault when loaded together. `ARGUS_DISABLE_FAISS=true` now skips faiss entirely (never imported) and uses the numpy matching fallback. Auto-enabled on macOS; Linux/CUDA is unaffected and keeps faiss. Also pins OpenMP/BLAS thread counts and enables `faulthandler` (native traceback to stderr on crash) on the native run path.

### Added

- **Facial attributes — age, gender, head pose.** Already computed by the InsightFace model packs (buffalo_l/buffalo_s/antelopev2 all bundle genderage + 3D-68 pose) but previously discarded. Now stored per face detection in a new `attributes` column, returned in the detect response and the per-image faces API, and shown in the Detect and Tag UIs (e.g. "Alice 92% · 30y · F · yaw 12°"). Read defensively — any model lacking a module simply yields `null`, never an error. No new downloads. Landmarks are not surfaced (the 5-point set is already used internally for alignment, which is all that benefits recognition).
- **`POST /api/verify` — 1:1 face verification.** "Are these two images the same person?" Takes two images (`file1`/`file2` or `image{1,2}_url` / `image{1,2}_base64`), returns `{similarity, match, threshold, face1, face2}`. Uses the highest-confidence face per image; `400` if either has no face; optional `threshold` override. Stores nothing.
- **`POST /api/identify` — 1:N identification (read-only).** "Who is each face in this photo?" One image in; returns per face the best match (`identity_id`/`label`/`similarity`, null below threshold with the best-guess score), a ranked `suggestions` list, and age/gender/pose. Stores nothing — no crops, detections, review entries, or source image. Optional `threshold` and `top_n`.
- **Account management.** Any non-admin user can now **delete their own account** (Account → Danger zone → Delete account), which removes their account and cascades all their data, then signs them out. The admin account cannot self-delete.
- **Admin user management** on the Account page (replaces the old "Pending registrations" card with a fuller "Users" card): approve pending registrations, **revoke** or **restore** access (block sign-in without deleting data), and **delete** any non-admin account. Admin accounts are protected at the store level (cannot be revoked or deleted).
- `face.match_strategy` setting — **Best match** (default; compares against every reference photo and uses the closest) or **Average** (one blended centroid per person, faster/steadier). Best match keeps enrolled photos at ~100% and recognizes people who look different across photos (age, glasses, lighting) better; the cost is indexing every reference instead of one centroid (negligible at self-hosted scale). Changing it rebuilds the match index; the gallery/tag similarity shown follows the active strategy.
- `DELETE /api/detections/{id}/enroll` — removes a crop from an identity's reference set (inverse of the existing `POST`), so the gallery's reference button is a true toggle.
- Identity gallery items now include an `enrolled` flag (whether the crop is in the reference set).
- `POST /api/detect/faces|all` now returns a `similarity` field per face — the match strength (0–1) against the matched enrolled identity, distinct from `confidence` (the face-detection quality score). Lets clients store/display the real match %.
- `DELETE /api/images/{source_image_id}` — cascade-deletes a source image and all its detections (faces + objects) and removes their crop files. The enrolled face reference set is untouched.
- `replace` flag on `POST /api/detect/faces|objects|all` (form field, JSON body, or `?replace=true`) — clears the image's existing detections of the type being run before writing new ones, making re-detection of the same image idempotent. Uses the existing content-hash source-image dedup, so clients don't need to track `source_image_id`.

---

## [0.1.0-alpha.2] — 2026-06-21

### Added

**YOLO-World (open vocabulary object detection)**
- New model type: `yolov8s-worldv2`, `yolov8m-worldv2`, `yolov8l-worldv2` — detect anything described in plain language, not limited to COCO's 80 classes
- Configurable vocabulary per settings: ~160 default classes covering all COCO + weapons, fire, smoke, license plates, face masks, extended animals, vehicles, and more
- Vocabulary autocomplete in Settings — type to search available classes, suggestions exclude items already in the list, Enter selects from list only (no freeform duplicates)
- Settings page is context-aware: shows COCO class grid for standard YOLO, YOLO-World vocabulary textarea for world models

**GPU support improvements**
- `__main__.py` auto-discovers and pre-loads CUDA shared libraries from pip-installed nvidia packages at startup — no manual `LD_LIBRARY_PATH` configuration needed
- NVBLAS excluded from preload (required config file it doesn't have, caused segfaults)
- CUDA library re-encoding skipped when YOLO-World vocabulary hasn't changed (performance)
- `Use GPU` toggle in Settings is hidden/disabled when no CUDA device is detected

**Detection improvements**
- Inline `label` field on `POST /api/detect/faces` and `POST /api/detect/all` — when provided, assigns the identity and confirms immediately, bypassing the review queue entirely. Useful for API clients that already know who is in a photo
- Base64 image input added to Detect and Enroll pages (accepts `data:image/...;base64,...` or raw base64)
- MPO image format (Multi Picture Object, used by some cameras and iPhones) now supported — first frame extracted

**Enrollment**
- Enrolling via `POST /api/faces/enroll` or `POST /api/identities/{id}/enroll` now creates a confirmed detection row, so the enrolled photo appears immediately in the identity's gallery with a thumbnail

**Review queue**
- Pending detections whose top faiss suggestion meets the auto-confirm threshold are confirmed automatically when the review queue is loaded — no more "No match found" alongside a 100% suggestion
- Review count badge in the nav bar polls every 30 seconds and clears itself when the queue empties

**Dashboard**
- Identity count badges on People and Objects tabs — shows total enrolled count, updates instantly when new identities load
- Bulk select and delete identities directly from the dashboard — hover to reveal checkboxes, select multiple, confirm deletion via modal

**Settings**
- Settings now save explicitly via a Save button per section instead of auto-saving on change
- Danger zone card at bottom of Settings — "Delete all identity data" wipes all enrolled people, detected objects, detections, embeddings, and stored images while leaving settings, API keys, and models untouched

**Health and monitoring**
- `GET /api/health` now returns `face_model` and `object_model` names (e.g. `"buffalo_l"`) instead of engine class names
- `HEAD /api/health` now supported (was returning 405)
- Health status card on Account page (admin only) — shows status, version, GPU availability, active provider, and loaded models
- Argus version shown in footer on every page

### Fixed

- Review page timestamp was rendering as raw JS template literal string instead of formatted date
- faiss "not available" warning was logged once per user on startup instead of once total; now logs once at the `build_all` call
- Settings page 500 error caused by Jinja2 `{% continue %}` (not a valid Jinja2 tag) — replaced with conditional wrapping
- `onnxruntime` (CPU) and `onnxruntime-gpu` installed simultaneously caused the CPU version to shadow the GPU one

---

## [0.1.0-alpha.1] — 2026-06-14

Initial alpha release.

### Added

**Core recognition**
- Face detection and recognition via InsightFace (RetinaFace detection + ArcFace embeddings)
- Object detection via Ultralytics YOLO (80 COCO classes)
- Embedding averaging — mean of all reference embeddings per identity stored as representative embedding for accurate matching
- faiss `IndexFlatIP` per user for fast cosine similarity search at scale; falls back to numpy if faiss unavailable
- GPU auto-detected at startup via `onnxruntime.get_available_providers()`; falls back to CPU silently

**Enrollment and detection**
- Enroll faces by name via file upload, image URL, or base64
- Detect faces and objects via file upload, image URL, or base64 — all three paths converge to the same pipeline
- Bulk detect endpoint (`POST /api/detect/bulk`) — multiple images in one request
- Auto-enroll: confirmed detections above `face.auto_enroll_threshold` (default 0.92) added to reference set automatically
- Reassign always enrolls unconditionally — human explicit label treated as ground truth

**Review queue**
- Face detections below match threshold land in a review queue with ranked suggestions from enrolled faces
- Configurable auto-confirm threshold (`face.auto_confirm_threshold`, default 0.80) — high-confidence matches skip the queue
- Actions: confirm, reject, reassign to a different identity, or dismiss
- Autocomplete on the reassign input

**Galleries and tagging**
- Justified infinite-scroll gallery per identity (face or object class)
- Crops saved at detect-time per detection bounding box — galleries show tight face/object crops, never full source images
- Cover photo selection, bulk confirm/enroll/delete within galleries
- Tag page (`/tag/{source_image_id}`) — full source image with clickable bbox overlays for labelling individual faces

**Accounts and API keys**
- Per-user accounts with username/password (scrypt hashing)
- First registered user is automatically admin; subsequent registrations require admin approval
- Multiple named API keys per user — create, revoke, delete individually
- Remember-me sessions; timezone and locale preferences per user
- Date and time displayed in user's configured locale and timezone throughout the UI

**Export and import**
- Export selected identities as ZIP (JSON manifest + crops + source images)
- Import with merge — existing identities receive additional detections and embeddings rather than being duplicated

**Settings (live, no restart required)**
- Face: match threshold, detection confidence, min face size, auto-confirm on/off + threshold, auto-enroll threshold
- Object: detection confidence, IOU threshold, enabled COCO classes
- System: crop padding, unknown detection saving, URL fetch timeout and size limit, GPU toggle
- Hot-swap face and object models without restarting — engine registry with lock around active-engine reference
- Settings cache refreshed on every `PUT /api/settings/*` call

**API**
- Full REST API — every UI action available via API
- `X-API-Key` header authentication on all `/api/*` routes
- Cursor-based pagination throughout (`?cursor=&limit=`)
- Interactive API docs at `/docs` (ReDoc); published to GitHub Pages on release

**Infrastructure**
- Single Docker container (`docker compose up`)
- Native run (`python -m app`) — same codebase, same behaviour
- `SECRET_KEY` auto-generated on first startup and persisted
- GitHub Actions: `test.yml` (lint + pytest on every push), `release.yml` (Docker image to `ghcr.io` on version tag)
- Mobile-responsive UI — hamburger nav at ≤767px

**Supported image formats:** JPEG, PNG, WEBP, BMP, GIF (first frame), TIFF, HEIC/HEIF

---

[0.1.0-alpha.4]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.3...v0.1.0-alpha.4
[0.1.0-alpha.3]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.2...v0.1.0-alpha.3
[0.1.0-alpha.2]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]: https://github.com/MichaelYagi/argus/releases/tag/v0.1.0-alpha.1
