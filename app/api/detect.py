"""Detection routes — POST /api/detect/faces|objects|all."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api._utils import delete_crops, is_truthy
from app.core import settings_cache
from app.core import webhook as _webhook
from app.core.auth import require_auth, require_env_id
from app.core.image_input import (
    acquire_image,
    acquire_image_slot,
    decode_base64,
    fetch_url,
    open_and_validate,
    read_body_field,
    to_rgb_array,
)
from app.core.paths import crops_dir, sources_dir
from app.db import store
from app.inference.registry import registry
from app.inference.runner import infer_faces, infer_objects

router = APIRouter()

_FMT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp",
             "GIF": "gif", "TIFF": "tif", "HEIF": "heif", "AVIF": "avif"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/detect/faces")
async def detect_faces(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    run_async = is_truthy(request.query_params.get("async", ""))
    raw = await acquire_image(request)
    label = await _extract_label(request)
    replace = await _extract_replace(request)
    external_ref = await _extract_external_ref(request)
    if run_async:
        job_id = store.create_job(user_id, "detect_faces", environment_id)
        background_tasks.add_task(
            _run_detection_job, job_id, user_id, environment_id, raw, label, replace, "face", external_ref)
        return {"job_id": job_id, "status": "pending"}
    img = open_and_validate(raw)
    _, source_id = _save_source_image(user_id, environment_id, raw, img, external_ref)
    if replace:
        _clear_detections(user_id, environment_id, source_id, "face")
    result = {"source_image_id": source_id, "external_ref": external_ref,
              "faces": _run_faces(user_id, environment_id, img, source_id, label=label)}
    _emit_det(len(result["faces"]), 0, external_ref)
    from app.core import webhook
    webhook.fire(user_id, environment_id, "detection.created",
                 {"source_image_id": source_id, "external_ref": external_ref, "type": "face"})
    return result


@router.post("/api/detect/objects")
async def detect_objects(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    run_async = is_truthy(request.query_params.get("async", ""))
    raw = await acquire_image(request)
    replace = await _extract_replace(request)
    external_ref = await _extract_external_ref(request)
    if run_async:
        job_id = store.create_job(user_id, "detect_objects", environment_id)
        background_tasks.add_task(
            _run_detection_job, job_id, user_id, environment_id, raw, None, replace, "object", external_ref)
        return {"job_id": job_id, "status": "pending"}
    img = open_and_validate(raw)
    _, source_id = _save_source_image(user_id, environment_id, raw, img, external_ref)
    if replace:
        _clear_detections(user_id, environment_id, source_id, "object")
    objs, img_tags = _run_objects(user_id, environment_id, img, source_id)
    result: dict = {"source_image_id": source_id, "external_ref": external_ref, "objects": objs}
    if img_tags is not None:
        result["image_tags"] = img_tags
    _emit_det(0, len(objs), external_ref)
    from app.core import webhook
    webhook.fire(user_id, environment_id, "detection.created",
                 {"source_image_id": source_id, "external_ref": external_ref, "type": "object"})
    return result


@router.post("/api/detect/all")
async def detect_all(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    run_async = is_truthy(request.query_params.get("async", ""))
    raw = await acquire_image(request)
    label = await _extract_label(request)
    replace = await _extract_replace(request)
    external_ref = await _extract_external_ref(request)
    if run_async:
        job_id = store.create_job(user_id, "detect_all", environment_id)
        background_tasks.add_task(
            _run_detection_job, job_id, user_id, environment_id, raw, label, replace, "all", external_ref)
        return {"job_id": job_id, "status": "pending"}
    img = open_and_validate(raw)
    _, source_id = _save_source_image(user_id, environment_id, raw, img, external_ref)
    if replace:
        _clear_detections(user_id, environment_id, source_id, None)  # both faces and objects
    objs, img_tags = _run_objects(user_id, environment_id, img, source_id)
    result = {
        "source_image_id": source_id,
        "external_ref": external_ref,
        "faces": _run_faces(user_id, environment_id, img, source_id, label=label),
        "objects": objs,
    }
    if img_tags is not None:
        result["image_tags"] = img_tags
    _emit_det(len(result["faces"]), len(objs), external_ref)
    from app.core import webhook
    webhook.fire(user_id, environment_id, "detection.created",
                 {"source_image_id": source_id, "external_ref": external_ref, "type": "all"})
    return result


@router.post("/api/detect/bulk")
async def detect_bulk(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Batch detect across multiple images.

    Multipart: one or more ``file`` fields plus optional ``type`` field.
    JSON: ``{"image_urls": [...], "type": "faces|objects|all"}``
    """
    content_type = request.headers.get("content-type", "")
    detect_type = "all"
    jobs: list[tuple[str, bytes]] = []  # (label, raw_bytes)

    if "multipart/form-data" in content_type:
        form = await request.form()
        detect_type = str(form.get("type", "all"))
        files = form.getlist("file")
        if not files:
            raise HTTPException(400, "No files provided")
        for f in files:
            jobs.append((getattr(f, "filename", "") or "upload", await f.read()))
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        detect_type = body.get("type", "all")
        urls = body.get("image_urls", [])
        if not urls:
            raise HTTPException(400, "No image_urls provided")
        for url in urls:
            try:
                jobs.append((url, await fetch_url(url)))
            except HTTPException as exc:
                jobs.append((url, b"__error__:" + exc.detail.encode()))
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    if detect_type not in ("faces", "objects", "all"):
        raise HTTPException(400, "type must be faces, objects, or all")

    if is_truthy(request.query_params.get("async", "")):
        job_id = store.create_job(user_id, "detect_bulk", environment_id)
        background_tasks.add_task(_run_bulk_job, job_id, user_id, environment_id, jobs, detect_type)
        return {"job_id": job_id, "status": "pending", "total": len(jobs)}

    results = []
    for i, (label, raw) in enumerate(jobs):
        base: dict = {"index": i, "filename": label}
        if raw.startswith(b"__error__:"):
            base["error"] = raw[len(b"__error__:"):].decode()
            results.append(base)
            continue
        try:
            img = open_and_validate(raw)
            _, src_id = _save_source_image(user_id, environment_id, raw, img)
            base["source_image_id"] = src_id
            if detect_type in ("faces", "all"):
                base["faces"] = _run_faces(user_id, environment_id, img, src_id)
            if detect_type in ("objects", "all"):
                objs, img_tags = _run_objects(user_id, environment_id, img, src_id)
                base["objects"] = objs
                if img_tags is not None:
                    base["image_tags"] = img_tags
        except HTTPException as exc:
            base["error"] = exc.detail
        except Exception as exc:
            base["error"] = str(exc)
        results.append(base)

    return {"total": len(results), "type": detect_type, "results": results}


