'use strict';
(() => {
  const alertEl = document.getElementById('settings-alert');

  function showAlert(msg, type) {
    alertEl.className = 'alert alert-' + type;
    alertEl.textContent = msg;
    alertEl.hidden = false;
    clearTimeout(alertEl._t);
    alertEl._t = setTimeout(() => { alertEl.hidden = true; }, 2500);
  }

  async function saveSetting(key, value) {
    const resp = await fetch('/api/settings/' + encodeURIComponent(key), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    });
    if (resp.ok) {
      showAlert('Saved', 'success');
    } else {
      const err = await resp.json().catch(() => ({}));
      showAlert(err.detail || 'Failed to save', 'error');
      return false;
    }
    return true;
  }

  // Sliders
  document.querySelectorAll('input[type=range][data-key]').forEach(slider => {
    const valEl = document.getElementById('v-' + slider.dataset.key.replace(/\./g, '-'));
    if (valEl) slider.addEventListener('input', () => { valEl.textContent = slider.value; });
    slider.addEventListener('change', () => saveSetting(slider.dataset.key, slider.value));
  });

  // Number inputs
  document.querySelectorAll('input[type=number][data-key]').forEach(inp => {
    inp.addEventListener('change', () => saveSetting(inp.dataset.key, inp.value));
  });

  // Toggles
  document.querySelectorAll('input[type=checkbox][data-key]').forEach(cb => {
    cb.addEventListener('change', async () => {
      const ok = await saveSetting(cb.dataset.key, cb.checked ? 'true' : 'false');
      if (!ok) cb.checked = !cb.checked; // revert on error
    });
  });

  // Dependent rows — a checkbox with data-controls disables the target row when unchecked
  document.querySelectorAll('input[type=checkbox][data-controls]').forEach(cb => {
    const rowId = 'srow-' + cb.dataset.controls.replace(/\./g, '-');
    function sync() {
      const row = document.getElementById(rowId);
      if (!row) return;
      row.style.opacity = cb.checked ? '1' : '0.45';
      row.querySelectorAll('input[type=range], input[type=number], input[type=text]').forEach(el => {
        el.disabled = !cb.checked;
      });
    }
    cb.addEventListener('change', sync);
    sync(); // apply on page load
  });

  // COCO class checkboxes
  const allCoco = document.querySelectorAll('.coco-cb');
  const allCocoToggle = document.getElementById('coco-all');

  if (allCocoToggle) {
    allCocoToggle.addEventListener('change', () => {
      allCoco.forEach(c => { c.checked = allCocoToggle.checked; });
      saveSetting('object.classes_enabled', allCocoToggle.checked ? '*' : '');
    });
  }

  allCoco.forEach(cb => {
    cb.addEventListener('change', () => {
      const checked = [...allCoco].filter(c => c.checked).map(c => c.value);
      const value = checked.length === allCoco.length ? '*' : checked.join(',');
      if (allCocoToggle) allCocoToggle.checked = (value === '*');
      saveSetting('object.classes_enabled', value);
    });
  });
})();
