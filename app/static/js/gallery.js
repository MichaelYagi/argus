'use strict';
(() => {
  const container = document.getElementById('gallery');
  if (!container) return;

  const identityId   = container.dataset.identityId;
  const identityType = container.dataset.identityType;
  let   coverId      = container.dataset.coverId || null;
  const PAGE = 30;

  // Returns true when the identity no longer exists server-side (auto-purged).
  async function _identityGone() {
    try {
      const r = await fetch(`/api/identities/${identityId}/gallery?limit=1`);
      return r.status === 404;
    } catch (e) { return false; }
  }

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
    el.textContent = `${n} photo${n !== 1 ? 's' : ''}`;
  }
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
  const selected = new Set();
  const STATE_KEY = '_argus_gallery_' + identityId;

  function saveState() {
    try {
      history.replaceState({
        [STATE_KEY]: { scrollY: Math.round(window.scrollY), cursor, hasMore, items: allItems },
      }, '');
    } catch (_) {}
  }

  // Incremental layout state — avoids full DOM rebuild on each page load
  let pending = [];
  let tailEl = null;
  let lastW = 0;
  let resizeTimer;

  const loadingEl = document.getElementById('gallery-loading');
  const emptyEl   = document.getElementById('gallery-empty');
  const bulkBar   = document.getElementById('gallery-bulk-bar');
  const bulkCount = document.getElementById('gallery-bulk-count');

  function updateBulkBar() {
    if (bulkBar) bulkBar.style.display = selected.size === 0 ? 'none' : 'flex';
    if (bulkCount) bulkCount.textContent = selected.size;
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
        relayout();
        if (await _identityGone()) { location.href = '/?tab=' + identityType; return; }
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
      relayout();
      if (await _identityGone()) { location.href = '/?tab=' + identityType; return; }
      if (window.showToast) showToast(n + ' moved to ' + label, 'success');
    });
  };

  window.clearSelection = () => {
    selected.clear();
    updateBulkBar();
    // Update in-place — no relayout needed just to clear selection state
    container.querySelectorAll('.g-item.selected').forEach(el => el.classList.remove('selected'));
    container.querySelectorAll('.g-check').forEach(cb => { cb.checked = false; });
  };

  // ---------------------------------------------------------------------------
  // Per-item DOM builder (extracted so both appendItems and relayout share it)
  // ---------------------------------------------------------------------------
  function makeItemEl(item, height) {
    const ar = item.nw / (item.nh || 1);
    const w = Math.floor(ar * height);
    const isSelected = selected.has(item.detection_id);

    const el = document.createElement('div');
    el.className = 'g-item' + (isSelected ? ' selected' : '');
    el.dataset.id = item.detection_id;
    if (item.source_image_id) el.dataset.sourceId = item.source_image_id;
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

    const tagLink = document.createElement('a');
    tagLink.className = 'g-tag-link';
    tagLink.href = '/tag/' + item.source_image_id;
    tagLink.textContent = 'Tag';
    tagLink.addEventListener('click', e => { e.stopPropagation(); saveState(); });
    el.appendChild(tagLink);

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
          relayout();
          if (window.showToast) showToast('Detection deleted', 'success');
        } else if (window.showToast) {
          showToast('Could not delete the detection.', 'error');
        }
      }, { confirmText: 'Delete', danger: true });
    });
    el.appendChild(delBtn);

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

    const coverBtn = document.createElement('button');
    coverBtn.className = 'g-cover-btn';
    coverBtn.textContent = '★';
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
      e.stopPropagation();
      toggleItem(cb.checked);
    });

    el.addEventListener('click', () => toggleItem());

    return el;
  }

  function buildRowEl(rowItems, height) {
    const row = document.createElement('div');
    row.className = 'g-row';
    rowItems.forEach(item => row.appendChild(makeItemEl(item, height)));
    return row;
  }

  // ---------------------------------------------------------------------------
  // Incremental layout — only appends new rows, never rebuilds existing ones
  // ---------------------------------------------------------------------------
  function appendItems(newItems) {
    if (tailEl) { tailEl.remove(); tailEl = null; }
    const W = container.clientWidth || lastW;
    if (W) lastW = W;

    let cur = [...pending];
    let sumAR = cur.reduce((s, item) => s + item.nw / (item.nh || 1), 0);

    newItems.forEach(item => {
      cur.push(item);
      sumAR += item.nw / (item.nh || 1);
      if (sumAR * TARGET_H + GAP * (cur.length - 1) >= W) {
        container.appendChild(buildRowEl(cur, (W - GAP * (cur.length - 1)) / sumAR));
        cur = []; sumAR = 0;
      }
    });
    pending = cur;
    if (pending.length) {
      tailEl = buildRowEl(pending, TARGET_H);
      container.appendChild(tailEl);
    }
  }

  // Full rebuild — only used after item deletion/bulk ops or viewport width change
  function relayout() {
    container.innerHTML = '';
    tailEl = null;
    pending = [];
    const W = container.clientWidth || lastW;
    if (W) lastW = W;
    const rows = packRows(allItems, W);
    if (!rows.length) return;
    const last = rows[rows.length - 1];
    const lastIsPartial = last.height === TARGET_H;
    (lastIsPartial ? rows.slice(0, -1) : rows).forEach(({ items: ri, height }) => {
      container.appendChild(buildRowEl(ri, height));
    });
    if (lastIsPartial) {
      pending = last.items;
      tailEl = buildRowEl(last.items, TARGET_H);
      container.appendChild(tailEl);
    }
  }

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
    if (!resp.ok) {
      if (resp.status === 404) { location.href = '/?tab=' + identityType; return; }
      loading = false; return;
    }
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
    appendItems(loaded);
    loading = false;
    if (loadingEl) loadingEl.hidden = true;
    if (!hasMore) sentinel.remove();
    else if (sentinelVisible) loadPage();
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

  function observe() {
    new IntersectionObserver(([e]) => {
      sentinelVisible = e.isIntersecting;
      if (e.isIntersecting) loadPage();
    }, { rootMargin: '300px' }).observe(sentinel);
  }

  function reset() {
    allItems.length = 0;
    pending = [];
    if (tailEl) { tailEl.remove(); tailEl = null; }
    cursor = null; hasMore = true; loading = false;
    selected.clear();
    updateBulkBar();
    container.innerHTML = '';
    if (loadingEl) loadingEl.hidden = false;
    if (emptyEl) emptyEl.hidden = true;
    if (!sentinel.parentNode) container.after(sentinel);
    loadPage();
  }

  const navType    = performance.getEntriesByType('navigation')[0]?.type;
  const savedState = navType === 'back_forward' ? history.state?.[STATE_KEY] : null;

  if (savedState) {
    allItems.push(...savedState.items);
    cursor  = savedState.cursor  ?? null;
    hasMore = savedState.hasMore ?? true;
    loading = true;
    requestAnimationFrame(() => {
      appendItems(savedState.items);
      loading = false;
      if (loadingEl) loadingEl.hidden = true;
      if (!hasMore) sentinel.remove();
      observe();
      requestAnimationFrame(() => {
        const lastId = sessionStorage.getItem('argus_last_viewed');
        if (lastId) {
          const el = container.querySelector('[data-source-id="' + lastId + '"]');
          if (el) {
            sessionStorage.removeItem('argus_last_viewed');
            const r = el.getBoundingClientRect();
            if (r.top < 0 || r.bottom > window.innerHeight) {
              el.scrollIntoView({ behavior: 'instant', block: 'center' });
            }
            return;
          }
        }
        if (savedState.scrollY) window.scrollTo(0, savedState.scrollY);
      });
    });
  } else {
    observe();
    loadPage();
  }

  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    const lastId = sessionStorage.getItem('argus_last_viewed');
    if (!lastId) return;
    const el = container.querySelector('[data-source-id="' + lastId + '"]');
    if (!el) return;
    sessionStorage.removeItem('argus_last_viewed');
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) {
      el.scrollIntoView({ behavior: 'instant', block: 'center' });
    }
  });

  // Wire back arrow to history.back() so the dashboard restores scroll on return.
  const backLink = document.getElementById('back-link');
  if (backLink && history.length > 1) {
    backLink.addEventListener('click', e => { e.preventDefault(); history.back(); });
  }

  // Only relayout when the container WIDTH changes — mobile toolbar show/hide
  // changes viewport HEIGHT only and doesn't affect the justified row packing.
  window.addEventListener('resize', () => {
    if (!allItems.length) return;
    const w = container.clientWidth;
    if (w === lastW) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(relayout, 150);
  });
})();