# ---------------------------------------------------------------------------
# Verify (1:1) and Identify (1:N, read-only) — neither stores anything
# ---------------------------------------------------------------------------

@router.post("/api/verify")
async def verify(request: Request, user_id: int = Depends(require_auth)):
    """1:1 face verification — are the two supplied images the same person?

    Two images, each as exactly one of file{n}/image{n}_url/image{n}_base64.
    Stores nothing. Uses the highest-confidence face in each image.
    """
    raw1 = await acquire_image_slot(request, 1)
    raw2 = await acquire_image_slot(request, 2)
    threshold = await _extract_threshold(request)

    _require_face_engine()  # 503 if no active face model
    if threshold is None:
        threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)

    face1 = _top_face(raw1)
    if face1 is None:
        raise HTTPException(400, "No face found in image 1")
    face2 = _top_face(raw2)
    if face2 is None:
        raise HTTPException(400, "No face found in image 2")

    sim = store.cosine_similarity(
        _embedding_to_bytes(face1.embedding), _embedding_to_bytes(face2.embedding)
    )
    sim = round(float(sim), 4) if sim is not None else 0.0
    return {
        "similarity": sim,
        "match": sim >= threshold,
        "threshold": threshold,
        "face1": _face_summary(face1),
        "face2": _face_summary(face2),
    }


