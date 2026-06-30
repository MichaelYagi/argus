'use strict';
(() => {
  const container = document.getElementById('gallery');
  if (!container) return;

  const identityId   = container.dataset.identityId;
  const identityType = container.dataset.identityType;
  let   coverId      = container.dataset.coverId || null;
  const PAGE = 30;

  function adjustRefCount(delta) {
    const el = document.getElementById('ref-count-label');
    if (!el) return;
    const cur = parseInt(el.textContent, 10) || 0;
    const n = Math.max(0, cur + delta);
    el.textContent = `${n} reference${n !== 1 ? 's' : ''}`;
  }
  function adjustDetCount(delta) {
    const el = document.getElementById('det-count-label');
    if (!el) return;
    const cur = parseInt(el.textContent, 10) || 0;
    const n = Math.max(0, cur + delta);
    el.textContent = `${n} detection${n !== 1 ? 's' : ''}`;
  }
  // Drop one or more items from the local model and update both header counts
  // (a removed detection also removes its reference if it was enrolled).
  function removeItems(ids) {
    ids.forEach(id => {
      const idx = allItems.findIndex(i => i.detection_id === id);
      if (idx === -1) return;
      const wasEnrolled = !!allItems[idx].enrolled;
      allItems.splice(idx, 1);
      adjustDetCount(-1);
      if (wasEnrolled) adjustRefCount(-1);
    });
  }
  const GAP = 4;
  const TARGET_H = 200;
  let cursor = null, hasMore = true, loading = false;
  const allItems = [];
  const selected = new Set(); // detection_ids

  const loadingEl = document.getElementById('gallery-loading');
  const emptyEl   = document.getElementById('gallery-empty');
  const bulkBar   = document.getElementById('gallery-bulk-bar');
  const bulkCount = document.getElementById('gallery-bulk-count');

  function updateBulkBar() {
    if (bulkBar) bulkBar.style.display = selected.size === 0 ? 'none' : 'flex';
    if (bulkCount) bulkCount.textContent = selected.size;
    // When anything is selected, show all checkboxes; clear on deselect-all
    container.classList.toggle('has-selection', selected.size > 0);
  }

  // ---------------------------------------------------------------------------
  // Bulk actions (called from template buttons)
  // ---------------------------------------------------------------------------
  window.bulkDelete = () => {
    showConfirm(
      `Remove ${selected.size} detection${selected.size === 1 ? '' : 's'} from this identity?`,
      async () => {
        const items = [...selected].map(id => ({ detection_id: id, action: 'reject' }));
        const n = selected.size;
        let ok = false;
        try {
          const resp = await fetch('/api/review/bulk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(items),
          });
          ok = resp.ok;
        } catch (e) { ok = false; }
        if (!ok) {
          if (window.showToast) showToast('Could not remove the selected detections.', 'error');
          return;
        }
        removeItems([...selected]);
        selected.clear();
        updateBulkBar();
        render();
        if (window.showToast) showToast(n + ' detection' + (n === 1 ? '' : 's') + ' removed', 'success');
      },
      { confirmText: 'Delete', danger: true }
    );
  };

  window.bulkChangeIdentity = () => {
    const btn = document.getElementById('bulk-change-btn');
    showLabelPopup(btn, async label => {
      if (!label) return;
      const items = [...selected].map(id => ({ detection_id: id, action: 'reassign', label }));
      const n = selected.size;
      let ok = false;
      try {
        const resp = await fetch('/api/review/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(items),
        });
        ok = resp.ok;
      } catch (e) { ok = false; }
      if (!ok) {
        if (window.showToast) showToast('Could not reassign the selected detections.', 'error');
        return;
      }
      addFaceLabel(label);
      removeItems([...selected]);
      selected.clear();
      updateBulkBar();
      render();
      if (window.showToast) showToast(n + ' moved to ' + label, 'success');
    });
  };

  window.clearSelection = () => {
    selected.clear();
    updateBulkBar();
    render();
  };

  // ---------------------------------------------------------------------------
  // Load
  // ---------------------------------------------------------------------------
  async function loadPage() {
    if (loading || !hasMore) return;
    loading = true;
    if (loadingEl) loadingEl.hidden = false;

    const params = new URLSearchParams({ limit: PAGE });
    if (cursor) params.set('cursor', cursor);
    const resp = await fetch(`/api/identities/${identityId}/gallery?${params}`);
    if (!resp.ok) { loading = false; return; }
    const data = await resp.json();

    hasMore = data.has_more;
    cursor = data.next_cursor;

    if (!data.items.length && !allItems.length) {
      if (emptyEl) emptyEl.hidden = false;
      loading = false;
      return;
    }

    const loaded = await Promise.all(data.items.map(item => new Promise(resolve => {
      const img = new Image();
      img.onload = () => resolve({ ...item, nw: img.naturalWidth, nh: img.naturalHeight });
      img.onerror = () => resolve({ ...item, nw: 1, nh: 1 });
      img.src = item.crop_url;
    })));

    allItems.push(...loaded);
    render();
    loading = false;
    if (loadingEl) loadingEl.hidden = true;
    if (!hasMore) sentinel.remove();
    else if (sentinelVisible) loadPage();
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  function render() {
    container.innerHTML = '';
    const W = container.clientWidth;
    packRows(allItems, W).forEach(({ items, height }) => {
      const row = document.createElement('div');
      row.className = 'g-row';
      items.forEach(item => {
        const ar = item.nw / (item.nh || 1);
        const w = Math.floor(ar * height);
        const isSelected = selected.has(item.detection_id);

        const el = document.createElement('div');
        el.className = 'g-item' + (isSelected ? ' selected' : '');
        el.style.width  = w + 'px';
        el.style.height = Math.floor(height) + 'px';

        const img = document.createElement('img');
        img.src = item.crop_url;
        img.loading = 'lazy';
        img.alt = '';
        if (item.source_image_url) {
          img.style.cursor = 'zoom-in';
          img.addEventListener('click', e => {
            e.stopPropagation();
            if (selected.size > 0) {
              toggleItem();
            } else {
              openSourceModal(item.source_image_url);
            }
          });
        }
        el.appendChild(img);

        // Match-similarity badge (bottom-left, hover only): how strongly this crop
        // matches the person's reference set. Null when there are no references yet.
        if (item.similarity != null) {
          const badge = document.createElement('div');
          badge.className = 'g-badge';
          badge.textContent = (item.similarity * 100).toFixed(0) + '%';
          badge.title = "Similarity to this person's reference set";
          el.appendChild(badge);
        } else if (identityType === 'object' && item.confidence != null) {
          const badge = document.createElement('div');
          badge.className = 'g-badge';
          badge.textContent = (item.confidence * 100).toFixed(0) + '%';
          badge.title = 'Detection confidence';
          el.appendChild(badge);
        }

        // Tag link (top-right, hover only)
        const tagLink = document.createElement('a');
        tagLink.className = 'g-tag-link';
        tagLink.href = '/tag/' + item.source_image_id;
        tagLink.textContent = 'Tag';
        tagLink.addEventListener('click', e => e.stopPropagation());
        el.appendChild(tagLink);

        // Delete button (bottom-left alongside badge, hover only)
        const delBtn = document.createElement('button');
        delBtn.title = 'Delete this detection';
        delBtn.className = 'g-del-btn';
        delBtn.textContent = '✕';
        delBtn.addEventListener('click', async e => {
          e.stopPropagation();
          showConfirm('Delete this detection permanently?', async () => {
            let resp;
            try {
              resp = await fetch(`/api/detections/${item.detection_id}`, { method: 'DELETE' });
            } catch (err) {
              if (window.showToast) showToast('Could not delete the detection (network error).', 'error');
              return;
            }
            if (resp.ok || resp.status === 204) {
              removeItems([item.detection_id]);
              selected.delete(item.detection_id);
              updateBulkBar();
              render();
              if (window.showToast) showToast('Detection deleted', 'success');
            } else if (window.showToast) {
              showToast('Could not delete the detection.', 'error');
            }
          }, { confirmText: 'Delete', danger: true });
        });
        el.appendChild(delBtn);

        // Reference toggle (bottom-right, face identities only).
        // Shows "+" to add, "✓" (persistent, blue) when the crop is a reference.
        if (identityType === 'face') {
          const enrollBtn = document.createElement('button');
          enrollBtn.className = 'g-enroll-btn';
          let enrolled = !!item.enrolled;
          const renderEnroll = () => {
            enrollBtn.textContent = enrolled ? '✓' : '+';
            enrollBtn.classList.toggle('enrolled', enrolled);
            enrollBtn.title = enrolled
              ? 'In reference set — click to remove'
              : 'Add to reference set';
          };
          renderEnroll();
          enrollBtn.addEventListener('click', async e => {
            e.stopPropagation();
            enrollBtn.disabled = true;
            try {
              if (!enrolled) {
                const resp = await fetch(`/api/detections/${item.detection_id}/enroll`, { method: 'POST' });
                if (resp.ok) {
                  const d = await resp.json();
                  if (d.added) adjustRefCount(1);
                  enrolled = true;
                } else if (window.showToast) {
                  showToast('Could not add to reference set.', 'error');
                }
              } else {
                const resp = await fetch(`/api/detections/${item.detection_id}/enroll`, { method: 'DELETE' });
                if (resp.ok) {
                  const d = await resp.json();
                  if (d.removed) adjustRefCount(-1);
                  enrolled = false;
                } else if (window.showToast) {
                  showToast('Could not remove from reference set.', 'error');
                }
              }
              item.enrolled = enrolled;
              renderEnroll();
            } catch (err) {
              if (window.showToast) showToast('Reference update failed (network error).', 'error');
            } finally {
              enrollBtn.disabled = false;
            }
          });
          el.appendChild(enrollBtn);
        }

        // Set-cover button (bottom-right). Gold + persistent when this is the cover.
        const coverBtn = document.createElement('button');
        coverBtn.className = 'g-cover-btn';
        coverBtn.textContent = '★';
        // Cover is resolved server-side (explicit choice, else the oldest photo) and
        // passed in, so the star is stable as new detections arrive.
        const isCover = coverId && String(item.detection_id) === String(coverId);
        coverBtn.classList.toggle('is-cover', !!isCover);
        coverBtn.title = isCover ? 'Current cover photo' : 'Set as cover photo';
        coverBtn.addEventListener('click', async e => {
          e.stopPropagation();
          let resp;
          try {
            resp = await fetch(`/api/identities/${identityId}/cover`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ detection_id: item.detection_id }),
            });
          } catch (err) {
            if (window.showToast) showToast('Could not set the cover photo (network error).', 'error');
            return;
          }
          if (resp.ok) {
            container.querySelectorAll('.g-cover-btn.is-cover').forEach(b => {
              b.classList.remove('is-cover');
              b.title = 'Set as cover photo';
            });
            coverBtn.classList.add('is-cover');
            coverBtn.title = 'Current cover photo';
            coverId = String(item.detection_id);
            if (window.showToast) showToast('Cover photo updated', 'success');
          } else if (window.showToast) {
            showToast('Could not set the cover photo.', 'error');
          }
        });
        el.appendChild(coverBtn);

        // Checkbox (top-left, hover + selected)
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'g-check';
        cb.checked = isSelected;
        el.appendChild(cb);

        function toggleItem(force) {
          const next = force !== undefined ? force : !selected.has(item.detection_id);
          cb.checked = next;
          if (next) selected.add(item.detection_id);
          else selected.delete(item.detection_id);
          el.classList.toggle('selected', next);
          updateBulkBar();
        }

        cb.addEventListener('click', e => {
          e.stopPropagation();   // don't bubble to el
          toggleItem(cb.checked);
        });

        // Clicking anywhere on the photo always toggles selection.
        // Label correction is done via the "Change identity" bulk button.
        el.addEventListener('click', () => toggleItem());

        row.appendChild(el);
      });
      container.appendChild(row);
    });
  }

  function packRows(items, W) {
    const rows = [];
    let cur = [], sumAR = 0;
    items.forEach(item => {
      const ar = item.nw / (item.nh || 1);
      cur.push({ ...item, ar });
      sumAR += ar;
      if (sumAR * TARGET_H + GAP * (cur.length - 1) >= W) {
        rows.push({ items: cur, height: (W - GAP * (cur.length - 1)) / sumAR });
        cur = []; sumAR = 0;
      }
    });
    if (cur.length) rows.push({ items: cur, height: TARGET_H });
    return rows;
  }

  let sentinelVisible = false;
  const sentinel = document.createElement('div');
  sentinel.style.height = '1px';
  container.after(sentinel);
  new IntersectionObserver(([e]) => {
    sentinelVisible = e.isIntersecting;
    if (e.isIntersecting) loadPage();
  }, { rootMargin: '300px' }).observe(sentinel);

  loadPage();
  window.addEventListener('resize', () => { if (allItems.length) render(); });
})();
