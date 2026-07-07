'use strict';
/**
 * Shared face-label autocomplete utilities.
 * Included in base.html so all pages get it automatically.
 *
 * Exposes:
 *   window.makeAutocomplete(input)   — wire an <input> to show a dropdown
 *   window.getFaceLabels()           — current cached label list
 *   window.addFaceLabel(label)       — add a newly created label to the cache
 *   window.showLabelPopup(anchor, onConfirm)
 *                                    — floating inline input with autocomplete,
 *                                      positioned below `anchor` (any element)
 */
(() => {
  let faceLabels = [];

  fetch('/api/identities?type=face')
    .then(r => r.json())
    .then(data => { faceLabels = (data.items || data).map(i => i.label); })
    .catch(() => {});

  window.getFaceLabels = () => faceLabels;
  window.addFaceLabel  = label => { if (label && !faceLabels.includes(label)) faceLabels.push(label); };

  // ---------------------------------------------------------------------------
  // makeAutocomplete — attach a dropdown to an existing <input>
  // ---------------------------------------------------------------------------
  window.makeAutocomplete = input => {
    let drop = null;
    let activeIdx = -1;

    const hide = () => { drop?.remove(); drop = null; activeIdx = -1; };

    const setActive = idx => {
      if (!drop) return;
      const items = drop.querySelectorAll('li');
      items.forEach((li, i) => { li.style.background = i === idx ? 'var(--hover)' : ''; });
      activeIdx = idx;
    };

    const show = items => {
      hide();
      if (!items.length) return;

      drop = document.createElement('ul');
      Object.assign(drop.style, {
        position: 'absolute', listStyle: 'none', margin: '0', padding: '0',
        background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)', zIndex: '400',
        maxHeight: '180px', overflowY: 'auto',
        boxShadow: '0 4px 12px rgba(0,0,0,.15)',
        minWidth: Math.max(input.offsetWidth, 160) + 'px',
        top: (input.offsetTop + input.offsetHeight + 2) + 'px',
        left: input.offsetLeft + 'px',
      });

      items.forEach(label => {
        const li = document.createElement('li');
        li.textContent = label;
        Object.assign(li.style, { padding: '7px 12px', cursor: 'pointer', fontSize: '13px' });
        li.addEventListener('mouseenter', () => {
          const idx = Array.from(drop.querySelectorAll('li')).indexOf(li);
          setActive(idx);
        });
        li.addEventListener('mouseleave', () => setActive(-1));
        li.addEventListener('mousedown', e => {
          e.preventDefault();
          input.value = label;
          hide();
          input.dispatchEvent(new Event('autocomplete-select'));
        });
        drop.appendChild(li);
      });

      const parent = input.closest('.ra-wrap') || input.closest('[style*="position:relative"]') || input.parentElement;
      parent.style.position = 'relative';
      parent.appendChild(drop);

      // Flip the dropdown above the input when there isn't room below — e.g. an
      // input in a sticky bottom action bar (the Suggested page).
      const rect = input.getBoundingClientRect();
      if (rect.bottom + 200 > window.innerHeight && rect.top > 200) {
        drop.style.top = 'auto';
        drop.style.bottom = (parent.offsetHeight - input.offsetTop + 2) + 'px';
      }
    };

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      if (!q) return hide();
      const matches = faceLabels.filter(l => l.toLowerCase().includes(q));
      matches.sort((a, b) => {
        const ai = a.toLowerCase().indexOf(q), bi = b.toLowerCase().indexOf(q);
        return ai !== bi ? ai - bi : a.localeCompare(b);
      });
      show(matches);
    });
    input.addEventListener('focus', () => {
      if (!input.value.trim()) show(faceLabels.slice(0, 12));
    });
    input.addEventListener('blur', () => setTimeout(hide, 200));
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { hide(); return; }
      if (e.key === 'ArrowDown' && drop) {
        e.preventDefault();
        const count = drop.querySelectorAll('li').length;
        setActive(Math.min(activeIdx + 1, count - 1));
        return;
      }
      if (e.key === 'ArrowUp' && drop) {
        e.preventDefault();
        setActive(Math.max(activeIdx - 1, -1));
        return;
      }
      if (e.key === 'Enter' && drop) {
        if (activeIdx >= 0) {
          const li = drop.querySelectorAll('li')[activeIdx];
          if (li) { input.value = li.textContent; }
        }
        hide();
      }
    });
  };

  // ---------------------------------------------------------------------------
  // showLabelPopup — floating popup with an autocomplete input
  // Positioned via fixed coords relative to `anchor` element.
  // onConfirm(label) called with the typed/selected value (blank = reject/clear).
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // showConfirm — custom confirm modal (replaces browser confirm())
  // ---------------------------------------------------------------------------
  window.showConfirm = (message, onConfirm, { confirmText = 'Confirm', danger = false } = {}) => {
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    Object.assign(overlay.style, {
      position: 'fixed', inset: '0',
      background: 'rgba(0,0,0,.45)',
      zIndex: '600',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    });

    const box = document.createElement('div');
    Object.assign(box.style, {
      background: 'var(--surface)', color: 'var(--text)', borderRadius: 'var(--radius)',
      padding: '24px', maxWidth: '400px', width: '90vw',
      boxShadow: '0 8px 32px rgba(0,0,0,.2)',
    });

    const msg = document.createElement('p');
    if (message instanceof Node) { msg.appendChild(message); } else { msg.textContent = message; }
    msg.style.cssText = 'margin-bottom:20px;font-size:14px;line-height:1.5';

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:flex-end;gap:8px';

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.className = 'btn btn-ghost';

    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = confirmText;
    confirmBtn.className = danger ? 'btn btn-danger' : 'btn btn-primary';

    row.appendChild(cancelBtn);
    row.appendChild(confirmBtn);
    box.appendChild(msg);
    box.appendChild(row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    confirmBtn.focus();

    const close = () => overlay.remove();
    cancelBtn.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    confirmBtn.addEventListener('click', () => { close(); onConfirm(); });

    const onKey = e => {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); }
      if (e.key === 'Enter')  { close(); onConfirm(); document.removeEventListener('keydown', onKey); }
    };
    document.addEventListener('keydown', onKey);
  };

  // ---------------------------------------------------------------------------
  // showMessage — single-button informational modal (no cancel)
  // ---------------------------------------------------------------------------
  window.showMessage = (message, { buttonText = 'OK', onClose = null } = {}) => {
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    Object.assign(overlay.style, {
      position: 'fixed', inset: '0',
      background: 'rgba(0,0,0,.45)',
      zIndex: '600',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    });

    const box = document.createElement('div');
    Object.assign(box.style, {
      background: 'var(--surface)', color: 'var(--text)', borderRadius: 'var(--radius)',
      padding: '24px', maxWidth: '400px', width: '90vw',
      boxShadow: '0 8px 32px rgba(0,0,0,.2)',
    });

    const msg = document.createElement('p');
    msg.textContent = message;
    msg.style.cssText = 'margin-bottom:20px;font-size:14px;line-height:1.5';

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:flex-end';

    const okBtn = document.createElement('button');
    okBtn.textContent = buttonText;
    okBtn.className = 'btn btn-primary';

    row.appendChild(okBtn);
    box.appendChild(msg);
    box.appendChild(row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    okBtn.focus();

    const close = () => { overlay.remove(); onClose?.(); };
    okBtn.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Escape' || e.key === 'Enter') {
        close();
        document.removeEventListener('keydown', onKey);
      }
    });
  };

  // ---------------------------------------------------------------------------
  // showToast — transient corner notification (info / success / error)
  // ---------------------------------------------------------------------------
  window.showToast = (message, level = 'info', { timeout = 5000 } = {}) => {
    let stack = document.getElementById('toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toast-stack';
      stack.style.cssText = 'position:fixed;top:16px;right:16px;z-index:900;' +
        'display:flex;flex-direction:column;gap:8px;max-width:min(360px,90vw)';
      document.body.appendChild(stack);
    }
    const colors = {
      info:    { bar: 'var(--accent)',  },
      success: { bar: 'var(--success)', },
      error:   { bar: '#e53e3e',        },
    };
    const c = colors[level] || colors.info;
    const toast = document.createElement('div');
    toast.style.cssText = 'background:var(--surface);color:var(--text);border-radius:8px;' +
      'box-shadow:0 4px 20px rgba(0,0,0,.3);border-left:3px solid ' + c.bar + ';' +
      'padding:10px 12px;font-size:13px;line-height:1.4;display:flex;align-items:flex-start;gap:10px';
    const text = document.createElement('div');
    text.style.cssText = 'flex:1;min-width:0;word-break:break-word';
    text.textContent = message;
    const close = document.createElement('button');
    close.textContent = '×';
    close.setAttribute('aria-label', 'Dismiss');
    close.style.cssText = 'background:none;border:none;color:var(--muted);cursor:pointer;' +
      'font-size:16px;line-height:1;padding:0';
    const remove = () => { toast.remove(); if (!stack.children.length) stack.remove(); };
    close.addEventListener('click', remove);
    toast.appendChild(text);
    toast.appendChild(close);
    stack.appendChild(toast);
    if (timeout) setTimeout(remove, timeout);
    return remove;
  };

  // ---------------------------------------------------------------------------
  // capturePhoto(onCapture) — get a photo from the device camera.
  // Live in-browser webcam where the page is a secure context (HTTPS / localhost);
  // otherwise the phone's native camera via a capture file input (works over LAN HTTP).
  // Calls onCapture(File) with a JPEG/photo File. No backend involvement.
  // ---------------------------------------------------------------------------
  window.capturePhoto = function (onCapture) {
    const liveOk = window.isSecureContext && navigator.mediaDevices
                   && typeof navigator.mediaDevices.getUserMedia === 'function';

    if (!liveOk) {
      // Native camera: hand off to the OS camera app via the file input.
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.setAttribute('capture', 'environment');
      inp.style.display = 'none';
      inp.addEventListener('change', () => {
        if (inp.files && inp.files[0]) onCapture(inp.files[0]);
        inp.remove();
      });
      document.body.appendChild(inp);
      inp.click();
      return;
    }

    // Live webcam modal (not given the modal-overlay class, so showToast/showMessage
    // don't purge it).
    let stream = null;
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:700;' +
      'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:16px';
    const video = document.createElement('video');
    video.autoplay = true; video.playsInline = true; video.muted = true;
    video.style.cssText = 'max-width:100%;max-height:70vh;border-radius:8px;background:#000';
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:10px';
    const snap = document.createElement('button');
    snap.className = 'btn btn-primary'; snap.textContent = 'Capture';
    const cancel = document.createElement('button');
    cancel.className = 'btn btn-ghost'; cancel.textContent = 'Cancel';
    row.appendChild(snap); row.appendChild(cancel);
    overlay.appendChild(video); overlay.appendChild(row);
    document.body.appendChild(overlay);

    const stop = () => {
      if (stream) stream.getTracks().forEach(t => t.stop());
      overlay.remove();
    };
    cancel.addEventListener('click', stop);
    overlay.addEventListener('keydown', e => { if (e.key === 'Escape') stop(); });

    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then(s => { stream = s; video.srcObject = s; })
      .catch(err => { stop(); if (window.showToast) showToast('Camera unavailable: ' + err.message, 'error'); });

    snap.addEventListener('click', () => {
      if (!video.videoWidth) return;
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      canvas.toBlob(blob => {
        if (blob) onCapture(new File([blob], 'capture.jpg', { type: 'image/jpeg' }));
        stop();
      }, 'image/jpeg', 0.92);
    });
  };

  // Hide [data-camera-btn] buttons where the camera can't actually be used: no live
  // webcam (insecure context) AND no touch device. Otherwise the button would just open
  // a file dialog on desktop, which is pointless and confusing.
  document.addEventListener('DOMContentLoaded', () => {
    const liveOk = window.isSecureContext && navigator.mediaDevices
                   && typeof navigator.mediaDevices.getUserMedia === 'function';
    const hasTouch = ('ontouchstart' in window) || navigator.maxTouchPoints > 0;
    if (liveOk || hasTouch) return;
    document.querySelectorAll('[data-camera-btn]').forEach(b => { b.style.display = 'none'; });
  });

  window.showLabelPopup = (anchor, onConfirm, placeholder = 'Name (blank to clear)', onUnidentify = null, prefill = '') => {
    document.querySelectorAll('.label-popup, .label-backdrop').forEach(p => p.remove());

    // Backdrop — clicking it dismisses the popup
    const backdrop = document.createElement('div');
    backdrop.className = 'label-backdrop';
    Object.assign(backdrop.style, {
      position: 'fixed', inset: '0', zIndex: '499',
    });
    document.body.appendChild(backdrop);

    const popup = document.createElement('div');
    popup.className = 'label-popup';
    Object.assign(popup.style, {
      position: 'fixed',
      background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius)', padding: '16px',
      zIndex: '500', boxShadow: '0 8px 32px rgba(0,0,0,.2)',
      width: '300px', maxWidth: '90vw',
      visibility: 'hidden',  // positioned after append (needs measured size)
    });

    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = placeholder;
    input.autocomplete = 'off';
    Object.assign(input.style, {
      display: 'block', width: '100%', padding: '7px 10px',
      border: '1px solid var(--border)', borderRadius: 'var(--radius)',
      fontSize: '13px', marginBottom: '8px', boxSizing: 'border-box',
    });

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:6px';

    const applyBtn = document.createElement('button');
    applyBtn.textContent = 'Apply';
    applyBtn.className = 'btn btn-primary';
    Object.assign(applyBtn.style, { padding: '4px 12px', fontSize: '12px' });

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.className = 'btn btn-ghost';
    Object.assign(cancelBtn.style, { padding: '4px 12px', fontSize: '12px' });

    row.appendChild(applyBtn);
    row.appendChild(cancelBtn);
    if (onUnidentify) {
      const spacer = document.createElement('span');
      spacer.style.marginLeft = 'auto';
      row.appendChild(spacer);
      const unBtn = document.createElement('button');
      unBtn.textContent = 'Unidentify';
      unBtn.className = 'btn btn-ghost';
      Object.assign(unBtn.style, { padding: '4px 10px', fontSize: '12px', color: 'var(--danger)' });
      unBtn.addEventListener('click', () => { close(); onUnidentify(); });
      row.appendChild(unBtn);
    }
    // Wrap the input in a relative container so makeAutocomplete anchors its
    // dropdown to THIS wrapper (input.closest('.ra-wrap')) rather than setting
    // the popup itself to position:relative — which would clobber the popup's
    // position:fixed and drop it to the bottom of the page.
    const inputWrap = document.createElement('div');
    inputWrap.className = 'ra-wrap';
    inputWrap.style.position = 'relative';
    inputWrap.appendChild(input);
    popup.appendChild(inputWrap);
    popup.appendChild(row);
    document.body.appendChild(popup);

    // Position beside the anchor (the clicked box/button), clamped to the viewport,
    // so it appears next to what you clicked rather than pinned to the screen centre.
    // Falls back to centred when no usable anchor is provided.
    {
      const pad = 8;
      const pr = popup.getBoundingClientRect();
      const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
      let left, top;
      if (r && (r.width || r.height)) {
        left = r.right + pad;                                    // prefer the box's right
        if (left + pr.width > window.innerWidth - pad)
          left = r.left - pr.width - pad;                        // else its left
        left = Math.max(pad, Math.min(left, window.innerWidth  - pr.width  - pad));
        top  = Math.max(pad, Math.min(r.top, window.innerHeight - pr.height - pad));
      } else {
        left = (window.innerWidth  - pr.width)  / 2;
        top  = (window.innerHeight - pr.height) / 2;
      }
      popup.style.left = left + 'px';
      popup.style.top  = top + 'px';
      popup.style.visibility = 'visible';
    }

    makeAutocomplete(input);
    if (prefill) { input.value = prefill; input.select(); }
    input.focus();

    const close = () => { popup.remove(); backdrop.remove(); };

    backdrop.addEventListener('click', close);
    applyBtn.addEventListener('click', () => { close(); onConfirm(input.value.trim()); });
    cancelBtn.addEventListener('click', close);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { close(); onConfirm(input.value.trim()); }
      if (e.key === 'Escape') close();
    });
  };
})();

window.openSourceModal = function openSourceModal(url) {
  if (!url) return;
  document.querySelectorAll('.src-modal').forEach(m => m.remove());
  const overlay = document.createElement('div');
  overlay.className = 'src-modal';
  const img = document.createElement('img');
  img.src = url;
  img.alt = '';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'src-modal-close';
  closeBtn.textContent = '×';
  overlay.appendChild(img);
  overlay.appendChild(closeBtn);
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  const onKey = e => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
};

// Queue a toast to show after the next page load (for actions that reload, e.g. the
// Models page). Read once on load below.
window.flashToast = (message, level = 'info') => {
  try { sessionStorage.setItem('argus-flash', JSON.stringify({ message, level })); } catch (e) {}
};
document.addEventListener('DOMContentLoaded', () => {
  let f;
  try { f = sessionStorage.getItem('argus-flash'); } catch (e) { return; }
  if (!f) return;
  try { sessionStorage.removeItem('argus-flash'); } catch (e) {}
  try {
    const { message, level } = JSON.parse(f);
    if (message && window.showToast) window.showToast(message, level);
  } catch (e) {}
});