@router.post("/api/identify")
async def identify(
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """1:N identification — who is each face, among this user's enrolled people?

    One image (file/image_url/image_base64). Read-only: stores nothing. Returns the
    best match per face (null below threshold, with the best-guess similarity) plus a
    ranked suggestion list.
    """
    raw = await acquire_image(request)
    threshold = await _extract_threshold(request)
    top_n = await _extract_top_n(request)

    _require_face_engine()
    if threshold is None:
        threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)

    from app.core import face_index

    img = open_and_validate(raw)
    faces, _ = infer_faces(to_rgb_array(img))

    results = []
    for det in faces:
        ranked = face_index.search(det.embedding, user_id, environment_id, threshold=0.0, k=top_n)
        suggestions = []
        for iid, s in ranked:
            ident = store.get_identity(iid, user_id, environment_id)
            if ident:
                suggestions.append({
                    "identity_id": iid,
                    "label": ident["label"],
                    "similarity": round(float(s), 4),
                })
        best = suggestions[0] if suggestions else None
        matched = best if (best and best["similarity"] >= threshold) else None
        results.append({
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": round(float(det.confidence), 4),
            "identity_id": matched["identity_id"] if matched else None,
            "label": matched["label"] if matched else None,
            "similarity": best["similarity"] if best else None,
            "suggestions": suggestions,
            **_face_attrs(det),
        })

    return {"threshold": threshold, "faces": results}


@router.post("/api/test")
async def test_detect(
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Stateless detection — is there a person / object in this image?

    One image (file/image_url/image_base64). Pure detection: runs the face and
    object engines and returns bboxes + counts. Stores nothing, enrolls nothing,
    matches nothing. ``?type=faces|objects|all`` (default all) selects which
    engines run; an engine that isn't loaded is simply skipped (its list is
    empty and ``available`` is false), so the call never 503s on a missing model.
    """
    type_param = (request.query_params.get("type") or "all").strip().lower()
    if type_param not in ("faces", "objects", "all"):
        raise HTTPException(400, "type must be faces, objects, or all")

    raw = await acquire_image(request)
    img = open_and_validate(raw)
    return _stateless_detect(img, type_param, user_id, environment_id)


def _stateless_detect(
    img: Any, type_param: str, user_id: int | None = None, environment_id: int | None = None,
) -> dict:
    """Run the requested engines on one image and return bboxes + counts.
    Stores nothing. Faces also get a read-only best-match (highest similarity, no
    threshold) against the caller's enrolled people. Shared by /api/test[/batch]."""
    img_array = to_rgb_array(img)

    faces: list[dict] = []
    objects: list[dict] = []
    face_available = False
    object_available = False
    image_tags: list[str] | None = None

    if type_param in ("faces", "all"):
        face_engine = registry.get_face_engine()
        if face_engine is not None:
            from app.core import face_index
            face_available = True
            for det in face_engine.detect(img_array):
                face = {
                    "bbox": {"x": det.bbox[0], "y": det.bbox[1],
                             "w": det.bbox[2], "h": det.bbox[3]},
                    "confidence": round(float(det.confidence), 4),
                    "identity_id": None, "label": None, "similarity": None,
                    **_face_attrs(det),
                }
                # Read-only identification — top match regardless of threshold. No writes.
                if user_id is not None:
                    ranked = face_index.search(det.embedding, user_id, environment_id, threshold=0.0, k=1)
                    if ranked:
                        iid, sim = ranked[0]
                        ident = store.get_identity(iid, user_id, environment_id)
                        if ident:
                            face["identity_id"] = iid
                            face["label"] = ident["label"]
                            face["similarity"] = round(float(sim), 4)
                faces.append(face)

    if type_param in ("objects", "all"):
        object_engine = registry.get_object_engine()
        if object_engine is not None:
            object_available = True
            if getattr(object_engine, "has_image_tags", False):
                image_tags, raw_dets = object_engine.detect_with_tags(img_array)
            else:
                raw_dets = object_engine.detect(img_array)
            for det in raw_dets:
                objects.append({
                    "bbox": {"x": det.bbox[0], "y": det.bbox[1],
                             "w": det.bbox[2], "h": det.bbox[3]},
                    "confidence": round(float(det.confidence), 4),
                    "class_name": det.class_name,
                    "class_id": det.class_id,
                })

    result: dict = {
        "faces": faces,
        "objects": objects,
        "counts": {"faces": len(faces), "objects": len(objects)},
        "available": {"faces": face_available, "objects": object_available},
    }
    if image_tags is not None:
        result["image_tags"] = image_tags
    return result


