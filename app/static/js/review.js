'use strict';
(() => {
  const container = document.getElementById('review-list');
  if (!container) return;
  let cursor = null, hasMore = true, loading = false;
  const selected = new Set();

  // Face labels come from the shared autocomplete.js (loaded in base.html)

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ---------------------------------------------------------------------------
  // Bulk actions
  // ---------------------------------------------------------------------------
  const bulkCount      = document.getElementById('bulk-count');
  const bulkConfirmBtn = document.getElementById('bulk-confirm-btn');
  const bulkRejectBtn  = document.getElementById('bulk-reject-btn');
  const selectAllCb    = document.getElementById('select-all-cb');

  function updateBulkBar() {
    const n = selected.size;
    if (bulkCount)      bulkCount.textContent   = n;
    if (bulkConfirmBtn) bulkConfirmBtn.disabled  = n === 0;
    if (bulkRejectBtn)  bulkRejectBtn.disabled   = n === 0;
  }

  function decrementBadge(n = 1) {
    const badge = document.getElementById('review-count-badge');
    if (!badge) return;
    const next = Math.max(0, (parseInt(badge.textContent) || 0) - n);
    badge.textContent = next;
    badge.style.display = next === 0 ? 'none' : 'inline';
  }

  window.doBulkConfirm = async () => {
    const ids = [...selected];
    const items = ids.map(id => ({ detection_id: id, action: 'confirm' }));
    await fetch('/api/review/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(items) });
    ids.forEach(id => document.getElementById('rc-' + id)?.remove());
    decrementBadge(ids.length);
    selected.clear();
    if (selectAllCb) selectAllCb.checked = false;
    updateBulkBar();
  };

  window.doBulkReject = async () => {
    const ids = [...selected];
    const items = ids.map(id => ({ detection_id: id, action: 'reject' }));
    await fetch('/api/review/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(items) });
    ids.forEach(id => document.getElementById('rc-' + id)?.remove());
    decrementBadge(ids.length);
    selected.clear();
    if (selectAllCb) selectAllCb.checked = false;
    updateBulkBar();
  };

  window.doSelectAll = cb => {
    document.querySelectorAll('.rc-check').forEach(c => {
      c.checked = cb.checked;
      const id = parseInt(c.dataset.id);
      if (cb.checked) selected.add(id); else selected.delete(id);
    });
    updateBulkBar();
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  async function loadPage() {
    if (loading || !hasMore) return;
    loading = true;
    const params = new URLSearchParams({ limit: 20 });
    if (cursor) params.set('cursor', cursor);
    const resp = await fetch('/api/review?' + params);
    if (!resp.ok) { loading = false; return; }
    const data = await resp.json();
    hasMore = data.has_more;
    cursor = data.next_cursor;
    data.items.forEach(renderItem);
    loading = false;
    if (!hasMore) {
      if (!container.querySelector('.rc-card')) {
        const p = document.createElement('p');
        p.className = 'muted';
        p.style.cssText = 'text-align:center;padding:32px';
        p.textContent = 'No pending items in the review queue.';
        container.appendChild(p);
      }
      sentinel.remove();
    }
  }

  function renderItem(item) {
    const card = document.createElement('div');
    card.className = 'review-card rc-card';
    card.id = 'rc-' + item.detection_id;

    const matched = item.current_identity;
    const name = matched ? esc(matched.label) : null;
    const currentId = matched?.identity_id;

    // Pull similarity for the current match from the suggestions list
    const currentMatchData = item.suggested_matches.find(m => m.identity_id === currentId);
    const currentSim = currentMatchData
      ? `<span class="muted" style="font-size:12px;margin-left:6px">${(currentMatchData.similarity*100).toFixed(0)}% similarity</span>`
      : '';

    const suggestions = item.suggested_matches
      .filter(m => m.identity_id !== currentId)
      .map(m =>
        `<button class="suggest-btn" onclick="doReassign(${item.detection_id},${m.identity_id})">
           ${esc(m.label)} <span class="muted" style="font-size:11px">${(m.similarity*100).toFixed(0)}% match</span>
         </button>`
      ).join('');

    card.innerHTML = `
      <div style="display:flex;width:100%;align-items:flex-start;gap:8px">
        <input type="checkbox" class="rc-check" data-id="${item.detection_id}" style="margin-top:4px;flex-shrink:0">
        <img src="${esc(item.crop_url)}" alt=""
             style="width:110px;height:110px;object-fit:cover;border-radius:4px;flex-shrink:0">
        <div class="rc-info" style="flex:1;min-width:0">

          <div style="display:flex;width:100%;align-items:baseline;margin-bottom:10px">
            ${matched ? `
            <div>
              <span class="muted" style="font-size:12px">Matched to</span>
              <strong style="margin-left:4px">${name}</strong>${currentSim}
            </div>` : `
            <div><span class="muted">No match found</span></div>`}
            <span class="muted" style="font-size:11px;white-space:nowrap;margin-left:auto;padding-left:16px">
              ${item.suggested_matches.length ? `${(item.suggested_matches[0].similarity*100).toFixed(0)}% best match · ` : ''}${typeof formatDate !== 'undefined' ? formatDate(item.detected_at) : item.detected_at}
            </span>
          </div>

          ${matched ? `
          <div style="display:flex;gap:6px;margin-bottom:10px">
            <button class="btn btn-success" onclick="doConfirm(${item.detection_id})">Yes, this is ${name}</button>
            <button class="btn btn-danger"  onclick="doReject(${item.detection_id})">No, not ${name}</button>
          </div>` : `
          <div style="margin-bottom:10px">
            <button class="btn btn-ghost" onclick="doReject(${item.detection_id})">Dismiss</button>
          </div>`}

          ${suggestions ? `
          <div style="margin-bottom:8px">
            <div class="muted" style="font-size:11px;margin-bottom:4px">
              ${matched ? 'Or assign to someone else:' : 'Possible matches:'}
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:4px">${suggestions}</div>
          </div>` : ''}

          <div style="display:flex;align-items:center;gap:6px">
            <span class="muted" style="font-size:11px;white-space:nowrap">Assign to:</span>
            <span class="ra-wrap" style="display:inline-flex;gap:4px;position:relative;flex:1">
              <input type="text" id="ra-${item.detection_id}" placeholder="Type a name…"
                     style="width:100%;max-width:180px" autocomplete="off">
              <button class="btn btn-ghost" onclick="doReassignLabel(${item.detection_id})">Assign</button>
            </span>
          </div>

        </div>
      </div>`;

    card.querySelector('.rc-check').addEventListener('change', e => {
      const id = item.detection_id;
      if (e.target.checked) selected.add(id); else selected.delete(id);
      updateBulkBar();
    });

    // Wire autocomplete after the card is in the DOM
    const raInput = card.querySelector('#ra-' + item.detection_id);
    container.appendChild(card);
    makeAutocomplete(raInput);
  }

  function removeCard(id) {
    document.getElementById('rc-' + id)?.remove();
    selected.delete(id);
    decrementBadge(1);
    updateBulkBar();
  }

  window.doConfirm = async id => {
    await fetch('/api/review/' + id + '/confirm', { method: 'POST' });
    removeCard(id);
  };
  window.doReject = async id => {
    await fetch('/api/review/' + id + '/reject', { method: 'POST' });
    removeCard(id);
  };
  window.doReassign = async (id, identityId) => {
    await fetch('/api/review/' + id + '/reassign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity_id: identityId }),
    });
    removeCard(id);
  };
  window.doReassignLabel = async id => {
    const label = document.getElementById('ra-' + id)?.value.trim();
    if (!label) return;
    addFaceLabel(label);
    await fetch('/api/review/' + id + '/reassign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    removeCard(id);
  };

  const sentinel = document.createElement('div');
  container.after(sentinel);
  new IntersectionObserver(([e]) => { if (e.isIntersecting) loadPage(); }, { rootMargin: '300px' }).observe(sentinel);
  loadPage();
})();
