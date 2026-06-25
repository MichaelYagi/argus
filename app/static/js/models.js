'use strict';
(() => {
  // Spinner beside the button — the underlying library doesn't expose byte progress,
  // so this shows activity without a misleading percentage.
  function showSpinner(btn) {
    const host = btn.parentElement;
    if (!host || host.querySelector('.dl-spinner')) return;
    const s = document.createElement('span');
    s.className = 'dl-spinner';
    btn.insertAdjacentElement('afterend', s);
  }
  function removeSpinner(btn) {
    btn.parentElement?.querySelector('.dl-spinner')?.remove();
  }

  document.querySelectorAll('[data-download]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.download;
      btn.disabled = true;
      btn.textContent = 'Starting…';
      const resp = await fetch('/api/models/' + id + '/download', { method: 'POST' });
      if (!resp.ok) {
        btn.textContent = 'Error'; btn.disabled = false;
        showToast('Could not start the download.', 'error');
        return;
      }
      showSpinner(btn);
      pollDownload(id, btn);
    });
  });

  document.querySelectorAll('[data-delete-model]').forEach(btn => {
    btn.addEventListener('click', () => {
      showConfirm(
        'Remove downloaded weights for this model? You can re-download them later.',
        async () => {
          const id = btn.dataset.deleteModel;
          btn.disabled = true;
          const resp = await fetch('/api/models/' + id, { method: 'DELETE' });
          if (resp.ok || resp.status === 204) { flashToast('Model weights removed.', 'success'); location.reload(); }
          else { btn.textContent = 'Error'; btn.disabled = false; showToast('Could not remove the model.', 'error'); }
        },
        { confirmText: 'Remove', danger: true }
      );
    });
  });

  document.querySelectorAll('[data-activate]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.activate;
      btn.disabled = true;
      btn.textContent = 'Loading…';
      const resp = await fetch('/api/models/' + id + '/activate', { method: 'PUT' });
      if (resp.ok) { flashToast('Model activated.', 'success'); location.reload(); }
      else {
        const err = await resp.json().catch(() => ({}));
        btn.textContent = 'Error: ' + (err.detail || resp.status);
        btn.disabled = false;
        showToast('Could not activate the model: ' + (err.detail || resp.status), 'error');
      }
    });
  });

  function pollDownload(id, btn) {
    const timer = setInterval(async () => {
      const resp = await fetch('/api/models/' + id + '/download/status');
      const data = await resp.json();
      if (data.status === 'complete') {
        clearInterval(timer);
        flashToast('Download complete.', 'success');
        location.reload();
      } else if (data.status === 'failed') {
        clearInterval(timer);
        removeSpinner(btn);
        btn.textContent = 'Failed: ' + (data.error || 'unknown');
        btn.disabled = false;
        showToast('Download failed: ' + (data.error || 'unknown'), 'error');
      } else {
        btn.textContent = 'Downloading…';
      }
    }, 2000);
  }

  // On load: resume polling for any models currently downloading
  document.querySelectorAll('[data-status-poll]').forEach(el => {
    const id = el.dataset.statusPoll;
    const btn = document.querySelector(`[data-download="${id}"]`);
    if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; showSpinner(btn); pollDownload(id, btn); }
  });
})();
