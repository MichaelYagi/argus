# Argus

Self-hosted face and object recognition you can both **use and build on** — a clean API backend for other apps *and* a real human curation UI in one, instead of a headless black box or a feature trapped inside a photo library. Runs on your LAN via `docker compose up`.

**Features:**
- Face detection and recognition (InsightFace/ArcFace) — enroll people by name, match them across photos
- Labelling a person during detection auto-enrolls them as a reference — no separate enroll step needed
- Object detection via standard YOLO (80 COCO classes) or YOLO-World (open vocabulary — detect anything)
- Per-user accounts with admin approval flow and individually managed, named API keys
- Environments — isolate recognition data into named workspaces (dev, prod, home, work…) within a single instance; API keys are scoped per environment
- Review queue with ranked match suggestions, configurable auto-confirm threshold, and auto-enroll on confirm
- Suggested people — clusters unlabeled faces into proposed identities; name a cluster to enroll everyone in it at once
- Justified infinite-scroll galleries per identity with cover photo selection and bulk operations
- Tag page — full source image with clickable face and object bbox overlays for labelling; draw bounding boxes manually for faces the detector missed (click-drag on desktop, long-press-drag on mobile); Prev/Next navigation across a gallery sequence with adjacent-image preloading; last-viewed thumbnail highlighted on return
- Test page — check whether an image contains people or objects, and who each face looks like, without storing or enrolling anything (read-only)
- Integration helpers — opaque `external_ref` correlation ids, a change feed for delta sync, webhooks, a capabilities manifest with hardware reporting, and batch label/read endpoints
- Bulk detection with optional async mode — submit many images in one call; fire-and-forget with a job id and webhook notification on completion
- Identity merge — combine two identities, moving all detections and enrolled references to the target; available via API and the gallery UI
- Reprocess — re-run detection on any stored image with the current active models, with optional replace to clear prior results; available via API and the tag page UI
- Source image filtering — filter the image browser by identity, detection type, date range, no-detections, no tagged faces, manually tagged faces, no crops; sortable by newest, oldest, most or fewest detections
- Export and import with merge — move recognition data between instances
- Live settings (thresholds, GPU, crop padding) — no restart needed
- Hot-swap models without restarting
- Embedding averaging + faiss index for fast, accurate matching at scale
- GPU auto-detected at startup; manual override available in Settings; hardware info (CPU, GPU, RAM, OS) reported in `/api/capabilities`
- REST API — everything the UI does is available via API, fully paginated
- Mobile-responsive browser UI

**Supported image formats:** JPEG, PNG, WEBP, BMP, GIF (first frame), TIFF, HEIC/HEIF, AVIF, MPO (first frame)

---

## Contents