_TEST_BATCH_MAX = 100


@router.post("/api/test/batch")
async def test_detect_batch(
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Stateless batch detection — multiple images in one call, stores nothing.

    multipart/form-data: repeat `file` for each image (plus optional `type`).
    application/json: ``{"type": "...", "image_urls": [...], "image_base64": [...]}``.
    Per-image results — one bad image never fails the rest. Mirrors /api/detect/bulk
    but read-only (no source images, detections, crops, or matching)."""
    content_type = request.headers.get("content-type", "")
    detect_type = "all"
    jobs: list[tuple[str, bytes]] = []  # (label, raw_bytes); raw may be an __error__ sentinel

    if "multipart/form-data" in content_type:
        form = await request.form()
        detect_type = str(form.get("type", "all"))
        files = form.getlist("file")
        if not files:
            raise HTTPException(400, "No files provided")
        for f in files:
            jobs.append((getattr(f, "filename", "") or "upload", await f.read()))
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        detect_type = body.get("type", "all")
        urls = body.get("image_urls", []) or []
        b64s = body.get("image_base64", []) or []
        if not urls and not b64s:
            raise HTTPException(400, "Provide image_urls and/or image_base64")
        for url in urls:
            try:
                jobs.append((url, await fetch_url(url)))
            except HTTPException as exc:
                jobs.append((url, b"__error__:" + str(exc.detail).encode()))
        for i, b in enumerate(b64s):
            try:
                jobs.append((f"base64[{i}]", decode_base64(b)))
            except HTTPException as exc:
                jobs.append((f"base64[{i}]", b"__error__:" + str(exc.detail).encode()))
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    if detect_type not in ("faces", "objects", "all"):
        raise HTTPException(400, "type must be faces, objects, or all")
    if len(jobs) > _TEST_BATCH_MAX:
        raise HTTPException(400, f"Too many images (max {_TEST_BATCH_MAX})")

    results = []
    for i, (label, raw) in enumerate(jobs):
        base: dict = {"index": i, "filename": label}
        if raw.startswith(b"__error__:"):
            base["error"] = raw[len(b"__error__:"):].decode()
            results.append(base)
            continue
        try:
            img = open_and_validate(raw)
            base.update(_stateless_detect(img, detect_type, user_id, environment_id))
        except HTTPException as exc:
            base["error"] = exc.detail
        except Exception as exc:
            base["error"] = str(exc)
        results.append(base)

    return {"total": len(results), "type": detect_type, "results": results}


# ---------------------------------------------------------------------------
# Async job runner
# ---------------------------------------------------------------------------

def _run_detection_job(
    job_id: str,
    user_id: int,
    environment_id: int,
    raw: bytes,
    label: str | None,
    replace: bool,
    det_type: str,  # 'face' | 'object' | 'all'
    external_ref: str | None = None,
) -> None:
    from app.core import webhook
    try:
        store.update_job(job_id, "running")
        img = open_and_validate(raw)
        _, source_id = _save_source_image(user_id, environment_id, raw, img, external_ref)
        if replace:
            _clear_detections(user_id, environment_id, source_id, None if det_type == "all" else det_type)
        result: dict = {"source_image_id": source_id, "external_ref": external_ref}
        if det_type in ("face", "all"):
            result["faces"] = _run_faces(user_id, environment_id, img, source_id, label=label)
        if det_type in ("object", "all"):
            objs, img_tags = _run_objects(user_id, environment_id, img, source_id)
            result["objects"] = objs
            if img_tags is not None:
                result["image_tags"] = img_tags
        _emit_det(len(result.get("faces", [])), len(result.get("objects", [])), external_ref)
        store.update_job(job_id, "complete", result)
        webhook.fire(user_id, environment_id, "job.done", {"job_id": job_id, "status": "complete", **result})
    except HTTPException as exc:
        store.update_job(job_id, "failed", {"error": exc.detail})
        webhook.fire(user_id, environment_id, "job.done", {"job_id": job_id, "status": "failed", "error": exc.detail})
    except Exception as exc:
        store.update_job(job_id, "failed", {"error": str(exc)})
        webhook.fire(user_id, environment_id, "job.done", {"job_id": job_id, "status": "failed", "error": str(exc)})


def _run_bulk_job(
    job_id: str,
    user_id: int,
    environment_id: int,
    jobs: list[tuple[str, bytes]],
    detect_type: str,
) -> None:
    from app.core import webhook
    results = []
    store.update_job(job_id, "running")
    for i, (label, raw) in enumerate(jobs):
        base: dict = {"index": i, "filename": label}
        if raw.startswith(b"__error__:"):
            base["error"] = raw[len(b"__error__:"):].decode()
            results.append(base)
            continue
        try:
            img = open_and_validate(raw)
            _, src_id = _save_source_image(user_id, environment_id, raw, img)
            base["source_image_id"] = src_id
            if detect_type in ("faces", "all"):
                base["faces"] = _run_faces(user_id, environment_id, img, src_id)
            if detect_type in ("objects", "all"):
                objs, img_tags = _run_objects(user_id, environment_id, img, src_id)
                base["objects"] = objs
                if img_tags is not None:
                    base["image_tags"] = img_tags
        except HTTPException as exc:
            base["error"] = exc.detail
        except Exception as exc:
            base["error"] = str(exc)
        results.append(base)
        store.update_job(job_id, "running", {"processed": i + 1, "total": len(jobs)})
    nf = sum(len(r.get("faces", [])) for r in results if "faces" in r)
    no = sum(len(r.get("objects", [])) for r in results if "objects" in r)
    n_imgs = sum(1 for r in results if "source_image_id" in r)
    if n_imgs:
        _emit_det(nf, no, f"{n_imgs} image{'s' if n_imgs != 1 else ''}")
    result = {"total": len(results), "type": detect_type, "results": results}
    store.update_job(job_id, "complete", result)
    webhook.fire(user_id, environment_id, "job.done", {"job_id": job_id, "status": "complete", **result})


# ---------------------------------------------------------------------------
# Persistence phase — matching, crop saves, DB writes
# (Inference phase lives in app/api/_infer.py — infer_faces / infer_objects)
# ---------------------------------------------------------------------------

async def _extract_label(request: Request) -> str | None:
    return ((await read_body_field(request, "label")) or "").strip() or None


async def _extract_external_ref(request: Request) -> str | None:
    raw = request.query_params.get("external_ref") or await read_body_field(request, "external_ref")
    return (raw or "").strip() or None


def _emit_det(nf: int, no: int, ref: str | None = None) -> None:
    from app.core import activity_buffer as _ab
    parts: list[str] = []
    if nf:
        parts.append(f"{nf} face{'s' if nf != 1 else ''}")
    if no:
        parts.append(f"{no} object{'s' if no != 1 else ''}")
    if not parts:
        return
    msg = ", ".join(parts) + " detected"
    if ref:
        msg += f" ({ref})"
    _ab.emit("detection", msg)


async def _extract_replace(request: Request) -> bool:
    if is_truthy(request.query_params.get("replace", "")):
        return True
    return is_truthy(await read_body_field(request, "replace") or "")


def _clear_detections(user_id: int, environment_id: int, source_id: int, det_type: str | None) -> None:
    """Remove prior detections (and their crop files) for a source image.
    References enrolled from those crops are dropped too (in the store call), so
    refresh the match index to drop them before re-detecting."""
    crops = store.clear_detections_for_source(source_id, user_id, det_type, environment_id)
    delete_crops(crops)
    if det_type != "object":
        from app.core import face_index
        face_index.rebuild_user(user_id, environment_id)


def _run_faces(user_id: int, environment_id: int, img: Any, source_id: int, label: str | None = None) -> list[dict]:
    # Inference phase
    img_array = to_rgb_array(img)
    detections, model_row = infer_faces(img_array)

    # Persistence phase
    threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)
    auto_confirm_on = settings_cache.cache.get_or("face.auto_confirm", True)
    auto_confirm = settings_cache.cache.get_or("face.auto_confirm_threshold", 0.80)
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    save_unknown = settings_cache.cache.get_or("system.save_unknown_detections", True)

    # When a label is provided, only the highest-confidence detection gets that
    # identity confirmed. All other faces are treated as unidentified and go to
    # the review queue — same as a no-label detect call.
    labeled_id: int | None = None
    if label and detections:
        best = max(detections, key=lambda d: d.confidence)
        labeled_id = id(best)

    results = []
    for det in detections:
        identity_id, sim = _match_face(det.embedding, model_row["id"], user_id, environment_id, threshold)

        if label and id(det) == labeled_id:
            # Caller asserted this specific face — confirm it directly.
            identity_id = store.get_or_create_identity(user_id, "face", label, environment_id)
            sim = 1.0
            review_status = "confirmed"
        else:
            if identity_id is None and not save_unknown:
                continue
            review_status = (
                "confirmed"
                if identity_id is not None and auto_confirm_on and sim >= auto_confirm
                else "pending"
            )

        attrs = _face_attrs(det)
        crop_filename = _save_crop(img, det.bbox, padding)
        detection_id = store.insert_detection(
            user_id=user_id,
            environment_id=environment_id,
            identity_id=identity_id,
            source_image_id=source_id,
            detection_type="face",
            model_id=model_row["id"],
            confidence=det.confidence,
            bbox_x=det.bbox[0],
            bbox_y=det.bbox[1],
            bbox_w=det.bbox[2],
            bbox_h=det.bbox[3],
            crop_path=crop_filename,
            embedding=_embedding_to_bytes(det.embedding),
            review_status=review_status,
            attributes=json.dumps(attrs),
        )

        display_label = label if (label and id(det) == labeled_id) else None
        if display_label is None and identity_id is not None:
            row = store.get_identity(identity_id, user_id, environment_id)
            display_label = row["label"] if row else None

        if label and id(det) == labeled_id:
            from app.api.enroll import enroll_from_detection
            enroll_from_detection(store.get_detection(detection_id, user_id, environment_id), user_id, environment_id)

        results.append({
            "detection_id": detection_id,
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": det.confidence,        # face-detection quality score
            "similarity": round(float(sim), 4),  # match strength vs the enrolled identity
            "identity_id": identity_id,
            "label": display_label,
            "crop_url": f"/media/crops/{crop_filename}",
            "review_status": review_status,
            **attrs,
        })

    return results


def _run_objects(
    user_id: int, environment_id: int, img: Any, source_id: int
) -> tuple[list[dict], list[str] | None]:
    """Run object/tagger detection. Returns (detections, image_tags).

    image_tags is a list of keyword strings when the active engine is a tagger
    (RAM++ + Grounding DINO); None for all other engines.
    """
    # Inference phase
    img_array = to_rgb_array(img)
    raw_dets, image_tags, model_row = infer_objects(img_array)

    # Persistence phase
    if image_tags:
        store.set_source_image_tags(source_id, json.dumps(image_tags))

    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    results = []
    for det in raw_dets:
        identity_id = store.get_or_create_identity(user_id, "object", det.class_name, environment_id)
        crop_filename = _save_crop(img, det.bbox, padding)
        detection_id = store.insert_detection(
            user_id=user_id,
            environment_id=environment_id,
            identity_id=identity_id,
            source_image_id=source_id,
            detection_type="object",
            model_id=model_row["id"],
            confidence=det.confidence,
            bbox_x=det.bbox[0],
            bbox_y=det.bbox[1],
            bbox_w=det.bbox[2],
            bbox_h=det.bbox[3],
            crop_path=crop_filename,
        )
        results.append({
            "detection_id": detection_id,
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": det.confidence,
            "class_name": det.class_name,
            "class_id": det.class_id,
            "identity_id": identity_id,
            "label": det.class_name,
            "crop_url": f"/media/crops/{crop_filename}",
            "review_status": "pending",
        })

    return results, image_tags


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_source_image(
    user_id: int, environment_id: int, raw_bytes: bytes, img: Any, external_ref: str | None = None,
) -> tuple[str, int]:
    if settings_cache.cache.get_or("system.compress_on_ingest", True):
        quality = max(1, min(95, int(settings_cache.cache.get_or("system.ingest_jpeg_quality", 85))))
        buf = io.BytesIO()
        src = img if img.mode == "RGB" else img.convert("RGB")
        src.save(buf, "JPEG", quality=quality)
        raw_bytes = buf.getvalue()
        ext = "jpg"
    else:
        ext = _FMT_EXT.get(img.format or "JPEG", "jpg")
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    filename = f"{content_hash}.{ext}"
    dest = sources_dir() / filename
    if not dest.exists():
        sources_dir().mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
    source_id = store.get_or_create_source_image(
        user_id, filename, img.width, img.height, environment_id, external_ref,
    )
    return filename, source_id


def _save_crop(img: Any, bbox: tuple[int, int, int, int], padding: float) -> str:
    x, y, w, h = bbox
    pad_x = int(w * padding)
    pad_y = int(h * padding)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(img.width, x + w + pad_x)
    y2 = min(img.height, y + h + pad_y)
    crop = img.crop((x1, y1, x2, y2))
    if crop.mode != "RGB":
        crop = crop.convert("RGB")
    crop_dir = crops_dir()
    crop_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    crop.save(crop_dir / filename, "JPEG")
    return filename


def _face_attrs(det: Any) -> dict:
    """Serializable facial attributes for a FaceDetection.
    Values are None when the loaded model pack didn't provide them."""
    return {
        "age": det.age,
        "gender": det.gender,
        "pose": list(det.pose) if det.pose is not None else None,
        "mask": det.mask,
        "kps": det.kps,
        "landmark_2d_106": det.landmark_2d_106,
        "landmark_3d_68": det.landmark_3d_68,
    }


def _require_face_engine() -> None:
    """Raise 503 if no active face model / engine is loaded."""
    if store.get_active_model("face") is None:
        raise HTTPException(503, "No active face model. Download and activate one via /api/models.")
    if registry.get_face_engine() is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")


def _top_face(raw: bytes) -> Any | None:
    """Highest-confidence face in an image, or None if no face is found."""
    img = open_and_validate(raw)
    faces, _ = infer_faces(to_rgb_array(img))
    return max(faces, key=lambda f: f.confidence) if faces else None


def _face_summary(face: Any) -> dict:
    """Compact face description for the verify response."""
    return {
        "bbox": {"x": face.bbox[0], "y": face.bbox[1], "w": face.bbox[2], "h": face.bbox[3]},
        "confidence": round(float(face.confidence), 4),
        **_face_attrs(face),
    }


async def _extract_threshold(request: Request) -> float | None:
    raw = request.query_params.get("threshold") or await read_body_field(request, "threshold")
    if not raw or not str(raw).strip():
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "threshold must be a number between 0 and 1")
    if not 0.0 <= val <= 1.0:
        raise HTTPException(400, "threshold must be between 0 and 1")
    return val


