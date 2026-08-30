# Argus

> **AI-authored.** 100% of the code was written by Claude (Anthropic); I shaped the architecture, scope, and every decision, and did all the testing.

Self-hosted face and object recognition — a clean HTTP API for other apps *and* a full human curation UI in one container. Enroll people by name, match them across photos, review uncertain matches, and keep everything on your LAN.

**[Documentation →](https://michaelyagi.github.io/argus/)**

---

**Features:**
- Face detection and recognition (InsightFace/ArcFace) — enroll people by name, match across photos
- Object detection via standard YOLO (80 COCO classes) or YOLO-World (open vocabulary — detect anything)
- Review queue with ranked match suggestions, configurable thresholds, and auto-confirm
- Suggested people — clusters unlabeled faces into proposed identities; name a cluster to enroll everyone at once
- Manual face tagging with click-drag bbox drawing and a three-tier embedding fallback
- Per-user accounts, named API keys, and environments (isolate data into named workspaces)
- Webhooks, change feed, `external_ref` correlation ids, and batch endpoints for integration
- Bulk detection with async mode and job tracking
- Identity merge, reprocess, export and import
- Live settings and hot-swap models — no restart needed
- GPU auto-detected; full CPU fallback
- REST API — everything the UI does is available via API
- Mobile-responsive browser UI

**Supported image formats:** JPEG, PNG, WEBP, BMP, GIF (first frame), TIFF, HEIC/HEIF, AVIF, MPO (first frame)

---

## Quick start

```bash
git clone https://github.com/MichaelYagi/argus.git
cd argus
docker compose up --build
```

Open `http://localhost:8100`. The first user to sign up is the admin.

**Full setup guide, native run, GPU configuration, and API reference:**
**[michaelyagi.github.io/argus](https://michaelyagi.github.io/argus/)**

---

## License

MIT