**Getting started:** [Quick start (Docker)](#quick-start-docker) · [First run](#first-run) · [Native run (no Docker)](#native-run-no-docker) · [Docker reference](#docker-reference)

**Concepts:** [Review queue](#review-queue) · [Suggested people](#suggested-people-face-clustering) · [Manual face tagging](#manual-face-tagging) · [Object detection models](#object-detection-models) · [Environments](#environments)

**API:** [API](#api) · [Webhooks](#webhooks) · [Bulk detection](#bulk-detection) · [Identity merge](#identity-merge) · [Reprocess](#reprocess) · [Image filtering](#image-filtering) · [Export and import](#export-and-import) · [Integrating another system](#integrating-another-system)

**Operations:** [Troubleshooting](#troubleshooting) · [Development](#development) · [Releasing a new version](#releasing-a-new-version)

---

## Quick start (Docker)

**1. Clone**

```bash
git clone https://github.com/MichaelYagi/argus.git
cd argus
```

No `.env` file needed — `SECRET_KEY` is auto-generated on first startup and persisted automatically. See `.env.example` if you want to override it or set other options.

**2. Build and start**

```bash
docker compose up --build
```

The first build downloads PyTorch, InsightFace, Ultralytics, and friends — expect several GB and 5–15 minutes depending on your connection. Subsequent starts are fast:

```bash
docker compose up          # foreground
docker compose up -d       # background
```

**3. Open the app**

```
http://localhost:8100
```

Port 8100 — Argus had 100 eyes.

---

## First run

1. Visit `http://localhost:8100` — you're redirected to sign up
2. Create an account — the **first user is automatically the admin**
3. Subsequent sign-ups require admin approval from the **Account** page
4. Go to **Models** and download a face model (`buffalo_l` is recommended), then **Activate** it
5. Go to **Enroll** and enroll a face with a name — or skip this and label faces directly on the **Detect** page
6. Go to **Detect** and drop in a photo — Argus will detect and match faces

**For object detection:** download and activate a YOLO model from the Models page. `yolov8s` is a good starting point for standard detection. For open-vocabulary detection, see [YOLO-World](#yolo-world-open-vocabulary-object-detection) below.

---

## Native run (no Docker)

Requires Python 3.11+.

```bash
cd argus
python3 -m venv .venv
source .venv/bin/activate       # Linux/macOS
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
```

```bash
python -m app
```

Binds to `http://localhost:8100` by default. Pass `--host` and `--port` to override:

```bash
python -m app --host 0.0.0.0 --port 9000
```

Data is stored in `./data/` and model weights in `./models/` relative to the working directory. To use a different data location, set `DATA_PATH`:

```bash
DATA_PATH="/Volumes/MyDrive/argus-data" python3 -m app --host 0.0.0.0 --port 8100
```

**GPU (native run):** Install `requirements.txt` and ensure NVIDIA drivers are installed. Argus auto-detects the GPU and pre-loads the required CUDA libraries at startup — no manual configuration needed for most setups. See [Troubleshooting](#troubleshooting) if GPU isn't detected.

---

## Docker reference

| Command | What it does |
|---|---|
| `docker compose up --build` | Build image and start (required after code changes) |
| `docker compose up` | Start using existing image |
| `docker compose up -d` | Start in background |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f` | Stream logs |

**Data persists** — `./data` (database, crops, source images) and `./models` (downloaded weights) are bind-mounted from your host, so they survive container rebuilds.

**Two containers, one command.** `docker compose up` starts both the main app (`argus`, port 8100) and the inference sidecar (`argus-inference`, internal port 8200, not exposed to the host). The sidecar loads the ML model weights; the main app handles all HTTP traffic, the UI, and the database. No configuration needed — the `INFERENCE_URL` that connects them is set in `docker-compose.yml`.

### GPU support (Docker)

Uncomment the `deploy` block in `docker-compose.yml` under the `argus-inference` service. That's where the model weights load, so that's where the GPU reservation belongs. Requires the NVIDIA Container Toolkit installed on the host and Docker Desktop using the WSL2 backend. GPU availability is auto-detected at runtime — no rebuild needed.

### GPU and performance

A GPU materially speeds up both engines:

- **Face engine (InsightFace/onnxruntime)** — moderate speedup; CPU is usable for casual use
- **Object engine (YOLO/RAM++/torch)** — significant speedup; RAM++ in particular is a large transformer and noticeably slow on CPU under load

CPU is fully functional — all detection, matching, and review features work. At low throughput (a few images at a time) the difference is small; under sustained load or with RAM++ active, GPU makes a meaningful difference.

The active provider is visible in `GET /api/health` — confirm GPU is working there before running heavy workloads.

### ARM / Apple Silicon (M1/M2/M3)

Works natively. The image builds for ARM64 and `onnxruntime` (CPU) is installed automatically — `onnxruntime-gpu` has no ARM64 wheels. Everything runs correctly on CPU; Apple's Neural Engine is not used.

---

## Review queue

Detections below the match threshold go into a review queue (`/review`). Each item shows:
- The detected face crop
- The current match (if any) and its similarity score
- Ranked suggestions from enrolled faces
- Actions: confirm, reject, reassign, or dismiss
- **View in image** link — opens the full tag page for the source photo with the detection bbox highlighted; the Back button returns to the same review tab at the same scroll position

**Three tabs:**

- **Suggested matches** — faces with a plausible identity match below the auto-confirm threshold. Candidates are grouped by the suggested identity: each group has a header showing the name and count, plus **Confirm all** / **Reject all** buttons for one-click bulk action. Groups collapse and expand by clicking the header.
- **No match** — faces that scored below the match threshold against all enrolled people.
- **Mismatches** — confirmed faces whose embedding scores poorly against their identity's centroid, flagged as possible mislabels. Each card has a **Looks correct** button (suppress the flag so it won't reappear) and a **Dismiss** button (unidentify — strip the assignment and move the face back to the review queue). Ignoring a card is fine: the face stays confirmed as that person and nothing changes. Mismatch detection uses a per-identity centroid (L2-normalized mean of all confirmed embeddings) and an adaptive threshold (mean − 2σ of similarity scores); identities with fewer than 3 confirmed faces fall back to the global mismatch threshold.

**Keyboard shortcuts:**

| Key | Action |
|---|---|
| `↑` / `↓` | Move focus to the previous / next card |
| `C` | Confirm the focused card |
| `D` | Dismiss (unidentify / reject) the focused card |
| `A` | Toggle select all on the active tab |
| `Shift+C` | Confirm all selected cards |
| `Shift+D` | Dismiss all selected cards |

Shortcuts are suppressed when focus is in a text input or textarea.

Key thresholds (all configurable in **Settings**):
- `face.match_threshold` (default 0.5) — minimum similarity to assign a match at all. Below this, the face is stored but left unidentified.
- `face.auto_confirm_threshold` (default 0.80) — detections at or above this similarity are confirmed automatically and skip the review queue. Below it, they land in the queue for manual review.
- `face.auto_enroll_threshold` (default 0.92) — gate for the *automatic* path only. When Argus auto-confirms a high-similarity match with no human in the loop, the detection's embedding is added to the reference set only if its face-detection quality score clears this bar — avoiding the unattended promotion of low-quality crops. Set to 0 to disable automatic enrollment.

**Human actions always enroll.** When you confirm, reassign, or label a face yourself, that's ground truth — its embedding is added to the person's reference set unconditionally (the threshold above does not apply), so future matches of that person improve.

**Matching method (`face.match_strategy`, Settings):**
- **Best match** (default) — a face is scored against *every* reference photo and takes the closest. Enrolled photos stay ~100%, and people who look different across photos (age, glasses, lighting) recognize better. Slightly more compute (every reference is indexed, not one centroid per person) — negligible until tens of thousands of faces.
- **Average** — each person's reference photos are blended into one representative embedding; a face is scored against that average. Faster and steadier, but an individual photo's score drifts below 100% as you add varied references (it's measured against the moving average, not itself).

Either way, confirming more varied shots of someone improves their recognition.

---

## Suggested people (face clustering)

The **Suggested** page (`/clusters`) groups *unlabeled* faces — ones that match nobody
enrolled — into "probably the same person" clusters by similarity. Name a group and every
face in it is labelled and enrolled together. This is the fast way to seed recognition on an
existing photo set: instead of labelling faces one at a time, you name a handful of groups
and you're mostly done.

- Adjust grouping live with the threshold slider (backed by `face.cluster_threshold`,
  default 0.5). Higher = stricter (splits more); lower = looser (merges more).
- Singletons are dropped — a suggestion needs at least two corroborating faces.
- **Faces are individually selectable**, which is how you correct imperfect groups:
  - **Remove a wrong face** — "Select all", then deselect the odd one out before naming; it stays unknown.
  - **Split** — select part of a group and name it, then name the rest separately.
  - **Merge** — select faces across two groups and name them together as one person.
  - **Dismiss** — hide selected faces from Suggested without deleting them (sets an `ignored`
    flag; the detection still shows on the tag page and in the image's data). For real faces
    you just won't enroll.
  - **Delete** — permanently remove the selected crops everywhere. For junk (false positives,
    partial faces, strangers you'll never name).

  "Select all" on a clean group then name is the one-click fast path.
- Naming uses the same batch-label endpoint as everywhere else, so named faces are enrolled
  as ground-truth references.
- Available over the API: `GET /api/clusters?threshold=<0-1>&min_size=<n>` returns the
  groups with their detection ids and crop URLs; name a selection by POSTing its ids to
  `/api/detections/label`. Read-only — clustering is computed on demand and stores nothing.

It complements the review queue: the queue handles faces that *do* resemble an enrolled
person, clustering handles the residual unknowns that match no one yet.

---

## Manual face tagging

When the face detector misses someone — profile shots, occluded faces, poor lighting — you can draw a bounding box yourself on the tag page. Click-drag on desktop; long-press-drag on mobile. Label the box with a name and save.

### Three-tier embedding fallback

After you save a manual box, Argus runs three attempts in order to extract a face embedding so the crop participates in future matching:

1. **Aligned (tier 1)** — RetinaFace re-runs on the cropped region to detect and align the face, then ArcFace extracts an embedding from the aligned face. Best quality. Most manual boxes on a reasonably visible face land here.
2. **Unaligned (tier 2)** — RetinaFace found nothing (face too obscured, too small, or at an extreme angle), so ArcFace runs directly on the raw crop without alignment. The embedding is real and participates in matching, but accuracy is lower than aligned. Useful for side profiles or partially covered faces.
3. **No embedding (tier 3)** — ArcFace also returned nothing. The detection is saved as a labelled crop and appears in the identity's gallery, but it won't match against future detections and won't strengthen the recognition model. Rare — typically only very small or heavily obscured faces.

### Border colors on the tag page

Manually drawn boxes are shown with a dashed border. The color indicates which tier was reached:

| Color | Meaning |
|---|---|
| Green | Aligned embedding extracted (tier 1) |
| Amber | Unaligned embedding extracted (tier 2) |
| Red | No embedding found (tier 3) |

Auto-detected boxes use a solid white border regardless of outcome.

### Embedding column in the detection list

The detection list on the tag page has an **Embedding** column. For manual detections it shows `aligned`, `unaligned`, or a dash, plus the similarity percentage when one was computed. For auto-detected faces it shows the similarity percentage alone (the embedding is always aligned for those). Objects show a dash.

### API

`POST /api/images/{source_image_id}/detections` creates a manual detection. The response includes `embedding_source: "aligned" | "raw" | null` so a client can see which tier succeeded. The same field is returned by `GET /api/images/{id}/faces`.

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"label": "Noah", "bbox": {"x": 120, "y": 80, "w": 60, "h": 80}}' \
  http://localhost:8100/api/images/42/detections
# → {"detection_id": 99, "label": "Noah", "embedding_source": "aligned", ...}
```

---

## Object detection models

Argus supports two kinds of object detection models, selectable from the **Models** page:

### Standard YOLO

Models: `yolov8n`, `yolov8s`, `yolov8m`, `yolov8x`, `yolo11n`

Detects a fixed set of 80 everyday object categories defined by the COCO dataset (people, vehicles, animals, furniture, food, etc.). Fast and consistent — the vocabulary is baked into the model weights and never changes. Use the **Object classes** setting to filter which of the 80 you care about.

### YOLO-World (open vocabulary)

Models: `yolov8s-worldv2`, `yolov8m-worldv2`, `yolov8l-worldv2`

Detects anything you describe in plain language. Instead of a fixed list of 80 categories, you define a vocabulary of words and phrases, and the model finds those things in photos.

**What is a "vocabulary"?** It's a list of physical things you want Argus to find and label. Each entry is a description of something that can appear in an image — "dog", "fire", "license plate", "person wearing a helmet". When Argus scans a photo, it looks for every item in your vocabulary and draws a bounding box around each one it finds.

**Can you use arbitrary words?** Mostly yes, within reason. YOLO-World understands natural language, so descriptions like "golden retriever", "broken window", or "person on a bicycle" work. Abstract or non-visual concepts ("happiness", "expensive") do not — if you couldn't point at it in a photo, it won't work.

**Why not just list thousands of things?** Two reasons:
- Every entry in the vocabulary adds a small amount of compute per image. 100–300 classes is a practical sweet spot; thousands would be noticeably slower.
- Precision degrades with a bloated list — more categories means more chances for false positives and misclassification between similar-sounding things.

**Default vocabulary:** Argus ships with ~160 classes covering all 80 COCO categories plus common additions: weapons, fire, smoke, license plates, face masks, extended vehicle types, more animal species, and other frequently useful categories. Edit the vocabulary in **Settings → YOLO-World vocabulary** to add or remove anything. Changes take effect on the next detection — no restart needed.

**When to use YOLO-World vs standard YOLO:** if the things you want to detect are all within COCO's 80 classes, standard YOLO is faster and more consistent. Switch to YOLO-World when you need to detect things outside that list — a specific vehicle type, safety equipment, environmental hazards, or anything else with a name.

---

## Environments

Each user has one or more named environments. All recognition data — identities, detections, enrolled faces, source images — is isolated per environment. Switching environments is instant; data in other environments is never visible.

**Use cases:**
- Separate a test or staging dataset from production data
- Run multiple independent recognition projects under one account
- Scope an API key to one dataset so a client app can only read/write its own data

**Managing environments:** go to **Account → Manage environments** (or navigate to `/environments`). You can create, rename, and delete environments there. Deleting an environment permanently removes all its data — crops, detections, identities, and embeddings.

**Switching:** use the environment name button in the top nav bar. The active environment is stored per user and restored on next login.

**API keys are environment-scoped.** Each key is bound to one environment at creation time. Requests authenticated by that key read and write only that environment's data, regardless of which environment the browser session has active. Choose the environment when creating a key on the **Account** page.

---

## API

All `/api/*` routes require an `X-API-Key` header **or** a valid browser session (for same-origin UI calls — no header needed when calling from the browser while logged in).

**Cross-origin requests (CORS):** Argus includes `CORSMiddleware` with `allow_origins=["*"]`, so browser-side calls from other LAN hosts (e.g. a separate web app on a different IP or port) work without a proxy. Authentication is still enforced via `X-API-Key` on every request.

**Get an API key:**
1. Sign in at `http://localhost:8100`
2. Go to **Account** → click **Create key**, choose an environment and type a label → copy the key (shown once — the last four characters are displayed afterwards as a hint so you can tell keys apart)

**Interactive docs:** `http://localhost:8100/docs`

**Public API reference:** `https://michaelyagi.github.io/argus/`

**Discovery (no key required):** `GET /api/health` reports status, version, active provider, and loaded models. `GET /api/capabilities` reports which detection types are usable, supported formats, pagination limits, and which integration features this build exposes — call it before integrating so a client can adapt instead of hardcoding.

**Example — detect faces:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@photo.jpg" \
  http://localhost:8100/api/detect/faces
```

**Example — detect with an inline label:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"image_url": "http://...", "label": "Noah"}' \
  http://localhost:8100/api/detect/faces
```

When `label` is provided, the highest-confidence face in the image is confirmed as that person and enrolled immediately, bypassing the review queue. Any other faces in the same image are stored as pending and appear in the review queue as normal.

**Example — bulk detect via URLs:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"image_urls": ["http://...", "http://..."], "type": "all"}' \
  http://localhost:8100/api/detect/bulk
```

**Example — re-detect a photo without piling up duplicates:**

Argus deduplicates source images by content hash, so re-detecting the same image
reuses its `source_image_id`. Pass `replace=true` to clear that image's prior
detections (of the type being run) before writing new ones — re-detection becomes
idempotent, and the client never needs to track IDs or delete anything first.

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@photo.jpg" \
  -F "replace=true" \
  http://localhost:8100/api/detect/all
```

**Example — delete a photo's detections entirely:**

```bash
curl -X DELETE \
  -H "X-API-Key: argus_..." \
  http://localhost:8100/api/images/42
# → {"source_image_id": 42, "detections_deleted": 5, "crops_removed": 5}
```

**Example — enroll a face:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "name=Noah" \
  -F "file=@noah.jpg" \
  http://localhost:8100/api/faces/enroll
```

**Facial attributes (age, gender, head pose).** Face detections include `age`, `gender`
(`"M"`/`"F"`), and `pose` (`[pitch, yaw, roll]` in degrees) when the active model provides
them — all three bundled packs (buffalo_l, buffalo_s, antelopev2) do. They're returned by
`/api/detect/*`, `/api/identify`, `/api/verify`, and `/api/images/{id}/faces`, and stored
per detection. Any value the model doesn't produce comes back as `null`. These fields are
API-only and not displayed in the UI.

**Example — identify (1:N, read-only): "who is in this photo?"**

Detects faces and matches them against your enrolled people **without storing anything**
(no crops, detections, or review entries). Best match per face plus ranked suggestions.

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@group.jpg" \
  http://localhost:8100/api/identify
# → {"threshold": 0.5, "faces": [
#      {"bbox": {...}, "confidence": 0.99, "identity_id": 2, "label": "Noah",
#       "similarity": 0.71, "suggestions": [...], "age": 9, "gender": "M", "pose": [...]}]}
```

Optional `threshold` (override) and `top_n` (suggestion count) via form field, JSON, or query.

**Example — test (read-only): "is there a person or object in this image, and who?"**

Detection plus read-only recognition — runs the face and object engines, returns bounding
boxes plus counts, and for each face attaches the **best matching enrolled person**
(`label` + `similarity`, highest score regardless of threshold; `null` if no one is enrolled).
**Stores nothing and enrolls nothing** — the match is a lookup, not a write. `?type=faces|objects|all`
(default `all`) selects which engines run; an engine with no active model is skipped (its list is
empty and `available` is false) rather than erroring. Also available as a UI page at `/test`.

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@photo.jpg" \
  "http://localhost:8100/api/test?type=all"
# → {"faces": [{"bbox": {...}, "confidence": 0.95, "label": "Noah", "similarity": 0.87,
#               "identity_id": 2, "age": 30, "gender": "F", "pose": [...]}],
#    "objects": [{"bbox": {...}, "confidence": 0.9, "class_name": "person", "class_id": 0}],
#    "counts": {"faces": 1, "objects": 1},
#    "available": {"faces": true, "objects": true}}
```

Batch variant — `POST /api/test/batch` tests many images in one call, still storing nothing.
Multipart: repeat `file` per image. JSON: `{"type": ..., "image_urls": [...], "image_base64": [...]}`.
Per-image results; one bad image never fails the rest (max 100 per call).

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "type=all" -F "file=@a.jpg" -F "file=@b.jpg" \
  http://localhost:8100/api/test/batch
# → {"total": 2, "type": "all", "results": [
#      {"index": 0, "filename": "a.jpg", "faces": [...], "objects": [...],
#       "counts": {...}, "available": {...}},
#      {"index": 1, "filename": "b.jpg", "error": "..."}]}
```

**Example — verify (1:1): "are these two faces the same person?"**

Compares two images directly. Stores nothing.

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file1=@a.jpg" -F "file2=@b.jpg" \
  http://localhost:8100/api/verify
# → {"similarity": 0.83, "match": true, "threshold": 0.5,
#    "face1": {"bbox": {...}, "confidence": 0.99, "age": 31, "gender": "F", "pose": [...]},
#    "face2": {...}}
```

Each image is one of `file{1,2}` / `image{1,2}_url` / `image{1,2}_base64`. Optional `threshold` override.

**Example — relabel a detection:**

```bash
curl -X PUT \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"label": "Sarah"}' \
  http://localhost:8100/api/detections/42/label
```

---

## Webhooks

Register HTTP callbacks to be notified when async jobs finish or new detections are created. Webhooks are per-environment and can filter on specific events.

**Supported events:** `detection.created` · `detection.labeled` · `detection.deleted` · `identity.created` · `identity.updated` · `identity.deleted` · `identity.merged` · `job.done`

**Managing webhooks from the UI:** go to **Account → Webhooks** (or navigate to `/webhooks`). Each webhook card shows its label, URL, event chips, and an Active toggle. From there you can create, edit, and delete webhooks via a modal form. The **Test** button sends a synchronous test ping and shows the HTTP status code and latency inline. Each card has an expandable **delivery log** showing the last 50 deliveries — timestamp, event, HTTP status, and round-trip duration — lazy-loaded on first expand with a Refresh button.

```bash
# Register a webhook
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"url": "https://my-server.local/hooks/argus", "events": ["job.done"], "label": "job alerts", "secret": "mysecret"}' \
  http://localhost:8100/api/webhooks

# List webhooks
curl -H "X-API-Key: argus_..." http://localhost:8100/api/webhooks

# Disable a webhook temporarily
curl -X PUT \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"is_active": false}' \
  http://localhost:8100/api/webhooks/1

# Send a test ping — returns {ok, status_code, duration_ms, error}
curl -X POST \
  -H "X-API-Key: argus_..." \
  http://localhost:8100/api/webhooks/1/test

# View delivery history (last 50, newest first)
curl -H "X-API-Key: argus_..." http://localhost:8100/api/webhooks/1/deliveries
```

When a `secret` is set, each request carries an `X-Argus-Signature: sha256=<hmac>` header computed over the JSON body. Verify it on your server to confirm the call is from Argus. Test pings are included in the delivery log (tagged `is_test: true`) and are signed the same way.

`detection.created` fires from all detect endpoints — `/api/detect/faces`, `/api/detect/objects`, `/api/detect/all`, manual detection (`POST /api/images/{id}/detections`), and the tag page label flow. `detection.labeled` fires whenever a detection is assigned or reassigned to an identity (confirm, reassign, relabel). `detection.deleted` fires when a source image or detection is deleted. `identity.*` events fire on create, update (rename, cover change, external_ref), delete, and merge. `job.done` fires when an async bulk detect or reprocess job completes.

---

## Bulk detection

Detect faces and/or objects across many images in a single API call. Each image can be a URL, file upload, or base64. One bad image never fails the others — per-image errors are reported inline.

**Sync mode** — waits for all images to complete and returns results directly. Good for small batches (under 20 images or so).

**Async mode** — returns a `job_id` immediately and processes in the background. Poll `GET /api/jobs/{job_id}` for status or register a `job.done` webhook to be notified.

```bash
# Sync bulk detect (URLs)
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"image_urls": ["http://cam1/snap.jpg", "http://cam2/snap.jpg"], "type": "faces"}' \
  http://localhost:8100/api/detect/bulk

# Async bulk detect — returns immediately with job_id
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"image_urls": ["http://cam1/snap.jpg", ...], "type": "all"}' \
  "http://localhost:8100/api/detect/bulk?async=true"
# → {"job_id": 7, "status": "pending", "total": 50}

# Check job status
curl -H "X-API-Key: argus_..." http://localhost:8100/api/jobs/7
```

---

## Identity merge

Combine two identities when the same person or object class was enrolled under different names. All detections and enrolled face references are moved to the target identity and the source is deleted.

```bash
# Merge identity 12 into identity 5 — all of 12's detections go to 5, identity 12 is deleted
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"into": 5}' \
  http://localhost:8100/api/identities/12/merge
# → {"merged_into": 5, "deleted": 12}
```

From the UI: open an identity's gallery and click **Merge into** in the page header. Type to search for the target identity, select it, and confirm.

---

## Reprocess

Re-run detection on a previously stored image using the currently active models. Useful when you switch to a better model and want to update results for existing photos.

```bash
# Re-detect all faces and objects in image 42 (keeps existing detections)
curl -X POST \
  -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images/42/reprocess?type=all"

# Replace existing face detections (clear and re-detect)
curl -X POST \
  -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images/42/reprocess?type=faces&replace=true"

# Async — process in background (same ?async=true as bulk detect)
curl -X POST \
  -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images/42/reprocess?type=all&replace=true&async=true"
```

Query params: `type=all|faces|objects` (default `all`), `replace=true` (clear prior results first), `async=true` (background job).

From the UI: open any image's tag page and click **Reprocess** in the header.

---

## Identity search

Search enrolled identities (people and object classes) by name using FTS5 trigram matching — handles partial names, case-insensitive, and near-misses. Available from the search bar in the top nav or via API.

```bash
# Find all identities matching "alice"
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/search?q=alice"

# Faces only, up to 20 results
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/search?q=john&type=face&limit=20"
```

Response:
```json
{
  "items": [
    {
      "id": 4,
      "label": "Alice",
      "type": "face",
      "cover_url": "/media/crops/abc123.jpg",
      "detection_count": 312
    }
  ]
}
```

Queries shorter than 3 characters fall back to a case-insensitive `LIKE` substring match; the same fallback fires if the FTS index is empty. The FTS index is built automatically on first startup — no migration steps required. `cover_url` is populated from the identity's oldest detection crop when no explicit cover has been selected, matching what the gallery page shows.

---

## Image filtering

Query the source image list with filters. All params are optional and combinable.

| Param | Type | Description |
|---|---|---|
| `identity_id` | int (repeatable) | Images that contain this identity (AND semantics across multiple) |
| `type` | `face` \| `object` | Only images with detections of this type |
| `since` / `until` | ISO timestamp | Upload date range |
| `no_detections` | bool | Images with zero detections |
| `no_tagged_faces` | bool | Images where no face has been identified |
| `no_crops` | bool | Images with no stored crop files |
| `has_manual_detections` | bool | Images with at least one manually drawn detection (mixed with auto is fine) |
| `sort` | `newest` \| `oldest` \| `most_detections` \| `fewest_detections` | Sort order (default: `newest`) |

```bash
# Images containing identity 5 (face or object)
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images?identity_id=5"

# Face-detection images from a date range
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images?type=face&since=2025-01-01T00:00:00&until=2025-12-31T23:59:59"

# Images with at least one manually drawn bbox, sorted by most detections
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images?has_manual_detections=true&sort=most_detections"
```

Companion endpoints use the same params: `GET /api/images/count` returns the matching total; `GET /api/images/ids` returns all matching IDs (no pagination, for select-all operations).

From the UI: the **Images** page has a filter bar — search for an identity by name, pick a detection type, set a date range, choose a preset filter (no tagged faces, manually tagged faces, no crops), pick a sort order, then click Apply.

---

## Export and import

Recognition data (identities, embeddings, detections, and images) can be exported and imported via the **Account** page or API. Imports merge by identity name — existing identities receive additional detections and embeddings rather than being duplicated.

```bash
# Export selected identities
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"identity_ids": [1, 2, 3]}' \
  http://localhost:8100/api/export \
  --output argus_export.zip

# Import into another instance
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@argus_export.zip" \
  http://localhost:8100/api/import
```

---

## Integrating another system

These endpoints make it easy for a client (a photo manager, a home server, any script) to
correlate its own records with Argus and stay in sync. All are generic — Argus never
interprets your identifiers.

**`external_ref` — attach your own id.** Every detect and enroll call accepts an optional
opaque `external_ref` string; it's stored on the source image (and, for enroll, the identity),
echoed back in responses, and queryable. Set it at creation and you never have to match by name
afterwards.

```bash
# Tag the image with your own id at detect time
curl -X POST -H "X-API-Key: argus_..." \
  -F "file=@photo.jpg" -F "external_ref=my-app-image-42" \
  http://localhost:8100/api/detect/all

# Resolve your id back to Argus's source_image_id
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/images?external_ref=my-app-image-42"

# Same for identities
curl -X POST -H "X-API-Key: argus_..." -H "Content-Type: application/json" \
  -d '{"label": "Noah", "type": "face", "external_ref": "my-app-person-7"}' \
  http://localhost:8100/api/identities
curl -H "X-API-Key: argus_..." \
  "http://localhost:8100/api/identities?external_ref=my-app-person-7"
# Backfill a ref onto an existing identity:
curl -X PUT -H "X-API-Key: argus_..." -H "Content-Type: application/json" \
  -d '{"external_ref": "my-app-person-7"}' \
  http://localhost:8100/api/identities/12/external_ref
```

**Change feed — sync deltas without re-scanning.** Poll `/api/changes?since=<cursor>` to learn
what changed (identities/detections created, relabeled, deleted). The returned `next_cursor` is
the value to pass as `since` next time. Detection events carry the source image's `external_ref`
so you know which of your records to update.

```bash
curl -H "X-API-Key: argus_..." "http://localhost:8100/api/changes?since=0&limit=100"
# → {"changes": [{"id": 1, "entity_type": "detection", "entity_id": 42,
#                 "action": "relabeled", "external_ref": "my-app-image-42", ...}],
#    "next_cursor": 1, "has_more": false}
```

**Capabilities — discover what this instance can do.** `/api/capabilities` reports which
detection types are usable right now, active models, supported formats, pagination limits, and
which integration features the build exposes — so a client can adapt instead of hardcoding.

```bash
curl http://localhost:8100/api/capabilities
```

**Batch operations.** Relabel or read many detections in one round-trip.

```bash
# Batch relabel — per-item results, one bad item never fails the others
curl -X POST -H "X-API-Key: argus_..." -H "Content-Type: application/json" \
  -d '{"items": [{"detection_id": 1, "label": "park bench"},
                 {"detection_id": 2, "identity_id": 5}]}' \
  http://localhost:8100/api/detections/label

# Batch read — current state of many detections (unknown ids simply absent)
curl -X POST -H "X-API-Key: argus_..." -H "Content-Type: application/json" \
  -d '{"detection_ids": [1, 2, 3]}' \
  http://localhost:8100/api/detections/query
```

---

## Troubleshooting

### GPU not detected (`active_provider: cpu` in `/api/health`)

**Check your GPU is visible:**
```bash
nvidia-smi
```

**Check if PyTorch sees CUDA:**
```bash
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

**Check if onnxruntime sees CUDA:**
```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

If PyTorch sees CUDA but onnxruntime only shows `CPUExecutionProvider`, the CUDA shared libraries aren't on the dynamic linker's path. Argus pre-loads them automatically at startup — if it still doesn't work, check whether both `onnxruntime` (CPU) and `onnxruntime-gpu` are installed:

```bash
python -m pip list | grep onnxruntime
```

If both appear, uninstall and reinstall only the GPU version:

```bash
python -m pip uninstall onnxruntime onnxruntime-gpu -y
python -m pip install onnxruntime-gpu
```

Then restart Argus. The health endpoint (`GET /api/health`) shows the active provider and loaded models — use it to confirm GPU is working.

### `libcudart.so.X: cannot open shared object file`

This means `onnxruntime-gpu` can't find the CUDA runtime library. Argus's startup code pre-loads CUDA libs from pip-installed nvidia packages automatically. If you still see this error:

1. Verify nvidia packages are installed in the same Python environment as Argus:
   ```bash
   python -m pip list | grep nvidia
   ```

2. If missing, reinstall PyTorch with CUDA:
   ```bash
   python -m pip install torch --index-url https://download.pytorch.org/whl/cu124
   ```

3. Verify onnxruntime-gpu version matches your CUDA version — onnxruntime-gpu 1.19+ bundles CUDA 12.x. If you're on CUDA 13.x (check with `torch.version.cuda`), ensure you have the matching onnxruntime-gpu.

### Face model shows `null` in `/api/health` after restart

The face engine failed to load silently at startup. Check the log for a `Failed to load face model` warning with a traceback. Most common causes:

- CUDA library not found (see above)
- Model files corrupted — re-download from the **Models** page
- Insufficient GPU memory — try a smaller model (`buffalo_s`)

You can force a reload without restarting by calling `PUT /api/models/{id}/activate`.

### Segfault on macOS / Apple Silicon when running object detection

`faiss-cpu` and `torch` (used by YOLO object detection) each bundle their own
OpenMP runtime, and loading both in one process segfaults on Apple Silicon —
typically the process dies the first time the object detector runs, while face
detection alone is fine.

Argus auto-detects macOS and sets `ARGUS_DISABLE_FAISS=true`, which skips faiss
entirely (it's never imported, so its OpenMP runtime never loads) and uses the
numpy matching fallback — equivalent results, fine until tens of thousands of
enrolled faces. No action needed; this is handled for you on the native run path.

If you still hit it (e.g. running uvicorn directly, bypassing `python -m app`),
set the flag yourself before starting:

```bash
export ARGUS_DISABLE_FAISS=true
```

Linux/CUDA is unaffected and keeps faiss for fast matching at scale.

---

## Development

Requires Python 3.11+.

```bash
pip install -r requirements.txt
pip install ruff pytest
ruff check .
pytest -v
```

The full API reference is available at `/docs` in a running instance, or at `https://michaelyagi.github.io/argus/`.

---

## Releasing a new version

The version string lives in `app/__init__.py` and is the single source of truth — the health endpoint, Docker image tag, and footer all read from it.

**1. Update the version**

Edit `app/__init__.py`:
```python
__version__ = "0.1.0-alpha.19"
```

**2. Update the changelog**

Add a new section at the top of `CHANGELOG.md` and a comparison link at the bottom.

**3. Run tests**

```bash
ruff check .
pytest -v
```

All checks must pass before tagging.

**4. Commit**

```bash
git add app/__init__.py CHANGELOG.md README.md
git commit -m "Release v0.1.0-alpha.19"
```

**5. Tag and push**

```bash
git tag v0.1.0-alpha.19
git push origin main
git push origin v0.1.0-alpha.19
```

**6. GitHub Actions takes over**

Pushing the tag triggers `release.yml` which re-runs the test suite as a gate, then builds and pushes the Docker image to `ghcr.io` and creates a GitHub Release with autogenerated notes. Monitor progress at `https://github.com/MichaelYagi/argus/actions`.

---

## License

MIT
