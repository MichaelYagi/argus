# Argus

Self-hosted face and object recognition. Single Docker container, runs on your LAN.

- Enroll faces, detect and match them across photos
- Object detection via YOLO (80 COCO classes)
- Per-user accounts with API keys
- Review queue for low-confidence matches
- Justified infinite-scroll galleries per identity
- REST API + browser UI

---

## Quick start (Docker)

**1. Clone and create your `.env`**

```bash
git clone https://github.com/YOUR_USERNAME/argus.git
cd argus
cp .env.example .env
```

Open `.env` and set a `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output into `.env`:

```
SECRET_KEY=a3f9c2d8e1b4...
```

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
2. Create an account — the first user is automatically the admin
3. Go to **Models** and download a face model (`buffalo_l` is recommended)
4. Wait for the download, then click **Activate**
5. Go to **Enroll** and enroll a face with a name
6. Go to **Detect** and drop in a photo — Argus will match against enrolled faces

To detect objects, download and activate a YOLO model from the Models page as well.

---

## Native run (no Docker)

Requires Python 3.11.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set SECRET_KEY
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

---

## API

All `/api/*` routes require an `X-API-Key` header.

**Get an API key:**
1. Sign in at `http://localhost:8100`
2. Go to `/keys` → Create → copy the key (shown once)

**Interactive docs:** `http://localhost:8100/docs`

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

---

## Development

```bash
pip install ruff pytest
ruff check .
pytest -v
```

See `DESIGN.md` for full architecture, schema, and API reference.

---

## License

MIT
