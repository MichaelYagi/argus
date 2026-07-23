# Changelog

All notable changes to Argus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0-alpha.19] — 2026-07-22

### Added

- **`has_manual_detections` filter on image list.** `GET /api/images`, `GET /api/images/count`, and `GET /api/images/ids` all accept `has_manual_detections=true` to narrow results to images that contain at least one manually drawn detection. The Images page filter bar exposes this as the "Manually tagged faces" preset.

### Changed

- **`image_tags` renamed to `scene_tags` throughout.** The image-level keyword array produced by tagger engines (RAM++) is now called `scene_tags` in all API responses, the database column, store functions, engine attributes, and templates. The DB migration renames the existing column via `ALTER TABLE source_images RENAME COLUMN image_tags TO scene_tags` on upgrade; new installs use the correct name from schema creation.
- **API endpoint cleanup.** Consolidated and renamed several routes for consistency:
  - `GET /api/source-images` (and `/count`, `/ids`) → `GET /api/images` (and `/count`, `/ids`); `external_ref` lookup merged as an optional query param on the same endpoint.
  - `POST /api/faces/scan` → `POST /api/detect/scan`.
  - `GET|DELETE /api/face_embeddings/{id}` → `GET|DELETE /api/face-embeddings/{id}`.
  - `POST /api/images/{id}/tag` → `POST /api/images/{id}/label`.
  - Deleted orphaned `POST /api/images/search` (no UI or API consumers).

---

## [0.1.0-alpha.18] — 2026-07-20

### Added

- **Regression tests for manual bbox and Android compositor invariants.** API tests cover `POST /api/images/{id}/detections` (happy path, validation rejections, `source='manual'` persisted) and `DELETE /api/detections/{id}` (removes row, 404 on unknown, cross-user isolation). Template guard tests in `test_templates.py` assert CSS/JS invariants that prevent Android bbox and desktop image-drag regressions: `transform: translateZ(0)` on `#tag-wrap`, `will-change: transform` on `.t-box`, no `{ passive: false }` listeners, no `draggable` attribute on `#tag-photo`, `document.addEventListener('dragstart')` present, and `renderBoxes()` rAF retry intact.

### Changed

- **Manual bbox popup simplified.** Manually-drawn bboxes show only the name input, Apply, Cancel, and Delete — "Unidentify" is removed. Manual bboxes are user-asserted and never auto-matched, so there is nothing to unidentify.

---

## [0.1.0-alpha.17] — 2026-07-20

### Added

- **`source_external_ref` in identity gallery response.** `GET /api/identities/{id}/gallery` items now include the source image's `external_ref`, so a client can correlate a gallery crop back to its own record without a separate lookup.
- **Delete button for manually-drawn bboxes.** Clicking the label popup's Delete button on a manual bbox calls `DELETE /api/detections/{id}` and removes it from the DOM immediately. Auto-detected bboxes are not deletable from the tag page — only manually drawn ones show the Delete button.
- **Visual distinction for manually-drawn bboxes.** Manual bboxes render with a dashed border (`.manual { border-style: dashed }`) and carry `data-source="manual"` so client-side logic can distinguish them from auto-detected results.

### Fixed

- **bbox overlays invisible on Android Chrome.** Two root causes: (1) `img.clientWidth === 0` when `onload` fires on slow hardware — fixed with a `requestAnimationFrame` retry loop in `renderBoxes()`. (2) Android Chrome's GPU compositor dropping absolutely-positioned children — fixed by adding `transform: translateZ(0)` to `#tag-wrap` and `will-change: transform` to `.t-box`, and by ensuring no `{ passive: false }` touch listeners, no `draggable` attribute on `#tag-photo`, and no `dragstart` listener on elements inside `#tag-wrap`. A `reposBoxes()` function handles resize when the Android address bar slides in/out.
- **Desktop image drag during bbox drawing.** Click-dragging to draw a bbox on desktop triggered the browser's HTML5 image drag, moving the photo instead of drawing. Fixed with `document.addEventListener('dragstart', e => { if (drawMode) e.preventDefault(); })` inside the draw-mode IIFE — `dragstart` never fires on Android touch, so this is desktop-only without side effects.
- **Draw box button state not reset after drawing.** After a successful draw the button text and accent color were not restored to "Draw box" state. `finishDraw()` now resets `textContent`, `borderColor`, and `color` to match `exitDrawMode()`.
- **`NOT NULL` constraint on manual bbox save.** `insert_detection` rejected manual bboxes because confidence is not meaningful for user-drawn boxes. The column is now nullable; manual rows set `confidence = NULL`.
- **`identity.updated` webhook not firing after manual bbox.** `POST /api/images/{id}/detections` now fires `identity.updated` with `action: "detection_added"` after saving the detection, consistent with the auto-detect paths.
- **Variable shadowing in `tag.py`.** A local loop variable shadowed an outer scope variable, producing stale data in certain tag-page responses.

---

## [0.1.0-alpha.16] — 2026-07-20

### Added

- **Persistent log files.** Argus now writes log output to rotating files under `DATA_PATH/logs/` (configurable via `LOG_PATH`). The log viewer in Settings loads from files when the in-memory buffer is empty or exhausted, giving access to logs across restarts. `system.log_buffer_size` controls the in-memory ring buffer; files are capped by rotation policy.
- **Manual bbox drawing on the tag page.** A "Draw box" button enters crosshair mode; click-drag on desktop or 500 ms long-press followed by a drag on mobile draws a bounding box over a face InsightFace missed. On release, the standard label popup appears — type a name and Apply to save. The backend crops the drawn region, runs InsightFace on it (best-effort; saves without embedding if no face is found), creates the identity if needed, enrolls the embedding, and returns the detection. Schema changes: `detections.confidence` is now nullable; a new `detections.source` column (`'auto'` | `'manual'`, default `'auto'`) distinguishes drawn from detected bboxes. New endpoint: `POST /api/images/{source_image_id}/detections`.
- **Review queue tabbed layout.** "Suggested matches" and "No match" sections are now tabs rather than side-by-side columns. Each tab shows a count badge. Switching tabs is instant (no re-fetch). This matches the practical workflow: users typically work one section at a time.

### Changed

- **Detect returns cached results on re-submit.** Re-detecting an image that already has results returns the stored detections immediately without re-running inference — no more duplicate identity rows from accidental re-submits. Add `?force=true` to bypass the cache and re-run inference without clearing existing results (`replace=true` still clears first then re-runs as before).

### Fixed

- **Source image files correctly reference-counted on delete.** Deleting a source image or running auto-discard now checks whether any other `source_images` row (across all users and environments) still references the same file path before removing the file from disk. Previously, a multi-user or multi-environment setup could delete a file still needed by another row.

