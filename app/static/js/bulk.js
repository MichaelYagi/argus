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
  const modeSelect = document.getElementById('detect-mode');
  const runBtn     = document.getElementById('run-btn');
  const queueCount = document.getElementById('queue-count');
  const resultsEl  = document.getElementById('results');
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

  function addFiles(newFiles) {
    fileList = [...fileList, ...newFiles];
    updateQueue();
  }

  urlInput.addEventListener('input', updateQueue);

  function getUrls() {
    return urlInput.value.split('\n').map(s => s.trim()).filter(Boolean);
  }

  function updateQueue() {
    const total = fileList.length + getUrls().length;
    runBtn.disabled = total === 0;
    queueCount.textContent = total ? `${total} image${total === 1 ? '' : 's'} queued` : '';
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
    const jobs = [
      ...fileList.map((f, idx) => ({ i: idx,                  type: 'file', payload: f,       label: f.name })),
      ...urls.map((u, idx)     => ({ i: fileList.length + idx, type: 'url',  payload: u,       label: u      })),
    ];

    await _pool(jobs, 3, async job => {
      setStatus(job.i, job.type === 'file' ? 'Processing…' : 'Fetching…', 'muted');
      const fd = new FormData();
      if (job.type === 'file') fd.append('file', job.payload);
      else fd.append('image_url', job.payload);
      const resp = await fetch(`/api/detect/${mode}`, { method: 'POST', body: fd });
      const data = await resp.json();
      done++;
      updateProgress();
      renderResult(rows[job.i], job.i, job.label, resp.ok, data, mode);
    });

    progressLbl.textContent = `Done — ${total} image${total === 1 ? '' : 's'} processed`;
    runBtn.disabled = false;
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

    const faces   = data.faces   || [];
    const objects = data.objects || [];
    const srcId   = data.source_image_id;
    const allDets = [...faces, ...objects];

    const summary = [];
    if (mode !== 'objects' && faces.length)   summary.push(`${faces.length} face${faces.length === 1 ? '' : 's'}`);
    if (mode !== 'faces'   && objects.length) summary.push(`${objects.length} object${objects.length === 1 ? '' : 's'}`);
    const desc = summary.length ? summary.join(', ') + ' detected' : 'Nothing detected';

    const tagLink = srcId ? `<a href="/tag/${srcId}" style="font-size:12px">Tag</a>` : '';
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
        ${thumbs ? `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">${thumbs}</div>` : ''}
        ${allDets.length ? `<div id="overlay-wrap-${i}" style="position:relative;display:inline-block;max-width:100%"></div>` : ''}
      </div>`;

    // If there's a source image, render it with bbox overlays
    if (srcId && allDets.length) {
      const wrap = row.querySelector(`#overlay-wrap-${i}`);
      const img = document.createElement('img');
      img.style.cssText = 'max-width:100%;display:block;border-radius:4px';
      img.src = `/media/sources/${srcId}`;  // will be set properly below
      fetch(`/api/images/${srcId}/faces`)
        .then(r => r.json())
        .then(srcData => {
          img.src = srcData.image_url;
          img.onload = () => {
            const sx = img.clientWidth  / img.naturalWidth;
            const sy = img.clientHeight / img.naturalHeight;
            allDets.forEach(det => {
              const b = det.bbox;
              const box = document.createElement('div');
              box.className = 'det-box ' + (det.class_name ? 'object' : 'face');
              box.style.left   = (b.x * sx) + 'px';
              box.style.top    = (b.y * sy) + 'px';
              box.style.width  = (b.w * sx) + 'px';
              box.style.height = (b.h * sy) + 'px';
              const lbl = document.createElement('div');
              lbl.className = 'det-lbl';
              lbl.textContent = (det.label || det.class_name || 'Unknown') + ' ' + (det.confidence * 100).toFixed(0) + '%';
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
