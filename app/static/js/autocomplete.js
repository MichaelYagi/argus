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
    .then(data => { faceLabels = data.map(i => i.label); })
    .catch(() => {});

  window.getFaceLabels = () => faceLabels;
  window.addFaceLabel  = label => { if (label && !faceLabels.includes(label)) faceLabels.push(label); };

  // ---------------------------------------------------------------------------
  // makeAutocomplete — attach a dropdown to an existing <input>
  // ---------------------------------------------------------------------------
  window.makeAutocomplete = input => {
    let drop = null;

    const hide = () => { drop?.remove(); drop = null; };

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
        li.addEventListener('mouseenter', () => { li.style.background = 'var(--hover)'; });
        li.addEventListener('mouseleave', () => { li.style.background = ''; });
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
    };

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      if (!q) return hide();
      show(faceLabels.filter(l => l.toLowerCase().includes(q)));
    });
    input.addEventListener('focus', () => {
      if (!input.value.trim()) show(faceLabels.slice(0, 12));
    });
    input.addEventListener('blur', () => setTimeout(hide, 200));
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') hide();
      if (e.key === 'Enter' && drop) {
        const first = drop.querySelector('li');
        if (first) { input.value = first.textContent; hide(); }
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
    msg.textContent = message;
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

  window.showLabelPopup = (anchor, onConfirm, placeholder = 'Name (blank to clear)') => {
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
      top: '50%', left: '50%',
      transform: 'translate(-50%, -50%)',
      background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius)', padding: '16px',
      zIndex: '500', boxShadow: '0 8px 32px rgba(0,0,0,.2)',
      width: '300px', maxWidth: '90vw',
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
    popup.appendChild(input);
    popup.appendChild(row);
    document.body.appendChild(popup);

    makeAutocomplete(input);
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
