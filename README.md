# Argus

Self-hosted face and object recognition. Single Docker container, runs on your LAN.

**Features:**
- Face detection and recognition (InsightFace/ArcFace) — enroll people by name, match them across photos
- Labelling a person during detection auto-enrolls them as a reference — no separate enroll step needed
- Object detection via YOLO (80 COCO classes)
- Per-user accounts with admin approval flow and individually managed, named API keys
- Review queue with ranked match suggestions, configurable auto-confirm threshold, and auto-enroll on confirm
- Justified infinite-scroll galleries per identity with cover photo selection and bulk operations
- Tag page — full source image with clickable face bbox overlays for labelling
- Export and import with merge — move recognition data between instances
- Live settings (thresholds, GPU, crop padding) — no restart needed
- Hot-swap models without restarting
- Embedding averaging + faiss index for fast, accurate matching at scale
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
5. Go to **Enroll** and enroll a face with a name — or skip this and label faces directly on the **Detect** page
6. Go to **Detect** and drop in a photo — Argus will detect and match faces

**For object detection:** also download and activate a YOLO model from the Models page (`yolov8s` is a good balance of speed and accuracy). Object detection is independent of face recognition and optional.

---

## Review queue

Detections below the match threshold go into a review queue (`/review`). Each item shows:
- The detected face crop
- The current match (if any) and its similarity score
- Ranked suggestions from enrolled faces
- Actions: confirm, reject, reassign, or dismiss

Key thresholds (all configurable in **Settings**):
- `face.match_threshold` (default 0.5) — minimum similarity to assign a match at all
- `face.auto_confirm_threshold` (default 0.80) — skip the queue for high-confidence matches
- `face.auto_enroll_threshold` (default 0.92) — automatically add confirmed detections to the reference set

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

### GPU support (Docker)

Uncomment the `deploy` block in `docker-compose.yml`. Requires the NVIDIA Container Toolkit installed on the host and Docker Desktop using the WSL2 backend. The app auto-detects GPU availability at runtime — no rebuild needed.

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