async def _extract_top_n(request: Request, default: int = 5) -> int:
    raw = request.query_params.get("top_n") or await read_body_field(request, "top_n")
    if not raw or not str(raw).strip():
        return default
    try:
        return max(1, min(20, int(raw)))
    except (TypeError, ValueError):
        return default


def _embedding_to_bytes(embedding: Any) -> bytes | None:
    try:
        import numpy as np
        result = np.asarray(embedding, dtype=np.float32).tobytes()
        return result if isinstance(result, bytes) else None
    except Exception:
        return None


def _match_face(
    embedding: Any, model_id: int, user_id: int, environment_id: int, threshold: float
) -> tuple[int | None, float]:
    from app.core import face_index
    results = face_index.search(embedding, user_id, environment_id, threshold=threshold, k=1)
    if results:
        return results[0]
    # Below threshold — get best anyway for review queue context
    best = face_index.search(embedding, user_id, environment_id, threshold=0.0, k=1)
    return (None, best[0][1]) if best else (None, 0.0)


def scan_unidentified(user_id: int, environment_id: int) -> dict:
    """Retroactively match unidentified face detections against enrolled identities.

    Uses the same two-threshold logic as at-detection-time:
    - sim >= auto_confirm_threshold  → confirmed, goes directly into the identity gallery
    - match_threshold <= sim < auto_confirm  → pending, goes to the review queue only

    Deduplicates: one identity gets at most one detection per source image. If the same
    source image already has a detection for a given identity (from a prior detect run or
    a previous scan), any duplicate unidentified detection for that source is dismissed.
    """
    import numpy as np

    model_row = store.get_active_model("face")
    if not model_row:
        return {"scanned": 0, "confirmed": 0, "pending": 0}
    threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)
    auto_confirm_thr = settings_cache.cache.get_or("face.auto_confirm_threshold", 0.80)
    rows = store.get_unknown_face_embeddings(user_id, model_row["id"], environment_id)

    # Pre-populate seen with (source_image_id, identity_id) pairs that already exist
    # in the DB, so duplicate unidentified detections from the same image are dismissed.
    seen: set[tuple[int, int]] = store.get_identity_source_pairs(user_id, environment_id)

    # Score every row, sort highest-confidence first so the best crop wins deduplication.
    scored: list[tuple[float, Any, int]] = []
    for row in rows:
        try:
            emb = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
        except Exception:
            continue
        identity_id, sim = _match_face(emb, model_row["id"], user_id, environment_id, threshold)
        if identity_id is not None:
            scored.append((sim, row, identity_id))
    scored.sort(key=lambda x: x[0], reverse=True)

    confirmed = pending = dismissed = 0
    for sim, row, identity_id in scored:
        key = (int(row["source_image_id"]), identity_id)
        if key in seen:
            store.dismiss_detections(user_id, [row["id"]], environment_id)
            dismissed += 1
            continue
        seen.add(key)
        if sim >= auto_confirm_thr:
            store.label_detection(row["id"], user_id, identity_id, environment_id)
            _webhook.fire_detection_labeled(row["id"], user_id, environment_id, identity_id=identity_id)
            confirmed += 1
        else:
            store.suggest_detection(row["id"], user_id, identity_id, environment_id)
            pending += 1

    if confirmed or pending:
        from app.core import activity_buffer as _ab
        parts = []
        if confirmed:
            parts.append(f"{confirmed} confirmed")
        if pending:
            parts.append(f"{pending} sent to review")
        _ab.emit("identity", f"Retroactive scan: {', '.join(parts)}")
    return {"scanned": len(rows), "confirmed": confirmed, "pending": pending, "dismissed": dismissed}


@router.post("/api/faces/scan", status_code=200)
async def scan_faces_endpoint(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Match all unidentified face detections against enrolled identities.
    Returns counts of detections scanned and newly matched."""
    return scan_unidentified(user_id, environment_id)