---

## [0.1.0-alpha.15] — 2026-07-19

### Added

- **Gallery — last-viewed thumbnail indicator.** When returning to any gallery (Images, identity gallery, Unidentified) from a tag page, the thumbnail of the last tag page visited is highlighted with a 2-second fading accent-color border ring. Applied via a `.last-viewed` CSS class and `::after` pseudo-element so it does not interfere with the existing `.selected` box-shadow.
- **Tag page — adjacent image preloading.** When Prev/Next arrows are visible, the adjacent source images are fetched and preloaded with `new Image()` in the background immediately after the arrows render. Backed by a new `GET /api/images/{source_image_id}/url` endpoint. Navigation to the next or previous image feels instant because the image data is already in the browser cache.
- **Review queue — "View in image" link.** Each review card (both Suggested matches and No match sections) now includes a "View in image" link that opens the full tag page for that source image with the detection bbox focused and highlighted. The Back button on the tag page returns directly to `/review`. Previously the only way to see context beyond the crop thumbnail was the source-image zoom modal.
- **`GET /api/images/{source_image_id}/url`.** Returns `{"image_url": "..."}` for the given source image. Scoped to the caller's user and environment. Used by the tag-page preload; available to API clients as a lightweight alternative to `GET /api/images/{id}/faces` when only the image URL is needed.

### Fixed

- **Tag page Prev/Next dead-end from identity gallery and Unidentified page.** The breadcrumb IIFE checked `referrer === /images` to decide whether to enter gallery-nav mode. Referrers from `/identities/{id}` and `/unidentified` did not match, so the IIFE fell into the else branch and immediately cleared `argus_nav_ids` — one tag visit showed arrows, the next had none. The check now matches any same-origin referrer whose path equals the saved `argus_nav_back` URL's path, covering all gallery types generically.
- **Identity gallery Prev/Next looping on the same image.** The nav-ids list was built from `allItems.map(i => i.source_image_id)`, where `allItems` is a detection list — multiple crops from one source image produced duplicate IDs. `indexOf(CUR)` found position 0, next was position 1 (same ID), and clicking Next navigated back to the same tag page. The list is now deduplicated with `[...new Set(...)]`.
- **Gallery scroll restoration unreliable on back-navigation.** `history.scrollRestoration` defaults to `'auto'`, causing the browser's own scroll restoration to fire asynchronously and overwrite the `window.scrollTo` calls in the gallery's rAF/setTimeout restore path. Set `history.scrollRestoration = 'manual'` in `gallery-framework.js` and `gallery.js` so the galleries own scroll timing entirely. The inner nested `requestAnimationFrame` was replaced with `setTimeout(0)` (fires in the same task after layout is committed, after any browser-triggered scroll restoration completes).
- **Unidentified page — stale nav state shown as Prev/Next on tag page.** Clicking "Tag" on the Unidentified page previously set `argus_nav_ids`, causing Prev/Next arrows to appear on the resulting tag page. The click handler now explicitly removes all three nav keys (`argus_nav_ids`, `argus_nav_back`, `argus_nav_depth`), clearing any stale state from a prior gallery visit.

---

## [0.1.0-alpha.14] — 2026-07-18

### Added

- **Images page — sort.** Sort dropdown with four options: Newest first (default), Oldest first, Most detections, Fewest detections. Sort is reflected in the URL and bookmarkable.
- **Images page — multi-identity filter.** The identity filter now supports multiple people simultaneously (AND semantics — images must contain all selected identities). Up to five identity chips can be added.
- **Images page — "No crops" filter.** New filter option showing only source images that have no detection crops on disk.
- **Images page — result count.** Live count of images matching the current filters, shown inside the identity filter field. Updates on Apply, Clear, and popstate.
- **Images page — Select all.** Button above the gallery selects all images matching the current filters (including pages not yet loaded), using a new `/api/source-images/ids` endpoint. Button hides after selecting; reappears after clearing selection via the batch bar.
- **Images page — date range timezone conversion.** The since/until date picker now converts the picked local date to UTC using the user's configured timezone (`Date & Time → Timezone` in Account settings) before sending to the API. Previously the filter treated picked dates as UTC, which caused off-by-one-day errors for users outside UTC.
- **`GET /api/storage`.** New endpoint returning the size of the Argus data directory (`DATA_PATH`) in bytes and human-readable form. Backed by the same 5-minute cached scan as the storage stat on the identities page.
- **`GET /api/source-images/count`.** Returns the total count of source images matching a given filter set (same parameters as `GET /api/source-images`). Used by the Images page result counter.
- **`GET /api/source-images/ids`.** Returns all source image IDs matching a given filter set with no pagination. Used by the Images page select-all to select across unloaded pages.

### Changed

- **Storage stat scans full data directory.** `_compute_storage()` previously summed only the `crops/` and `sources/` subdirectories. It now scans the entire `DATA_PATH` directory, so models, the database, and any other Argus-managed files are included in the storage figure.
- **Images page moved under Dashboard in navigation.** Previously a sub-item under Review; now a sub-item under Dashboard in both desktop sidebar and mobile menu. The page is a general browse/search/bulk-delete tool, not a review workflow tool.
- **`GET /api/source-images` `identity_id` now repeatable.** The parameter was a single integer; it now accepts multiple values (`?identity_id=1&identity_id=2`) with AND semantics. Single-value callers are unaffected.

### Internal

- `_source_images_inner()` CTE builder extracted from `list_source_images` and shared by the new `count_source_images_filtered` and `list_source_image_ids` store functions to eliminate query duplication.
- `get_dashboard_stats` returns combined all-environment totals (`all_people`, `all_objects`, `all_images`, `all_detections`, `all_unidentified`, `all_pending_review`) alongside current-environment counts. Not yet surfaced in the dashboard UI.

---

## [0.1.0-alpha.13] — 2026-07-15

### Added

- **Compare page (`/compare`).** New read-only tool: upload a reference image and one or more target images, and Argus highlights which faces from the reference appear in each target. Nothing is stored — embeddings are computed on the fly and discarded. Accessible from the nav.
- **Inference resize (`system.max_inference_size`).** Images are downscaled before being sent to the detection models when their longest edge exceeds `system.max_inference_size` (default 1920px). Bounding boxes are scaled back to original-image coordinates before storage, so crops and overlays remain accurate. Reduces memory pressure and speeds up inference on large photos without affecting stored results.
- **Batch YOLO inference in bulk detect.** Object detection in `POST /api/detect/bulk` now runs all images in a single YOLO forward pass via `infer_objects_batch`, rather than one image at a time. Significantly faster for multi-image bulk jobs.
- **`face_embeddings.confidence` column.** Each enrolled face embedding now stores the detection confidence from the source detection. Used by the `topk_weighted` matching strategy to weight reference embeddings. Existing rows default to `0.5`.
- **Incremental face index update (`update_identity`).** Enrolling or unenrolling a face now updates only the affected identity's centroid in memory rather than rebuilding the entire index. Full rebuilds still occur on model swap, environment change, and explicit rebuild calls.
- **Test page name filter.** Face detection results on the `/test` page can be filtered by name to focus on a specific person in a crowded result set.
- **Images page "No tagged people" filter.** Added a filter option that shows only source images with no confirmed (non-rejected) face identities.

