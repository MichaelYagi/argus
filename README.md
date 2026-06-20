# Argus

Self-hosted face and object recognition. Single Docker container, runs on your LAN.

**Features:**
- Face detection and recognition (InsightFace/ArcFace) — enroll people by name, match them across photos
- Object detection via YOLO (80 COCO classes)
- Per-user accounts with admin approval flow and individually managed, named API keys
- Review queue with ranked match suggestions and configurable auto-confirm threshold
- Justified infinite-scroll galleries per identity with cover photo selection and bulk operations
- Tag page — full source image with clickable face bbox overlays for labelling
- Export and import with merge — move recognition data between instances
- Live settings (thresholds, GPU, crop padding) — no restart needed
- Hot-swap models without restarting
- REST API — everything the UI does is available via API, fully paginated
- Mobile-responsive browser UI

**Supported image formats:** JPEG, PNG, WEBP, BMP, GIF (first frame), TIFF, HEIC/HEIF

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
5. Go to **Enroll** and enroll a face with a name
6. Go to **Detect** and drop in a photo — Argus will detect and match faces

**For object detection:** also download and activate a YOLO model from the Models page (`yolov8s` is a good balance of speed and accuracy). Object detection is independent of face recognition and optional.

---

## Review queue

Detections below the match threshold go into a review queue (`/review`). Each item shows:
- The detected face crop
- The current match (if any) and its similarity score
- Ranked suggestions from enrolled faces
- Actions: confirm, reject, reassign, or dismiss

A configurable auto-confirm threshold (`face.auto_confirm_threshold`, default 0.80) skips the queue for high-confidence matches.

---

## Native run (no Docker)

Requires Python 3.11+.

```bash
pip install -r requirements.txt
python -m app
```

Binds to `http://localhost:8100` by default. Pass `--host` and `--port` to override:

```bash
python -m app --host 0.0.0.0 --port 9000
```

Data is stored in `./data/` and model weights in `./models/` relative to the working directory.

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

### GPU support

Uncomment the `deploy` block in `docker-compose.yml`. Requires the NVIDIA Container Toolkit and Docker Desktop using the WSL2 backend with CUDA drivers configured. The app auto-detects GPU availability at runtime — no rebuild needed.

### ARM / Apple Silicon (M1/M2/M3)

Works natively. The image builds for ARM64 and `onnxruntime` (CPU) is installed automatically — `onnxruntime-gpu` has no ARM64 wheels. Everything runs correctly on CPU; Apple's Neural Engine is not used.

---

## API

All `/api/*` routes require an `X-API-Key` header **or** a valid browser session (for same-origin UI calls — no header needed when calling from the browser while logged in).

**Get an API key:**
1. Sign in at `http://localhost:8100`
2. Go to **Account** → type a label → **Create key** → copy the key (shown once)

**Interactive docs:** `http://localhost:8100/docs`

**Public API reference:** `https://michaelyagi.github.io/argus/`

**Example — detect faces:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "file=@photo.jpg" \
  http://localhost:8100/api/detect/faces
```

**Example — bulk detect via URLs:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"image_urls": ["http://...", "http://..."], "type": "all"}' \
  http://localhost:8100/api/detect/bulk
```

**Example — enroll a face:**

```bash
curl -X POST \
  -H "X-API-Key: argus_..." \
  -F "name=Noah" \
  -F "file=@noah.jpg" \
  http://localhost:8100/api/faces/enroll
```

**Example — relabel a detection:**

```bash
curl -X PUT \
  -H "X-API-Key: argus_..." \
  -H "Content-Type: application/json" \
  -d '{"label": "Sarah"}' \
  http://localhost:8100/api/detections/42/label
```

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

## License

MIT
