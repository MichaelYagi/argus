'use strict';
(() => {
  const alertEl = document.getElementById('settings-alert');

  function showAlert(msg, type) {
    alertEl.className = 'alert alert-' + type;
    alertEl.textContent = msg;
    alertEl.hidden = false;
    clearTimeout(alertEl._t);
    alertEl._t = setTimeout(() => { alertEl.hidden = true; }, 3000);
  }

  async function saveSetting(key, value) {
    const resp = await fetch('/api/settings/' + encodeURIComponent(key), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return err.detail || 'Failed to save';
    }
    return null;
  }

  // Update slider display value live while dragging
  document.querySelectorAll('input[type=range][data-key]').forEach(slider => {
    const valEl = document.getElementById('v-' + slider.dataset.key.replace(/\./g, '-'));
    if (valEl) slider.addEventListener('input', () => { valEl.textContent = slider.value; });
  });

  // Dependent rows — checkbox with data-controls disables the target row when unchecked
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
    sync();
  });

  // Save on form submit
  document.querySelectorAll('form.settings-form').forEach(form => {
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const btn = form.querySelector('button[type=submit]');
      btn.disabled = true;
      btn.textContent = 'Saving…';

      const errors = [];

      for (const el of form.querySelectorAll('[data-key]')) {
        let value;
        if (el.type === 'checkbox') value = el.checked ? 'true' : 'false';
        else value = el.value;
        const err = await saveSetting(el.dataset.key, value);
        if (err) errors.push(`${el.dataset.key}: ${err}`);
      }

      // COCO classes
      const allCoco = form.querySelectorAll('.coco-cb');
      if (allCoco.length) {
        const checked = [...allCoco].filter(c => c.checked).map(c => c.value);
        const value = checked.length === allCoco.length ? '*' : checked.join(',');
        const err = await saveSetting('object.classes_enabled', value);
        if (err) errors.push(err);
      }

      btn.disabled = false;
      btn.textContent = 'Save';

      if (errors.length) {
        showAlert(errors.join('; '), 'error');
      } else {
        showAlert('Settings saved', 'success');
      }
    });
  });

  // COCO all-classes toggle (UI only — saved on form submit)
  const allCocoToggle = document.getElementById('coco-all');
  if (allCocoToggle) {
    const allCoco = document.querySelectorAll('.coco-cb');
    allCocoToggle.addEventListener('change', () => {
      allCoco.forEach(c => { c.checked = allCocoToggle.checked; });
    });
    document.querySelectorAll('.coco-cb').forEach(cb => {
      cb.addEventListener('change', () => {
        const checked = [...document.querySelectorAll('.coco-cb')].filter(c => c.checked);
        allCocoToggle.checked = checked.length === allCoco.length;
      });
    });
  }
})();