### Changed

- **Review queue: "No, not [name]" and "Dismiss" now have distinct outcomes.**
  - **"No, not [name]"** — marks the detection `rejected`, clears its identity, and keeps it in the review queue under the "No match" section. The face stays there until explicitly dismissed.
  - **"Dismiss"** — removes the face from the review queue entirely and moves it to the Unidentified page, where it can be re-labeled.
  - Previously both actions called the same reject endpoint and removed the card from the queue, losing the face entirely.
- **Rejected detections clear `identity_id`.** `reject_detection` now sets `identity_id = NULL` alongside `review_status = 'rejected'`. Previously the wrong-match identity was kept, which caused rejected detections to show as labeled on the tag page and in gallery counts.
- **Dismissed faces excluded from `scan_unidentified` and Suggested People.** Unidentify (`reviewed_at` signal) prevents dismissed faces from being re-suggested by the background scan or re-clustered in Suggested People. Previously a dismissed face would bounce back to Suggested matches the next time any face was labeled.
- **Default face matching strategy changed back to `best`.** The `best` strategy (one vector per reference photo, highest cosine similarity wins) is both faster and more accurate in practice than `topk_weighted` for typical enrollment sizes. `topk_weighted` remains available.
- **Images page filter bar labels.** "Type" renamed to "Filter", "All types" renamed to "Unfiltered".
- **`idx_detections_identity_env_thumb` index added.** Covers the `(identity_id, user_id, environment_id, detected_at, id)` columns used by identity gallery and cover queries. Noticeably faster identity page loads on larger databases.

### Fixed

- **`POST /api/review/{id}/unidentify` returned 500.** The `review_status NOT NULL` constraint was violated because `unidentify_detection` set `review_status = NULL`. Now sets `review_status = 'pending'`.
- **Rejected detections showed as labeled (green) on tag page.** With `identity_id` now cleared on reject, the tag page correctly displays them as unlabeled (orange, no name chip).
- **Dismissed faces reappeared in Suggested matches.** After being dismissed via the review queue, faces with `identity_id IS NULL` were picked up by `scan_unidentified` and re-suggested to the same person. Fixed by recording `reviewed_at` at dismiss time and excluding `reviewed_at IS NOT NULL` faces from the scan.
- **Review queue 500 on load.** `d.review_status` was missing from the `get_review_queue` SELECT list but referenced in the auto-confirm pass.
- **`_connect` excessive DEBUG logging.** A `DEBUG` log was emitted on every database connection open, producing thousands of lines per hour under normal load. Now only logs a `WARNING` when connection setup takes more than 50 ms.

### Internal

- `face_index._centroids` module-level cache stores per-identity centroids so incremental updates don't require a full DB read.
- `store.get_reference_embeddings_with_confidence` simplified: confidence is read from `face_embeddings.confidence` directly (no JOIN needed to compute it).
- `store.get_embeddings_for_identity` added for single-identity centroid recomputation.
- `resize_for_inference(img, max_size)` helper in `image_input.py` returns `(img, scale)` and is used by both the detect pipeline and the compare endpoint.
- Review queue query now includes `d.review_status` in the SELECT and the WHERE condition covers `pending+identity`, `pending+no-identity+reviewed_at IS NULL` (fresh unmatched), and `rejected` — excluding dismissed faces (`pending+no-identity+reviewed_at IS NOT NULL`).

---

## [0.1.0-alpha.12] — 2026-07-10

### Added

- **Per-item `external_ref` in bulk detect.** The JSON body for `POST /api/detect/bulk` now accepts `{"images": [{"url": "...", "external_ref": "...?"}, ...], "type": "..."}`. Each item's `external_ref` is stored on the source image, included in the `detection.created` webhook payload, and returned in the per-item result. The legacy `image_urls` flat array still works (external_ref is null for those). Multipart form accepts a parallel `external_refs` JSON array (by index). Previously bulk always fired `detection.created` with `external_ref: null`.
- **`thumbnail_updated` action on `identity.updated`.** `PUT /api/identities/{id}/cover` now fires `identity.updated` with `action: "thumbnail_updated"` and a `thumbnail_url` field pointing to the new cover crop. Previously the cover endpoint fired no webhook.
- **`detection_id` in `embedding_added`/`embedding_removed` payloads.** `identity.updated` webhook events for enrollment changes now include the detection that triggered the enrollment. Set to `null` when an embedding is deleted directly via `DELETE /api/face_embeddings/{id}` (no associated detection in that path).
- **`embeddings_model_not_found` stat in import response.** When importing face embeddings whose model name does not exist on the target system, those embeddings are now counted in a new `embeddings_model_not_found` stat and skipped — previously they were silently inserted with `model_id = NULL`, making them invisible to the matching index and miscounted as `embeddings_imported`.

### Fixed

- **`embedding_added`/`embedding_removed` webhooks now fire from all enrollment paths.** Previously only the explicit `POST /api/detections/{id}/enroll` and `DELETE /api/detections/{id}/enroll` API endpoints fired these events. Auto-confirm, confirm, reassign, label, bulk review, and detect-with-label were all silent. Centralized in `enroll_from_detection` so every path is covered.
- **`DELETE /api/face_embeddings/{id}` now fires `identity.updated / embedding_removed`.** Was missing entirely.
- **Review queue badge count now filters `ignored = 0`.** `count_pending_review` included ignored (dismissed) detections, so the badge could show N while the queue showed empty. Badge now matches queue.
- **Face index dimension mismatch no longer 500s during model hot-swap.** If a query vector's dimension differs from the index (possible in the brief window when swapping to a model with a different embedding size), the numpy path now returns `[]` with a warning log instead of propagating a `ValueError` as a 500.
- **Startup identity purge scoped per user+environment.** The previous `DELETE FROM identities WHERE id NOT IN (SELECT DISTINCT identity_id FROM detections)` ran unscoped across all users, risking deletion of another user's freshly-created identity. Replaced with a `NOT EXISTS` variant that matches on both `user_id` and `environment_id`.
- **XSS in nav-search dropdown.** Identity labels were rendered via `innerHTML` string concatenation. Now escaped with `_esc()`.
- **XSS in dashboard identity cards.** `makeCard()` rebuilt using DOM methods (`createElement`, `textContent`) instead of `innerHTML`.
- **Missing identity ownership checks in three batch endpoints.** `POST /api/review` bulk reassign, `POST /api/review/label` batch label, and `POST /api/images/{id}/tag` were missing ownership checks — a caller could assign a detection to an identity belonging to a different user or environment. All three now verify the target identity exists in the caller's scope before writing.
- **`_run_bulk_job` webhook fire outside per-image try/except.** A detection error could skip `detection.created` for that item; also `store.update_job` was called outside the try so a job failure could leave progress uncounted. Both are now inside the try block.
- **`_save_crop` degenerate bbox guard.** When a detection bbox origin exceeds image dimensions (e.g. from a corrupt model output), the padded crop could produce `x2 <= x1` or `y2 <= y1` after clamping, creating a zero-size image. Now clamps to a minimum 2-pixel slice.
- **`_run_objects` falsy guard for empty tag list.** `if image_tags:` treated an empty list (valid output) as absent and skipped the `store.set_source_image_tags` write. Changed to `if image_tags is not None:`.

