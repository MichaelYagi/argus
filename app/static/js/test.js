'use strict';
(() => {
  const form      = document.getElementById('detect-form');
  const dropZone  = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const urlInput  = document.getElementById('url-input');
  const modeSelect = document.getElementById('detect-mode');
  const resultArea = document.getElementById('result-area');

  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('over');
    const f = e.dataTransfer.files[0];
    if (f) { fileInput.files = e.dataTransfer.files; showPreview(f); }
  });
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) showPreview(fileInput.files[0]); });

  function showPreview(file) {
    const reader = new FileReader();
    reader.onload = e => {
      document.getElementById('preview-img').src = e.target.result;
      document.getElementById('preview').hidden = false;
    };
    reader.readAsDataURL(file);
  }

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const mode = modeSelect.value;
    const fd = new FormData();
    let imageSrc = '';

    if (fileInput.files[0]) {
      fd.append('file', fileInput.files[0]);
      imageSrc = document.getElementById('preview-img').src;
    } else if (urlInput.value.trim()) {
      fd.append('image_url', urlInput.value.trim());
      imageSrc = urlInput.value.trim();
      // Show the image in the preview area so the user can see it
      document.getElementById('preview-img').src = imageSrc;
      document.getElementById('preview').hidden = false;
    } else {
      resultArea.innerHTML = '<div class="alert alert-error">Upload a file or enter an image URL first.</div>';
      return;
    }

    resultArea.innerHTML = '<p class="loading">Detecting…</p>';
    const resp = await fetch(`/api/detect/${mode}`, { method: 'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      resultArea.innerHTML = `<div class="alert alert-error">${err.detail || 'Detection failed'}</div>`;
      return;
    }

    // Prefer the server-saved copy so the overlay always works from same origin
    const data = await resp.json();
    if (data.source_image_id) {
      try {
        const srcResp = await fetch(`/api/images/${data.source_image_id}/faces`);
        if (srcResp.ok) {
          const srcData = await srcResp.json();
          imageSrc = srcData.image_url;
        }
      } catch (_) { /* keep original imageSrc */ }
    }

    renderResults(data, imageSrc);
  });

  function renderResults(data, imageSrc) {
    const faces   = data.faces   || [];
    const objects = data.objects || [];
    if (!faces.length && !objects.length) {
      resultArea.innerHTML = '<p class="muted">No detections.</p>';
      return;
    }

    const srcImg = { src: imageSrc };
    resultArea.innerHTML = '';

    // Overlay image
    const wrap = document.createElement('div');
    wrap.id = 'detect-result';
    const img = document.createElement('img');
    img.src = srcImg.src;
    wrap.appendChild(img);
    resultArea.appendChild(wrap);

    img.onload = () => {
      const sx = img.clientWidth  / img.naturalWidth;
      const sy = img.clientHeight / img.naturalHeight;
      [...faces, ...objects].forEach(det => {
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

    // Results list
    const list = document.createElement('div');
    list.style.marginTop = '16px';

    if (faces.length) {
      const h = document.createElement('h2');
      h.textContent = `Faces (${faces.length})`;
      h.style.marginBottom = '8px';
      list.appendChild(h);
      faces.forEach(f => {
        const card = document.createElement('div');
        card.className = 'review-card';
        card.innerHTML = `
          <img src="${f.crop_url}" style="width:80px;height:80px">
          <div class="rc-info">
            <strong>${f.label || 'Unknown'}</strong>
            <div class="muted">${(f.confidence * 100).toFixed(1)}% · ${f.review_status}</div>
            ${!f.label ? `<div class="rc-actions" style="margin-top:6px">
              <input type="text" placeholder="Label as…" style="width:130px" id="ln-${f.detection_id}">
              <button class="btn btn-primary" onclick="labelDetection(${f.detection_id}, 'ln-${f.detection_id}', this)">Label</button>
            </div>` : ''}
          </div>`;
        list.appendChild(card);
      });
    }

    if (objects.length) {
      const h = document.createElement('h2');
      h.textContent = `Objects (${objects.length})`;
      h.style.margin = '16px 0 8px';
      list.appendChild(h);
      objects.forEach(o => {
        const p = document.createElement('p');
        p.className = 'muted';
        p.style.marginBottom = '4px';
        p.textContent = `${o.class_name} — ${(o.confidence * 100).toFixed(1)}%`;
        list.appendChild(p);
      });
    }

    resultArea.appendChild(list);
    if (window.makeAutocomplete) {
      list.querySelectorAll('input[type=text]').forEach(inp => makeAutocomplete(inp));
    }
  }

  window.labelDetection = async (detId, inputId, btn) => {
    const label = document.getElementById(inputId).value.trim();
    if (!label) return;
    btn.disabled = true;
    const resp = await fetch('/api/review/' + detId + '/reassign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (resp.ok) { btn.textContent = 'Saved'; }
    else { btn.disabled = false; btn.textContent = 'Error'; }
  };
})();
