# Changelog

All notable changes to Argus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

[0.1.0-alpha.2]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]: https://github.com/MichaelYagi/argus/releases/tag/v0.1.0-alpha.1