### Internal

- Webhook documentation page updated: `thumbnail_updated` action added to the `identity.updated` description and payload examples; `embedding_added`/`embedding_removed` examples updated to show `detection_id`; `null` case for direct embedding deletion documented.
- Add webhook modal now starts with all event checkboxes unchecked (previously pre-checked `detection.created` and `job.done`).

---

## [0.1.0-alpha.11] — 2026-07-10

### Added

- **Free disk space on dashboard.** `GET /api/stats` now returns `storage_free` and `storage_free_bytes` alongside the existing `storage` and `storage_bytes` fields. The dashboard shows a "Storage free" stat card when the value is available.
- **`sidecar_reachable` in `GET /api/health`.** When running in two-container mode, the health response now includes `"sidecar_reachable": true/false` as a distinct field, so callers can tell whether the sidecar is up regardless of whether a model is loaded.
- **`identity.created` webhook event.** Fires on every new identity creation path: enroll, detect with a new label, tag page, review reassign, batch label, and `POST /api/identities`. Payload: `identity_id`, `label`, `type`, `external_ref`.
- **`identity.updated` webhook event.** Fires on rename (with `label` and `old_label`), embedding added/removed (`action: "embedding_added"/"embedding_removed"`), and external_ref change (`action: "external_ref_updated"`). Subscriptions for `identity.updated` are now accepted.
- **`detection.deleted` webhook event.** Fires after a bulk-delete from the review queue. Payload: `detection_ids` (array), `count`.
- **`detection.created` from async jobs.** The `detection.created` event now fires from async single-image and bulk-image jobs (`?async=true`) and from `POST /api/images/{id}/reprocess`. Previously only fired from the synchronous detect endpoints.
- **`model.activated` webhook event.** Fires to all active subscribers whenever a model is hot-swapped via `PUT /api/models/{id}/activate` (system-wide broadcast, not scoped to a single user/environment). Payload: `model_id`, `name`, `type`.
- **`bbox` in more API responses.** Gallery (`GET /api/identities/{id}/gallery`), rejected-detections list, `GET /api/detections/{id}`, review queue items, and `GET /api/identities/unknown` all now include `"bbox": {"x", "y", "w", "h"}`.
- **`attributes` in `GET /api/identities/unknown`.** The unknown detections list now includes the parsed `attributes` dict alongside `bbox`.
- **`cover_detection_id` in identity responses.** `GET /api/identities` and related endpoints now include `cover_detection_id` in each identity object.
- **`image_tags` and `external_ref` in image responses.** `GET /api/images`, `GET /api/images/{id}/faces`, `POST /api/images/search`, and `GET /api/images?external_ref=` now include `image_tags` (parsed array) and `external_ref` in every item.
- **`detected_at` in `GET /api/images/{id}/faces`** — was previously missing from the per-detection row in that response.
- **`confidence` and `bbox` in rejected-detections list** (`GET /api/identities/{id}/rejected`).
- **`image_tags` chips on `/images` page.** Source image thumbnails show up to three tag chips overlaid in the bottom-left corner; "+N" suffix when more exist. Full tag list shown as a tooltip.
- **Webhook UI lists all 9 supported events.** The event-subscription checkboxes in the Create/Edit webhook modal now include all current events: `detection.created`, `detection.labeled`, `detection.deleted`, `identity.created`, `identity.updated`, `identity.merged`, `identity.deleted`, `job.done`, `model.activated`.
- **Settings sliders for all threshold settings.** `face.auto_confirm_threshold`, `face.auto_enroll_threshold`, and `face.cluster_threshold` now render as sliders on the settings page (were plain text inputs).
- **Dashboard identity thumbnail auto-refreshes on back-navigation.** When returning to the dashboard from an identity gallery page (via browser back or bfcache restore), the thumbnail for that identity is refetched in case the cover photo changed.

### Fixed

