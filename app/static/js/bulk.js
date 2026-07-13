'use strict';


async function _pool(items, concurrency, fn) {
  let idx = 0;
  async function worker() {
    while (idx < items.length) {
      const item = items[idx++];
      await fn(item);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
}

(() => {
  const dropZone   = document.getElementById('drop-zone');
  const fileInput  = document.getElementById('file-input');
  const urlInput   = document.getElementById('url-input');
  const b64Input   = document.getElementById('b64-input');
  const modeSelect = document.getElementById('detect-mode');
  const runBtn       = document.getElementById('run-btn');
  const queueCount   = document.getElementById('queue-count');
  const previewStrip = document.getElementById('preview-strip');
  const resultsEl    = document.getElementById('results');
  const progressWrap = document.getElementById('progress-bar-wrap');
  const progressBar  = document.getElementById('progress-bar');
  const progressLbl  = document.getElementById('progress-label');

  let fileList = [];

  // ---------------------------------------------------------------------------
  // File selection
  // ---------------------------------------------------------------------------
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('over');
    addFiles([...e.dataTransfer.files]);
  });
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => addFiles([...fileInput.files]));
  document.getElementById('detect-camera')
    ?.addEventListener('click', () => capturePhoto(file => addFiles([file])));

  function addFiles(newFiles) {
    fileList = [...fileList, ...newFiles];
    updateQueue();
  }

  urlInput.addEventListener('input', updateQueue);
  if (b64Input) b64Input.addEventListener('input', updateQueue);

  function getUrls() {
    return urlInput.value.split('\n').map(s => s.trim()).filter(Boolean);
  }

  function getB64() {
    const v = b64Input?.value.trim();
    return v ? [v] : [];
  }

  function thumbStyle() {
    return 'width:48px;height:48px;object-fit:cover;border-radius:4px;' +
           'border:1px solid var(--border);flex-shrink:0';
  }

  function updateQueue() {
    const urls    = getUrls();
    const b64list = getB64();
    const total   = fileList.length + urls.length + b64list.length;
    runBtn.disabled = total === 0;
    queueCount.textContent = '';

    if (!previewStrip) return;
    previewStrip.innerHTML = '';

    if (total === 0) { previewStrip.style.display = 'none'; return; }
    previewStrip.style.display = 'flex';

    // File thumbnails — instant via object URL; × removes the file from the queue
    fileList.forEach((f, i) => {
      const img = document.createElement('img');
      img.src   = URL.createObjectURL(f);
      img.title = f.name;
      img.style.cssText = thumbStyle();
      img.onload = () => URL.revokeObjectURL(img.src);
      previewStrip.appendChild(removableThumb(img, () => { fileList.splice(i, 1); updateQueue(); }));
    });

    // URL thumbnails — attempt to load, fall back to placeholder on error
    urls.forEach((url, i) => {
      const img = document.createElement('img');
      img.src   = url;
      img.title = url;
      img.style.cssText = thumbStyle();
      const removeUrl = () => {
        urlInput.value = getUrls().filter((_, j) => j !== i).join('\n');
        updateQueue();
      };
      img.onerror = () => {
        const ph = document.createElement('div');
        ph.title = url;
        ph.style.cssText = thumbStyle() + ';background:var(--border);display:flex;' +
          'align-items:center;justify-content:center;font-size:9px;color:var(--muted);' +
          'text-align:center;padding:2px;box-sizing:border-box;overflow:hidden';
        ph.textContent = 'URL';
        img.replaceWith(ph);
      };
      previewStrip.appendChild(removableThumb(img, removeUrl));
    });

    // Base64 thumbnail
    b64list.forEach(b64 => {
      const img = document.createElement('img');
      const src = b64.startsWith('data:') ? b64 : 'data:image/jpeg;base64,' + b64;
      img.src   = src;
      img.title = 'base64 image';
      img.style.cssText = thumbStyle();
      img.onerror = () => {
        const ph = document.createElement('div');
        ph.style.cssText = thumbStyle() + ';background:var(--border);display:flex;' +
          'align-items:center;justify-content:center;font-size:9px;color:var(--muted)';
        ph.textContent = 'B64';
        img.replaceWith(ph);
      };
      previewStrip.appendChild(removableThumb(img, () => { if (b64Input) b64Input.value = ''; updateQueue(); }));
    });
  }

  // Wrap a thumbnail with a red × overlay that removes it from the upload queue.
  function removableThumb(child, onRemove) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;display:inline-block;line-height:0;flex-shrink:0';
    wrap.appendChild(child);
    const x = document.createElement('button');
    x.type = 'button';
    x.title = 'Remove';
    x.textContent = '×';
    x.style.cssText = 'position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;' +
      'border:none;background:var(--danger);color:#fff;font-size:11px;line-height:18px;text-align:center;' +
      'cursor:pointer;padding:0';
    x.addEventListener('click', e => { e.stopPropagation(); onRemove(); });
    wrap.appendChild(x);
    return wrap;
  }

  // ---------------------------------------------------------------------------
  // Processing
  // ---------------------------------------------------------------------------
  runBtn.addEventListener('click', async () => {
    const mode = modeSelect.value;
    const urls = getUrls();
    const total = fileList.length + urls.length;
    if (!total) return;

    runBtn.disabled = true;
    resultsEl.innerHTML = '';
    progressWrap.hidden = false;
    let done = 0;

    function updateProgress() {
      const pct = Math.round((done / total) * 100);
      progressBar.style.width = pct + '%';
      progressLbl.textContent = `${done} / ${total} processed`;
    }
    updateProgress();

    // Pre-create result rows so order is stable
    const rows = [];
    for (let i = 0; i < total; i++) {
      const row = document.createElement('div');
      row.className = 'card';
      row.style.cssText = 'margin-bottom:10px;display:flex;gap:12px;align-items:flex-start';
      const label = i < fileList.length ? fileList[i].name : urls[i - fileList.length];
      row.innerHTML = `
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:500;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
               title="${esc(label)}">${esc(label)}</div>
          <div class="muted" id="bulk-status-${i}" style="font-size:12px">Waiting…</div>
        </div>`;
      resultsEl.appendChild(row);
      rows.push(row);
    }

    // Build job list then process in parallel (max 3 concurrent)
    const b64List = getB64();
    const jobs = [
      ...fileList.map((f, idx) => ({ i: idx,                               type: 'file',   payload: f, label: f.name })),
      ...urls.map((u, idx)     => ({ i: fileList.length + idx,             type: 'url',    payload: u, label: u      })),
      ...b64List.map((b, idx)  => ({ i: fileList.length + urls.length + idx, type: 'base64', payload: b, label: 'base64 image' })),
    ];

    await _pool(jobs, 3, async job => {
      setStatus(job.i, job.type === 'file' ? 'Processing…' : 'Fetching…', 'muted');
      const fd = new FormData();
      if (job.type === 'file')        fd.append('file',          job.payload);
      else if (job.type === 'url')    fd.append('image_url',     job.payload);
      else if (job.type === 'base64') fd.append('image_base64',  job.payload);
      try {
        const resp = await fetch(`/api/detect/${mode}`, { method: 'POST', body: fd });
        const data = await resp.json();
        renderResult(rows[job.i], job.i, job.label, resp.ok, data, mode);
      } catch (err) {
        // A network error here used to break the whole batch — keep going, show it.
        renderResult(rows[job.i], job.i, job.label, false, { detail: 'Network error' }, mode);
        if (window.showToast) showToast('Detection failed for ' + job.label + ' (network error).', 'error');
      } finally {
        done++;
        updateProgress();
      }
    });

    progressLbl.textContent = `Done — ${total} image${total === 1 ? '' : 's'} processed`;
    runBtn.disabled = false;
    // Clear queue state and previews after processing
    fileList = [];
    if (urlInput) urlInput.value = '';
    if (b64Input) b64Input.value = '';
    if (previewStrip) { previewStrip.innerHTML = ''; previewStrip.style.display = 'none'; }
  });

  function setStatus(i, msg, cls = '') {
    const el = document.getElementById('bulk-status-' + i);
    if (el) { el.textContent = msg; el.className = cls; }
  }

  function renderResult(row, i, label, ok, data, mode) {
    if (!ok) {
      row.innerHTML = `
        <div style="flex:1">
          <div style="font-size:13px;font-weight:500;margin-bottom:4px">${esc(label)}</div>
          <div class="alert alert-error" style="margin:0">${esc(data.detail || 'Error')}</div>
        </div>`;
      return;
    }

    const faces      = data.faces   || [];
    const objects    = data.objects || [];
    const srcId      = data.source_image_id;
    const srcScale   = data.source_scale || 1;
    const allDets    = [...faces, ...objects];
    const discarded  = !!data.discarded;

    const summary = [];
    if (mode !== 'objects' && faces.length)   summary.push(`${faces.length} face${faces.length === 1 ? '' : 's'}`);
    if (mode !== 'faces'   && objects.length) summary.push(`${objects.length} object${objects.length === 1 ? '' : 's'}`);
    const desc = summary.length ? summary.join(', ') + ' detected' : 'Nothing detected';

    const tagLink = srcId && !discarded ? `<a href="/tag/${srcId}" style="font-size:12px">Tag</a>` : '';
    const thumbs = allDets.slice(0, 8).map(d =>
      `<img src="${d.crop_url}" title="${esc(d.label || d.class_name || 'Unknown')}"
            style="width:48px;height:48px;object-fit:cover;border-radius:3px">`
    ).join('');

    row.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1"
                title="${esc(label)}">${esc(label)}</span>
          <span class="muted" style="font-size:11px;white-space:nowrap">${desc}</span>
          ${tagLink}
        </div>
        ${discarded
          ? `<div class="alert alert-warning" style="margin:0">No detections — image was not saved.</div>`
          : (thumbs ? `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">${thumbs}</div>` : '') +
            (allDets.length ? `<div id="overlay-wrap-${i}" style="position:relative;display:inline-block;max-width:100%"></div>` : '') +
            (faces.length && objects.length ? `<div style="display:flex;gap:8px;margin-top:8px">
              <button class="btn btn-ghost tag-toggle active" data-kind="face">Faces</button>
              <button class="btn btn-ghost tag-toggle active" data-kind="obj">Objects</button>
            </div>` : '')
        }
      </div>`;

    row.querySelectorAll('.tag-toggle[data-kind]').forEach(btn => {
      btn.addEventListener('click', function() {
        this.classList.toggle('active');
        const wrap = row.querySelector(`#overlay-wrap-${i}`);
        if (wrap) wrap.classList.toggle('hide-' + this.dataset.kind + 's', !this.classList.contains('active'));
      });
    });

    // If there's a source image, render it with bbox overlays
    if (srcId && allDets.length) {
      const wrap = row.querySelector(`#overlay-wrap-${i}`);
      const img = document.createElement('img');
      img.style.cssText = 'max-width:100%;display:block;border-radius:4px';
      img.src = `/media/sources/${srcId}`;  // will be set properly below
      fetch(`/api/images/${srcId}/faces`)
        .then(r => r.json())
        .then(srcData => {
          img.src = srcData.source_image_url;
          img.onload = () => {
            const sx = img.clientWidth  / img.naturalWidth;
            const sy = img.clientHeight / img.naturalHeight;
            const sortedDets = [...allDets].sort((a, b) => (b.bbox.w * b.bbox.h) - (a.bbox.w * a.bbox.h));
            sortedDets.forEach(det => {
              const b = det.bbox;
              const box = document.createElement('div');
              box.className = 'det-box ' + (det.class_name ? 'object' : 'face');
              box.style.left   = (b.x / srcScale * sx) + 'px';
              box.style.top    = (b.y / srcScale * sy) + 'px';
              box.style.width  = (b.w / srcScale * sx) + 'px';
              box.style.height = (b.h / srcScale * sy) + 'px';
              const lbl = document.createElement('div');
              lbl.className = 'det-lbl';
              // Faces show match similarity; objects have no similarity, so show their score.
              const pct = det.class_name ? det.confidence : (det.similarity ?? det.confidence);
              const text = (det.label || det.class_name || 'Unknown') + ' ' + (pct * 100).toFixed(0) + '%';
              lbl.textContent = text;
              box.appendChild(lbl);
              wrap.appendChild(box);
            });
          };
          wrap.appendChild(img);
        })
        .catch(() => { wrap.appendChild(img); });
    }
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
})();
