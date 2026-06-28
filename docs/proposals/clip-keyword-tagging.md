# Proposal: Semantic Image Keyword Tagging (CLIP)

Status: Implemented and validated end-to-end. Default vocabulary seeded (~21k: Open Images
+ curated phrases). ViT-B-32 exported to ONNX under models/clip/ViT-B-32/ and confirmed
tagging real photos correctly (dog photo -> dog/puppy/golden retriever). ViT-L-14 can be
exported the same way via scripts/export_clip.py. One-time 21k text-matrix build is slow on
CPU (~9 min, cached afterward; runs as a background job on model activation); fast on GPU.
Scope: New optional capability. Does not change existing face/object behavior.

## 1. Summary

Add an optional, self-hosted **semantic keyword tagging** capability to Argus. When an
image is scanned, Argus returns its **recognized faces, objects, and keywords** together
in both the UI and the API. Keywords are **image-level** and produced by comparing a CLIP
image embedding against a **static, admin-managed ~20k-entry vocabulary** (single words and
short phrases such as "birthday party").

Beyond per-image tagging, v1 also supports **search-by-keyword** ("find every photo tagged
'Christmas'") over already-scanned images.

The CLIP model is an **optional, downloadable model** managed on the Models page (and via the
API) like the existing face/object models — Argus runs and is fully useful without it. It
introduces **no new Python dependency** (vendored BPE tokenizer) and **no new runtime service**:
CLIP runs in-process on the existing `onnxruntime`.

This is a generic capability any HTTP client benefits from; it is Shashin-agnostic.

## 2. Motivation and non-goals

YOLO detects concrete object classes and cannot express scene/theme concepts ("Christmas",
"celebration", "cozy"); ArcFace/buffalo embeddings are face-identity only. CLIP embeds images
and text into one shared space, so an image can be matched semantically against arbitrary
words/phrases — fast, deterministic, controllable, fully self-contained. The persisted image
embedding also doubles as the substrate for future semantic image search.

Non-goals:
- Not a captioner. CLIP scores a supplied vocabulary; it does not generate free-form text.
- Not per-bbox keywords. Keywords describe the whole image, shown once per image alongside
  the boxes. (Per-crop tagging is a possible future extension.)
- No localization of keywords (CLIP produces no boxes).

## 3. User-facing behavior

### Per-image scan (faces + objects + keywords)
A scanned photo returns recognized **faces, objects, and keywords** together, in both UI and
API. Keywords are image-level and rendered as a chip list beside the existing bbox overlays.

### UI (background tagging)
The page renders image and boxes immediately; keyword chips fill in a moment later (CLIP runs
in the background; the UI fetches stored keywords). First view of a new image shows a brief
"keywords loading" state; revisits are instant (keywords are stored). If no tagging model is
active, no keyword UI appears (same gating as the object-class editors).

### API (synchronous in detect response)
Detect/test responses include a `keywords` array inline by default, so one call returns faces +
objects + keywords. A `keywords=sync|async|off` flag controls timing without a second endpoint:
`sync` (default, response waits and includes them), `async` (respond fast, compute+store in the
background — used by the UI), `off` (skip). Keywords are stored in all non-`off` cases; the flag
only controls whether the response waits.

### Search by keyword (v1)
`GET /api/images?keyword=<word>` returns the stored images tagged with that keyword, using the
existing cursor-pagination pattern. This is a text query over stored keyword rows (not CLIP
semantic search; that remains future work).

## 4. Architecture

Reuses existing Argus patterns; no change to deployment topology.

### 4.1 New model type
- Extend the `models.type` CHECK from `('face','object')` to include `clip`. `embedding_dim`
  already exists and is used.
- Extend `engine_registry` with `get_tagging_engine()` / `swap_tagging_engine()`, mirroring the
  face/object accessors and reusing the same lock for hot-swap safety.
- Models API/page gain download / activate / remove / hot-swap for `clip` models, identical to
  face/object. `GET /api/health` and `/api/capabilities` report the active tagging provider;
  `GET /api/models?type=clip` lists them.
- Seed **two OpenCLIP models**: **ViT-B/32** (default/recommended — ~150M params, 512-d,
  CPU-friendly ~50-150 ms) and **ViT-L/14** (optional "quality" — ~428M, 768-d, GPU-preferred).
  Both use the **same CLIP BPE tokenizer**, so one vendored tokenizer covers both. **SigLIP is
  excluded** (its SentencePiece tokenizer would be a new dependency). Weights are exported to
  ONNX offline (torch is build-time only, never a runtime dependency). None downloaded by
  default — Argus operates without any tagging model. The differing dims (512 vs 768) make the
  model-swap = re-encode rule concrete.

### 4.2 CLIP engine wrapper (`app/core/tagging_engine.py`, new)
- Loads a CLIP image encoder and text encoder (ONNX) via existing `onnxruntime`, honoring
  `system.use_gpu` and provider auto-detection like the other engines.
- Image preprocessing (resize/center-crop, CLIP normalization, CHW float32) via existing
  Pillow + numpy. No new dependency.
- A vendored CLIP BPE tokenizer (single public file + vocab/merges asset) embeds vocabulary
  text. Keeps pip dependencies at zero.
- Methods: `embed_image(img) -> vec`, `embed_texts(words) -> matrix`.

### 4.3 Vocabulary management (global, admin, bulk-only)
- **Single global vocabulary**, admin-managed — consistent with models and the YOLO-World
  vocabulary being global, not per-environment. A vocabulary edit therefore re-tags all users'
  libraries (a cheap background re-score; see 4.7).
- Stored in its **own table** (20k entries is too large for a settings string).
- **Bulk-only editor:** download the current list as a file; upload a file to **replace** it.
  No in-app add/remove list. Admin-only, and hidden when no tagging model is active (so the
  "no model to embed words" state cannot occur).
- **Dedup + validation on upload:** trim, case-insensitive de-duplication (the YOLO-World
  sanitizer pattern), plus bounds on entry count and length. **Multi-word phrases are allowed**
  and are part of the shipped default list.
- **Default content:** seeded on init from `app/db/default_vocabulary.txt` (only when the table
  is empty, so an edited/cleared vocabulary is never resurrected). Argus **ships a curated ~900
  default** spanning objects, animals, food, household, nature, scenes, occasions, activities,
  weather, and moods — including theme/occasion **phrases** ("birthday party", "christmas
  morning", "golden hour", "candid"). The agreed full **Open Images ~19.9k** set (CC BY 4.0) is
  produced on demand by `scripts/fetch_openimages_vocab.py` (merging the phrase supplement); drop
  its output in as the seed file or upload it on the Models page. The dataset is not bundled
  (size/license), so the curated default ships and works out of the box.

### 4.4 Storage (persisted embeddings + cached keywords)
Two new tables, scoped by `user_id` + `environment_id`, tagged by `model_id` (so vectors/keywords
from different CLIP models are never mixed), per the `face_embeddings.model_id` precedent:
- `image_embeddings(source_image_id, model_id, embedding BLOB, dim, ...)` — one CLIP image vector
  per stored image. Keystone: lets vocabulary/template edits re-tag by re-scoring stored vectors
  (cheap matmul) instead of re-running the image encoder, and is what future semantic search needs.
- `image_keywords(source_image_id, user_id, environment_id, model_id, vocab_version, keyword,
  score)` — **normalized: one row per (image, keyword)**, stamped with `vocab_version` for
  staleness detection. Indexed on `keyword` (scoped by user/environment) for fast
  search-by-keyword, and on `source_image_id` for per-image fetch and cascade delete. Normalized
  rows (vs a JSON blob per image) are chosen specifically because search-by-keyword is in v1:
  retrieval becomes a trivial indexed lookup, score-sorting is free, and ~15 rows/image is
  negligible for SQLite.
- **Cascade delete:** removing a `source_image` deletes its embedding and keyword rows.

### 4.5 Tagging pipeline; stored vs ad-hoc
- On ingest of a **stored** image (detect/enroll) with a tagging model active and `keywords != off`:
  run `embed_image`, persist the vector, score against the cached text matrix, threshold, persist
  keywords. `sync` includes them in the response; `async` does the same in a `BackgroundTask`.
- **Ad-hoc images are compute-and-return.** The read-only Test page and `POST /api/keywords` on an
  arbitrary upload have no `source_image` to attach to, so they compute keywords and return them
  but **persist nothing**. The Test endpoint computes keywords **inline** in its response (reusing
  the already-decoded image — cheaper than a second background round-trip would be), so the Test
  page shows faces + objects + keywords together; the storing detect endpoints use the
  sync/async flag instead. Persistence/caching applies only to stored images.

### 4.6 Scoring and thresholds
- Cosine similarity of the image vector against the text matrix, then **top-K plus a floor
  threshold**. `clip.tag_top_k` (default **15**) is the primary control; `clip.tag_threshold`
  (default **~0.20** raw cosine) is the safety-net floor.
- Both are **live, user-configurable via UI and API** — not fixed defaults. They live in a new
  **`keywords` settings category**, which renders as its own "Keywords" card on the Settings page
  (parallel to how object detection-confidence/IOU sit in the "Object" card, while object
  *classes* live on the Models page). Editable via the standard `PUT /api/settings/*` endpoints
  and read from the live settings cache like every other threshold.
- Caveat: absolute cosine values are model/template-specific, so the defaults are calibrated to
  the default ViT-B/32 + default template and re-tuned during build; ViT-L/14 may want a
  different floor. Top-K is the robust control regardless of model.

### 4.7 Background jobs (backfill and re-tag)
- Reuse the existing `jobs` table and the model-download progress/poll pattern (same shape as the
  `data-status-poll` resume wiring). Execution via `BackgroundTasks`/asyncio (no Celery/Redis).
- Two job kinds:
  - **Backfill** — triggered by activating a tagging model (or switching models): re-encode every
    stored image (one image-encoder pass each; slow).
  - **Re-tag** — triggered by a vocabulary edit or template change: re-score persisted vectors
    against the rebuilt text matrix (cheap).
- **Dependency rule:** re-tag needs image vectors, so it waits for any in-progress backfill;
  otherwise it runs immediately.
- **Latest-wins, single-flight:** jobs are stamped with `vocab_version`; a newer edit supersedes a
  running re-tag (no point finishing a re-tag for a replaced list).
- **Status surfaced** via a job-status endpoint and a Models-page indicator reusing the download
  spinner/poll pattern.

## 5. API contract
- **Detect/test responses** gain an optional `keywords` array (`{ keyword, score }`), present when
  a tagging model is active and `keywords != off`. Timing via `keywords=sync|async|off` (default
  `sync`).
- **Standalone keyword endpoints**, following the one-of `file`/`image_url`/`image_base64` input
  convention:
  - `POST /api/keywords` — image in, ranked keywords out (ad-hoc, compute-and-return, no persist).
  - `GET /api/images/{source_image_id}/keywords` — stored keywords for an ingested image (UI async
    fill-in).
- **Search by keyword:** `GET /api/images/search?keyword=<word>` — stored images carrying that
  keyword, cursor-paginated. (Uses `/search` rather than `GET /api/images?keyword=` because the
  bare `/api/images` route already exists for external_ref resolution with a required param.)
- **Vocabulary (admin):** `GET /api/keywords/vocabulary` (download) and `PUT /api/keywords/vocabulary`
  (replace; server dedups + validates). Bulk only.
- **Models endpoints:** the new `clip` type flows through download/activate/remove/status unchanged.
- All `/api/*` keyword routes require `X-API-Key`; scoped to the caller's `(user_id, environment_id)`
  and the active tagging model.

## 6. Settings (new keys, live)

All three are in a new **`keywords` settings category** and render together as a **"Keywords" card**
on the Settings page, configurable via UI and via the standard `PUT /api/settings/*` API:

- `clip.tag_top_k` (int, default 15) — max keywords per image (primary control).
- `clip.tag_threshold` (float, default ~0.20) — minimum cosine score to keep a keyword
  (safety-net floor; calibrated to the default model/template).
- `clip.prompt_template` (string, default `"a photo of {word}"`) — admin-editable. **Changing it
  re-renders every vocabulary entry, so it triggers a full text-matrix rebuild + library re-tag
  (background job), not an instant change.** The settings PUT handler fires that job (the same way
  `face.match_strategy` triggers an index rebuild today).

Notes:
- Adding a `keywords` category requires extending the settings `_VALID_CATEGORIES` set
  (currently `{face, object, system}`) and seeding these keys.
- On/off is implicit: the feature is active iff a `clip` model is active.
- The vocabulary itself is its own table (size) and is edited on the Models page (bulk
  upload/download), not in this settings card — mirroring object classes (Models) vs object
  thresholds (Settings).

## 7. Performance and storage
- **Per stored image:** one CLIP image-encoder pass (~50-150 ms CPU for ViT-B/32, ~tens of ms GPU)
  plus a negligible matmul. "+1 model pass" on top of face/object.
- **Vocabulary/template edit re-tag (persisted vectors):** ~1 s for ~1k images, ~seconds for ~10k,
  ~tens of seconds for ~100k (streamed). Incremental word add/remove is near-instant; a template
  change re-embeds all 20k words (text side, seconds-minutes) then re-scores.
- **Model swap re-encode:** switching CLIP models invalidates stored vectors and triggers a full
  backfill (image re-encode). Vocabulary/template swaps do not.
- **Storage:** model ~150-600 MB; text matrix ~20-40 MB (20k x 512); per-image vector ~2 KB (fp32).
  RAM: model resident ~0.3-1.5 GB plus matrix. Note the combined footprint when buffalo + YOLO +
  CLIP are all active.

## 8. Dependencies and deployment
- **No new pip dependency** (vendored BPE tokenizer; onnxruntime/numpy/Pillow already present;
  BackgroundTasks for async work).
- **No new service** — in-process, single container, like InsightFace/YOLO.
- Model weights, tokenizer asset, and the default vocabulary are downloadable assets via the
  existing model-download flow (volumes for `models/`).
- `faiss` (already optional) is the lever if image-vector search later outgrows brute-force numpy.

## 9. Backward compatibility / optionality
Entirely additive. With no `clip` model active: no keyword UI, no `keywords` in responses, no
search-by-keyword results, no background work — existing face/object flows unchanged. Generic and
Shashin-agnostic (no Shashin-specific schema or routes).

## 10. Suggested build order
1. Schema: `models.type` CHECK extension; `image_embeddings`, `image_keywords` tables (cascade
   delete); vocabulary table; seed `clip` model rows and the default ~20k vocabulary.
2. `tagging_engine.py` (image + text encoders, vendored tokenizer, preprocessing); registry
   `get/swap_tagging_engine`.
3. Vocabulary upload/download + dedup/validation; text-matrix builder with incremental re-embed
   and on-disk cache; prompt-template application.
4. Scoring + thresholds; `image_embeddings`/`image_keywords` write path (stored images).
5. Detect-response `keywords` field + `keywords=sync|async|off`; `POST /api/keywords`;
   `GET /api/images/{id}/keywords`; `GET /api/images?keyword=`.
6. Models page + API for `clip`: download/activate/remove; health/capabilities provider.
7. Settings: new `keywords` category (extend `_VALID_CATEGORIES`); seed `clip.tag_top_k`,
   `clip.tag_threshold`, `clip.prompt_template`; PUT handler fires the rebuild/re-tag job on a
   `clip.prompt_template` change.
8. Background jobs (backfill on activate/model-swap; re-tag on vocab/template edit) via the `jobs`
   table, with dependency + latest-wins rules and status surfacing.
9. UI: keyword chips on Test/Tag pages (async fill-in); "Keywords" settings card (top-K,
   threshold, template); admin vocabulary upload/download on the Models page; job progress
   indicator on Models page.
10. Tests: engine hot-swap, dedup/validation, scoring/threshold, response shape, optional-when-
    inactive gating, keyword search, re-tag cheapness, template-change rebuild.

## 11. Resolved decisions

- **Model choice:** two OpenCLIP models — **ViT-B/32** (default, CPU) and **ViT-L/14** (optional,
  GPU/quality) — sharing one vendored BPE tokenizer. SigLIP excluded (SentencePiece dependency).
- **Default vocabulary:** Open Images image-level label names (~19.9k, CC BY 4.0, attributed) +
  a curated few-hundred occasion/mood/theme phrase supplement. Final license confirmation at
  implementation.
- **Thresholds:** live-configurable via UI + API in a new **"Keywords" settings card**
  (`keywords` category), not fixed. Defaults `clip.tag_top_k = 15` (primary),
  `clip.tag_threshold ~ 0.20` (floor), calibrated to the default ViT-B/32 + template during build.
  Mirrors object thresholds (Settings) vs object classes (Models).
- **Keyword storage:** normalized one-row-per-(image, keyword), indexed on `keyword` and
  `source_image_id`, chosen to make v1 search-by-keyword a fast indexed lookup.

### Deferred to implementation
- Exact OpenCLIP checkpoints/pretraining for B/32 and L/14, and confirming each weight license.
- Final calibration of the threshold floor against sample images (and any per-model floor).
- The curated phrase supplement contents and the Open Images NOTICE/attribution text.

### Out of scope (future)
- Per-crop (per-bbox) keywords.
- CLIP semantic (vector) image search reusing the persisted embeddings.

## 12. Risks
- **Tag quality:** CLIP correlates rather than reasons; near-synonyms co-fire and abstract tags can
  misfire. Mitigated by top-K + threshold tuning and vocabulary curation; blunter than a VLM.
- **Backfill time** on CPU for large libraries (model activate/swap). Mitigated by GPU and
  background execution; vocabulary/template edits stay cheap via persisted vectors.
- **Global re-tag blast radius:** an admin vocabulary/template edit re-tags all tenants. Cheap, but
  library-wide; runs as a background job with status.
- **Vendored tokenizer maintenance:** a small amount of vendored code tied to the chosen model family.