- **`GET /api/jobs` was environment-scoped incorrectly.** `store.list_jobs(user_id, environment_id)` was passing `environment_id` as the positional `limit` argument, capping results at 1–5 rows and ignoring environment isolation. Fixed with a keyword argument; `GET /api/jobs`, `GET /api/jobs/{id}`, and `DELETE /api/jobs/{id}` are now fully environment-scoped.
- **`ignored` flag not cleared on label or restore.** `store.label_detection` and `store.restore_detection` previously left `ignored = 1` intact when relabeling or un-rejecting a detection. Both now set `ignored = 0` in the same UPDATE.
- **Ignored detections leaked into review queue and gallery.** `get_review_queue` and `get_identity_gallery` were missing `AND d.ignored = 0` filters. Dismissed faces no longer reappear in review or in a person's gallery after being ignored.
- **`delete_face_embedding` was not environment-scoped.** The DELETE query joined identities by `user_id` only, allowing a caller in one environment to delete a reference embedding that belonged to another environment's identity under the same user. Now filters by both `user_id` and `environment_id`.
- **`delete_environment` left orphaned webhooks, API keys, and change-feed entries.** These tables had no FK cascade to `environments`; deleting an environment left those rows behind. `store.delete_environment` now explicitly deletes all three.
- **`delete_identity` detection queries were not environment-scoped.** The crop-path SELECT, source-image SELECT, and detection DELETE inside `store.delete_identity` all lacked `AND environment_id = ?`, allowing cross-environment data deletion under the same user.
- **`export_identity_data` and `import_identity_data` now environment-scoped.** Export verifies identities belong to the caller's environment; import scopes new identity rows and face_embeddings inserts to the caller's environment (previously used a bare default fallback with no scoping).
- **Review queue auto-confirm no longer fires webhooks silently.** When a face is auto-confirmed during `GET /api/review/queue`, `detection.labeled` is now fired for each auto-confirmed detection.
- **Review queue cursor could point past actual results after auto-confirm.** If all items on a page were auto-confirmed, the `next_cursor` was built from the pre-filter list (`items`) instead of the post-filter kept list, producing an empty page with `has_more: true` and immediate re-request loop. Fixed to use the last item in `kept`.
- **`reject` and `unidentify` endpoints now fire `detection.labeled`.** Previously these review actions mutated the detection without sending a webhook. Both now fire `detection.labeled` with `identity_id: null` and `label: null`.
- **Bulk-review `reject` action now fires `detection.labeled`** per rejected detection (was silently missing).
- **Identity cover ignores rejected detections.** `list_identities_summary`, `get_identity_with_counts`, and `get_oldest_detection_id` no longer select rejected detections as the default cover; cover subqueries now filter `review_status != 'rejected'`. Also adds `ORDER BY detected_at ASC, id ASC` for deterministic selection.
- **Search cover subquery scoped to environment.** The fallback cover-crop subquery in `search_identities` previously could pick a crop from a different environment. Now adds `AND environment_id = i.environment_id` and excludes rejected detections.
- **`GET /api/detections/{id}` no longer leaks internal fields.** Previously returned a raw `dict(det)` with `user_id`, `environment_id`, `model_id`, `ignored`, and `reviewed_at` included. Now returns an explicit shaped response with `attributes` parsed from JSON and `bbox` as a nested object.
- **Webhook CRUD is now fully environment-scoped.** `GET/PUT/DELETE /api/webhooks/{id}`, `GET /api/webhooks/{id}/deliveries`, and `POST /api/webhooks/{id}/test` all verify that the webhook's `environment_id` matches the caller's environment (previously only checked `user_id`).
- **`rename_identity` now fires `identity.updated`.** Previously renaming an identity did not send a webhook. Now fires with `action: "renamed"`, the new `label`, and `old_label`. Also returns 404 immediately if the identity doesn't exist before attempting the rename.
- **`create_identity` via `POST /api/identities` now fires `identity.created`** (was missing; only detect/enroll paths fired it).
- **`set_external_ref` now fires `identity.updated`** with `action: "external_ref_updated"`.
- **`POST /api/faces/enroll` (new face) now fires `identity.created`** and `enroll_existing` fires `identity.updated` with `action: "embedding_added"`.
- **`POST /api/identities/{id}/cover` validates detection ownership.** Previously set the cover without checking whether `detection_id` belongs to the specified identity. Now returns 400 if the detection exists but belongs to a different identity.
- **`job.done` payload `status` field normalized.** Async jobs now report `"status": "done"` (not `"complete"`) in both the stored result and the `job.done` webhook payload, matching what `GET /api/jobs/{id}` returns.
- **Activity feed returned oldest-first in some paths.** `GET /api/activity` no longer reverses the event list (the buffer already yields newest-first); events are now consistently newest-first.
- **Environment switcher now redirects to `/` after switching.** Previously redirected to `referer`, which could be a page in the old environment (e.g. a specific identity gallery). Now always lands on the dashboard.

### Internal

- **Modal scroll lock.** `lockScroll()` and `unlockScroll()` (reference-counted, globally available) prevent the page from scrolling behind an open modal. All existing modals — confirm, message, rename, merge, export, key-rename, reprocess, env-modal, about, settings/logs, and webhook create/edit — now call these. `overscroll-behavior: contain` added to all scrollable modal interiors. `body.modal-open { overflow: hidden }` added to the stylesheet.
- **`_VALID_EVENTS` in `webhooks.py` is the single source of truth.** `main_pages.py` `valid_events` list is now synced to match (both have all 9 events); the webhook UI and documentation page reflect the same set.
- **`get_or_create_identity` now returns `(identity_id, was_created: bool)`.** All call sites (detect, review, images, enroll, tag, batch) unpack the tuple and conditionally fire `identity.created` only when `was_created=True`.
- **`fire_broadcast(event, payload)` in `webhook.py`.** System-level events that are not user/env scoped (i.e. `model.activated`) fire to all active subscribers across all users and environments via a new `store.list_webhooks_for_event(event)` query.
- **`store.list_source_images` and `store.search_source_images` now SELECT `image_tags` and `external_ref`** — previously omitted despite those columns existing in the table.
- **`store.get_image_detections` now SELECTs `detected_at`** — was missing from the column list.
- **`store.get_identity_gallery` now SELECTs `bbox_x/y/w/h`** — was missing.
- **`store.get_unknown_detections` now SELECTs `bbox_x/y/w/h` and `attributes`** — both were missing.
- **`store.get_review_queue` now SELECTs `bbox_x/y/w/h`** — was missing.
- **`store.get_rejected_detections` now SELECTs `confidence` and `bbox_x/y/w/h`** — both were missing.
- **`_compute_storage` now returns `(used_str, free_str, used_bytes, free_bytes)`** — expanded return type; `_cached_storage` and callers updated accordingly.
- **`_CreateBody` and `_RenameBody` in `identities.py` use Pydantic `Field(max_length=…)` constraints** — `label` capped at 200 chars, `external_ref` at 500.

---

## [0.1.0-alpha.10] — 2026-07-09

### Changed

- **Docker Compose now runs two containers.** Inference (InsightFace, YOLO) runs in a dedicated `argus-inference` sidecar on port 8200 (internal only); the main app connects to it over HTTP. `docker compose up` is unchanged — both containers start automatically in the right order. The GPU `deploy` block in `docker-compose.yml` now sits on `argus-inference`, where the model weights actually load.
- Native run (`python -m app`) is unchanged — without `INFERENCE_URL` set it stays in-process, same as always.

### Internal

- Engine code extracted from `app/core/` into a new `app/inference/` package: `registry.py`, `face_engine.py`, `object_engine.py`, `tagger_engine.py`, `florence_engine.py`, `device.py`.
- `app/inference/server.py` — standalone FastAPI server exposing `POST /infer/faces`, `POST /infer/objects`, and `GET /infer/health`. Entry point: `python -m app.inference`.
- `app/inference/runner.py` — dispatch layer. In-process when `INFERENCE_URL` is unset; HTTP POST to the sidecar when set. Callers (`detect.py`, `enroll.py`) are unchanged — same `infer_faces`/`infer_objects` interface, same return types.
- Main process skips engine loading entirely when `INFERENCE_URL` is set, freeing RAM and GPU memory that only the sidecar needs.
- `GET /api/health` and `GET /api/capabilities` query the sidecar's `/infer/health` (2-second timeout, graceful on failure) for loaded-model status when `INFERENCE_URL` is set.
- `/api/test` and `/api/test/batch` stateless detection now routes through `runner.infer_faces`/`infer_objects`, so it also dispatches to the sidecar in two-container mode.

---

## [0.1.0-alpha.9] — 2026-07-06

### Added

- **Full face attribute set in API responses.** The detect, identify, and verify endpoints now return four additional face fields alongside `age`, `gender`, and `pose`: `mask` (mask-wearing probability 0–1), `kps` (5 keypoints as `[[x,y],…]`), `landmark_2d_106` (106-point 2D landmarks), and `landmark_3d_68` (68-point 3D landmarks). All four are `null` when the active model pack does not provide them — no error.
- **`GET /api/detections/{id}`** — fetch a single detection by id (metadata, bbox, attributes, crop URL; embedding bytes stripped). Scoped to the caller's user and environment.
- **`GET /api/detections/{id}/img`** — serve the saved crop file for a detection. Returns 404 when no crop exists.
- **`GET /api/face_embeddings/{id}`** — fetch a single reference embedding record (without the raw vector). Scoped to the caller's user and environment.
- **`GET /api/face_embeddings/{id}/img`** — serve the crop image associated with a reference embedding. Returns 404 when no image is stored.
- **Full API test coverage.** New test files cover all previously untested API modules: `GET /api/health` and `GET /api/capabilities` (health); `GET /api/activity` (activity feed); `GET /api/changes` including cursor filtering and user isolation (change feed); `/api/environments` CRUD including duplicate and delete-only-environment guards; `/api/jobs` list/get/delete with user isolation; `/api/keys` full lifecycle including revoked-key rejection; `GET /api/search` with type filter and isolation; `POST /api/export` and `POST /api/import` including round-trip, idempotency, and bad-input handling; and `/media/crops/*` and `/media/sources/*` file serving. Test suite now covers every API module.

### Fixed

- **Multi-face label no longer stamps every face in a group photo.** When `POST /api/detect/faces` or `POST /api/detect/all` is called with a `label` parameter and the image contains more than one face, previously all detected faces were confirmed as that identity — silently mislabeling everyone else in the photo. Now only the highest-confidence face is confirmed and enrolled under the given label; every other face is stored as unidentified and pending, surfacing in the review queue as normal.
- **Autocomplete dropdown no longer auto-selects on Enter.** Pressing Enter in an identity input field (tag page, review queue, suggested people) previously always selected the first item in the dropdown, even if the user had only typed without navigating. Enter now submits exactly what was typed unless the user explicitly arrow-keyed to a dropdown item. Arrow-up/down navigation and hover highlighting use a shared `activeIdx` so keyboard and mouse state stay consistent.
- **Delete all data modal shows environment name in bold.** The confirmation message and modal now clearly distinguish which environment's data is being deleted, with the environment name rendered in bold.
- **Review queue auto-confirm no longer performs DB writes inside the response formatter.** The auto-confirm pass now runs as an explicit pre-pass loop in `get_review_queue`, filtering out auto-confirmed rows before formatting. Previously the formatter was called from a generator expression and had DB side-effects, which is a latent ordering/error-propagation hazard.
- **`GET /api/face_embeddings/{id}` and `GET /api/face_embeddings/{id}/img` now enforce environment scoping.** The store query joins through `identities` and filters by both `user_id` and `environment_id`, preventing cross-environment reads.
- **Enrollment cover-photo assignment now respects ownership guards.** `POST /api/faces/enroll` previously set the identity's cover via a raw SQL statement that lacked the `WHERE user_id = ? AND environment_id = ?` guard present on every other identity mutation. It now calls `store.set_identity_cover()`, which carries the same ownership check as the explicit cover-selection endpoint.

### Internal

- `enroll.py` no longer calls `store._connect()` directly. Four raw-SQL sites replaced with store-layer functions (`store.embedding_exists`, `store.list_face_embeddings`, `store.get_face_embedding`, `store.set_identity_cover`). Three new helpers added to `store.py` to close the gap.
- `_FMT_EXT` and `_save_crop` deduplicated — `enroll.py` previously carried local copies that had silently diverged from the canonical versions in `detect.py`; both are now imported from there.
- `_persist_enrollment()` extracted in `enroll.py` — source-image save, crop save, detection insert, embedding insert, representative recompute, and index rebuild are now shared between `enroll_new` and `enroll_existing` instead of duplicated.
- Request body field parsing consolidated into `read_body_field(request, key)` in `image_input.py`. The five hand-rolled multipart/JSON parse blocks in `detect.py` (`_extract_label`, `_extract_external_ref`, `_extract_replace`, `_extract_threshold`, `_extract_top_n`) and the equivalent block in `enroll.py`'s `_parse_enroll_request` all call this helper instead. Starlette caches form/JSON after first access so there is no extra I/O cost.
- Torch device selection consolidated into `torch_device(mps=True/False)` in `app/core/device.py`. The three private `_florence_device`, `_tagger_device`, and `_object_device` functions were identical (or near-identical) copies; all three engines now import from the shared module. `mps=False` is passed by the YOLO engine, which has no MPS code path.
- `store._connect()` removed from `export_import.py`. Two new store-layer functions — `store.export_identity_data(user_id, identity_ids)` and `store.import_identity_data(user_id, identities)` — encapsulate the complex queries and the import transaction; the route handlers are now thin wrappers around those calls.
- Cursor pagination consolidated into a shared `paginate()` helper in `app/api/_utils.py`. The local `_paginate` in `identities.py` and the hand-rolled pagination block in `images.py` are removed; both now call the shared helper.
- `is_truthy` and `delete_crops` extracted to `app/api/_utils.py`. Seven identical crop-deletion loops across `detect.py`, `images.py`, `environments.py`, `identities.py`, and `review.py` replaced by the shared helper; two copies of `_is_truthy` in `detect.py` and `images.py` replaced by the shared one.
- `settings_cache._coerce` renamed to `coerce_setting` (was already imported externally by `settings.py`, so the private naming was misleading).
- `store.create_environment` and `store.rename_environment` now raise `store.DuplicateError` on a `UNIQUE` constraint violation instead of leaking `sqlite3.IntegrityError`. Callers in `main_pages.py` and `environments.py` now catch `store.DuplicateError` instead of `Exception`.
- FTS exception handlers in `store.py` now log a warning with `exc_info=True` instead of silently swallowing failures.
- Duplicate `PRAGMA table_info(users)` call in the `init_db` migration block merged into one — four column-existence checks now share a single query result.
- No-op `if not existing_env_tables: pass` block (which ran a DB query and discarded the result) removed from `init_db`.
- `import uuid` and `import json` moved to module-level in `store.py`; deferred `import uuid as _uuid` and `import json as _json` inside `create_job`/`update_job` removed.
- `face_index.py` faiss fallback now logs a warning with `exc_info=True` instead of silently setting `used_faiss = False`.
- `main.py` object-model load failure now passes `exc_info=True`, consistent with the face-model warning on the same code path.
- Replaced `httpx` with `httpx2` in `requirements.txt`. `httpx2` is a drop-in replacement (same `import httpx` namespace) required by newer starlette versions for `TestClient`; the old `httpx` entry caused a metaclass conflict in `starlette.testclient.WebSocketDenialResponse` in CI.
- 400 error message for unknown detection type normalized from `"type must be face or object"` to `"type must be 'face' or 'object'"` in `images.py` and `identities.py`.

---

## [0.1.0-alpha.8] — 2026-07-01

### Added

- **Webhooks management UI (`/webhooks`).** A dedicated page (listed as a submenu under Account, parallel to how Models sits under Settings) for managing webhooks without the API. Each webhook card shows the label, URL, event chips, and an inline Active toggle; per-card buttons for Test, Edit, and Delete. Create or edit via a modal form (URL, label, optional HMAC secret, event checkboxes).
- **Test ping.** The Test button on each webhook card sends a synchronous test delivery to the endpoint and shows the HTTP status code and round-trip latency inline for 6 seconds — green on 2xx, red otherwise. Works regardless of the webhook's active state.
- **Delivery log.** An expandable panel on each webhook card shows the last 50 deliveries (newest first): relative timestamp, event, HTTP status, and duration. Lazy-loaded on first expand with a Refresh button. Test pings appear tagged `(test)`. Capped at 100 rows per webhook in the database.
- **`GET /api/webhooks/{id}/deliveries`** — returns the 50 most recent delivery records for a webhook, including `status_code`, `duration_ms`, `error`, `is_test`, and `delivered_at`.
- **`POST /api/webhooks/{id}/test`** — fires a synchronous test delivery to the webhook URL and returns `{ok, status_code, duration_ms, error}`.
- **`detection.created` now fires** from all three synchronous detect endpoints (`/api/detect/faces`, `/api/detect/objects`, `/api/detect/all`). Previously listed as a supported event but never triggered from the sync paths.
- **`GET /api/search`** — FTS5 trigram identity search. Accepts `q` (1–200 chars), optional `type=face|object`, and `limit` (1–50, default 10). Returns ranked matches with `id`, `label`, `type`, `cover_url`, and `detection_count`. Queries shorter than 3 characters fall back to case-insensitive `LIKE`. No migration needed — the FTS index is built automatically on first startup.
- **Nav search bar.** A search input spanning the top bar on both desktop and mobile. Type to see up to 8 identity matches in a dropdown (debounced 220ms); keyboard-navigable with arrow keys, Enter, and Escape. Backed by `GET /api/search`.
- **Desktop sidebar navigation.** The top-bar nav groups and carets are replaced by a persistent left sidebar (220px). Toggled by a hamburger button on the left of the top bar; state persisted in `localStorage`. The top bar retains the environment picker, account link, theme picker, and sign-out button. When the sidebar is collapsed and any notification dot or badge count is active, a red dot appears on the hamburger. Webhooks moves to the sidebar as its own section. Mobile drawer is unchanged.
- **Garbagefire theme.** A deliberately hideous high-contrast theme: yellow background, chartreuse surfaces, hot-pink nav, Comic Sans font throughout.
- **CORS support.** `CORSMiddleware` added (`allow_origins=["*"]`), allowing browser-side cross-origin calls to the API from other LAN hosts (e.g. a separate web app calling Argus's `/api/*` routes directly). No configuration needed.

### Fixed

- **RGBA crop crash.** Detecting faces or objects in PNG images with an alpha channel caused a 500 error when saving the crop as JPEG (`cannot write mode RGBA as JPEG`). The crop is now converted to RGB before saving.
- **`GET /api/search` case-insensitivity.** The FTS5 index stored labels with original casing; queries were not normalized, so "brian" would not match "Brian" on builds where the trigram tokenizer's case-folding is not guaranteed. The index now stores `LOWER(label)` (triggers updated, existing index rebuilt on next startup) and all queries are lowercased before matching. A `LIKE` fallback fires when the FTS5 path returns no results, making search resilient to a stale or empty index.
- **`GET /api/search` `cover_url` always null.** `cover_url` was null for any identity whose `cover_detection_id` had not been explicitly set via enrollment or the cover-selection UI, even though the gallery page shows a cover for those identities. The search response now falls back to the oldest detection's crop — the same logic used by the gallery — so `cover_url` is populated whenever any detection exists.

---

## [0.1.0-alpha.7] — 2026-06-30

### Added

- **Unidentified faces page (`/unidentified`).** Dedicated gallery for all unidentified faces (no match found or dismissed from review). Each crop shows a tag link, a delete button, and an assign (+) button that lets you type a person's name and immediately label the face without going through the review flow. Linked from the dashboard.
- **Webhooks.** `POST /api/webhooks` creates a webhook; `GET /api/webhooks` lists them; `GET/PUT/DELETE /api/webhooks/{id}` fetch, update, or remove one. Argus fires the webhook on `detection.created`, `identity.created`, `identity.deleted`, and `detection.labeled` events. Update body accepts `url`, `events`, `label`, `secret`, and `is_active` (all optional).
- **Image reprocess** — `POST /api/images/{id}/reprocess` re-runs detection on an already-stored source image using the currently active models. Accepts `?type=faces|objects|all`, `?replace=true` (clear existing detections of that type first), and `?async=true`.
- **Image search** — `POST /api/images/search` finds source images containing all supplied identities (AND semantics) with optional `type`, `since`, `until`, `confidence_min`, and cursor/limit pagination. Useful for querying "find all photos where Alice and Bob both appear."
- **Identity merge API** — `POST /api/identities/{id}/merge` with `{"into": <target_id>}` moves all detections and reference embeddings from the source identity into the target, then deletes the source and rebuilds the match index. Backs the "Merge into" button already present on the identity page.
- **Hardware and OS info in `GET /api/health`** — response now includes CPU model, physical core count, total RAM, and OS name/version. Adds `psutil` as a new dependency.

### Changed

- **Gallery infinite scroll no longer jumps on mobile.** The justified-layout gallery (identity gallery, source images, unidentified) now appends only new complete rows when a page loads during infinite scroll — existing rows are never touched. Previously, each page load rebuilt the entire DOM, causing scroll-position jumps. A companion fix ignores resize events that only change viewport height (mobile browser toolbar show/hide) since justified row packing is width-based; full relayout only fires when the container width changes.
- **Source images page (`/images`) improvements** — incremental justified layout (same no-jump fix as identity gallery), back-to-top button, filter bar shows all images matching any combination of identity, type, and date range.
- **Tag page** — label-entry input repositioned closer to the bbox it belongs to; object boxes use the same assign flow as face boxes.

---

## [0.1.0-alpha.6] — 2026-06-28

### Added

- **Admin log viewer.** A "View logs" button on the Settings page opens a modal that replays Argus's recent in-memory logs (level filter + Refresh), backed by a new admin-only `GET /api/logs`. A new `system.log_buffer_size` setting (default 500, bounded 100–100000) controls how many lines are kept in memory and is resized live when changed. In-memory only (cleared on restart); stdlib-only, no new dependencies.
- **Camera capture** on Enroll, Detect, and Test — a "Use camera" button grabs a photo from the device camera and feeds it straight into the flow. Uses the live in-browser webcam where the page is a secure context (HTTPS or `localhost`), and falls back to the phone's native camera (capture file input) over plain LAN HTTP, so snapping a photo on a phone works without HTTPS. No backend changes — the captured frame goes through the existing upload path.
- **More object detection models** in the registry, all producing bounding boxes: YOLOv8l, YOLO11 (s/m/l/x), YOLOv10 (s/m/l/x), and RT-DETR (l/x — a transformer detector, loaded via Ultralytics' `RTDETR`); plus the compact `buffalo_sc` face pack. Existing YOLO-World open-vocab models remain.
- **Each model now has a short description** (stored in a new `models.description` column) shown on the Models page and returned by `/api/models`.
- **Source images page (`/images`).** Justified infinite-scroll gallery of all processed source images. Each thumbnail is a link to the tag page for that image; delete an image and all its detections in one click. Filterable by identity, detection type, and date range.
- **About modal** — an info dialog accessible from the nav showing the Argus version, license, and links.

### Changed

- **Review queue split into two sections** — "Suggested matches" (faces Argus tentatively matched to someone) and "No match" (below-threshold faces). Each has its own selection and one unambiguous bulk action: **Confirm selected** for suggested matches, **Dismiss selected** for no-match faces. This removes the old single-checkbox/two-button model where "Confirm selected" silently ignored unmatched faces. The sections sit side by side on desktop and stack on mobile.
- **The Test page now identifies faces** — each detected face shows the best-matching enrolled person (name + similarity, top match regardless of threshold) on the box and in the result table. Still read-only: nothing is stored, enrolled, or queued for review. `/api/test` and `/api/test/batch` faces gain `identity_id`/`label`/`similarity` fields (`null` when no one is enrolled).

### Fixed

- **Recognition now works without a restart on a freshly set-up instance.** When a face model was activated before anyone was enrolled, the in-memory match index never recorded the active model, so the first enrollments were saved but not loaded into the index — matching only started working after a restart. `build_all` now records the active model even with no enrolled faces, so the first enrollment takes effect immediately.

---

## [0.1.0-alpha.5] — 2026-06-25

### Added

- **Recognition readiness indicators.** A banner on every signed-in page flags when models aren't set up — red and persistent when none are active, amber and dismissible (per session) when only one type is active; both active shows nothing. A matching status dot sits on the Models nav item for admins. Running People-only, Objects-only, or both are all treated as valid setups, so a missing type is only flagged when nothing is active.
- **Adaptive Detect/Test controls.** The mode selector reflects what's active: "People + Objects / People / Objects" when both are active, a fixed "People" or "Objects" when only one is, and "No models active" with the inputs hidden when none are.
- **Toast notifications** (`showToast`/`flashToast`) for model download/activate/remove events and for enroll/detect errors. Detect now survives a per-image network error instead of aborting the whole batch.
- **Admin onboarding.** The first signup button reads "Create account as admin"; first registration and admin login land on the Models page until at least one model is downloaded, then on the dashboard.
- **Removable upload thumbnails** — an × on each queued image (Enroll, Detect, Test) to drop it before submitting.
- **Enroll result preview** — after enrolling, the source image is shown with a box around the enrolled face, matching the Detect/Test style.
- A **download spinner** on the Models page while weights download.
- `/api/capabilities` reports `downloaded` per detection type.

### Changed

- **Environments are managed in one full-page modal** (Select / Rename / Delete / Create) opened from the nav, which now shows the active environment's name; the old dropdown is gone. Creating an environment no longer reloads the page and prompts whether to switch to it. The `/environments` page remains.
- **Face-only features hide when no face model is active** — Enroll, Review, and Suggested drop out of the nav (desktop and mobile).
- **Themes are defined once** — a single list drives the JS palette and both the desktop and mobile pickers; the active theme is marked with an accent ring, and the theme picker is now available in the mobile menu.
- Detect's mode labels use "People" to match Test.

### Fixed

- **Change feed now covers review actions and single deletes.** Reassign, reject, and `DELETE /api/detections/{id}` previously mutated a detection without emitting an event, so a delta-sync client missed corrections made through the Review queue. They now record `relabeled`/`deleted` events like the casual-correction and bulk paths already did.
- **Startup crash on a stale session.** A session cookie pointing at a user that no longer exists (e.g. after a database reset) now redirects to sign-in instead of returning a 500.
- **Mobile horizontal-scroll fixes** — the identity page header, the crop lightbox, review cards, and the Account API-key table no longer overflow sideways on small screens.

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

[0.1.0-alpha.18]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.17...v0.1.0-alpha.18
[0.1.0-alpha.17]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.16...v0.1.0-alpha.17
[0.1.0-alpha.16]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.15...v0.1.0-alpha.16
[0.1.0-alpha.15]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.14...v0.1.0-alpha.15
[0.1.0-alpha.14]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.13...v0.1.0-alpha.14
[0.1.0-alpha.13]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.12...v0.1.0-alpha.13
[0.1.0-alpha.12]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.11...v0.1.0-alpha.12
[0.1.0-alpha.11]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.10...v0.1.0-alpha.11
[0.1.0-alpha.10]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.9...v0.1.0-alpha.10
[0.1.0-alpha.9]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.8...v0.1.0-alpha.9
[0.1.0-alpha.8]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.7...v0.1.0-alpha.8
[0.1.0-alpha.7]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.6...v0.1.0-alpha.7
[0.1.0-alpha.6]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.5...v0.1.0-alpha.6
[0.1.0-alpha.5]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.4...v0.1.0-alpha.5
[0.1.0-alpha.4]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.3...v0.1.0-alpha.4
[0.1.0-alpha.3]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.2...v0.1.0-alpha.3
[0.1.0-alpha.2]: https://github.com/MichaelYagi/argus/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]: https://github.com/MichaelYagi/argus/releases/tag/v0.1.0-alpha.1
