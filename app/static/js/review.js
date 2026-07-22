'use strict';
(() => {
  const suggestedList = document.getElementById('suggested-list');
  const nomatchList   = document.getElementById('nomatch-list');
  if (!suggestedList || !nomatchList) return;

  let cursor = null, hasMore = true, loading = false;

  // Independent selection per section so a checkbox's meaning is unambiguous:
  // Suggested → Confirm; No match → Dismiss.
  const selSg = new Set();   // suggested-match detection ids
  const selNm = new Set();   // no-match detection ids
  const itemCache = new Map(); // detection_id -> item (for moving rejected cards to no-match)

  // Tab counts (cards currently rendered in each list).
  let countSg = 0, countNm = 0;

  function updateTabBadges() {
    const sgBadge = document.getElementById('sg-tab-badge');
    const nmBadge = document.getElementById('nm-tab-badge');
    if (sgBadge) { sgBadge.textContent = countSg || ''; sgBadge.style.display = countSg ? '' : 'none'; }
    if (nmBadge) { nmBadge.textContent = countNm || ''; nmBadge.style.display = countNm ? '' : 'none'; }
  }

  const toTop = document.getElementById('scroll-top');
  const updateToTop = () => { if (toTop) toTop.style.display = window.scrollY > 300 ? 'flex' : 'none'; };
  window.addEventListener('scroll', updateToTop, { passive: true });
  if (toTop) toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  window.switchTab = tab => {
    document.getElementById('panel-sg').style.display = tab === 'sg' ? '' : 'none';
    document.getElementById('panel-nm').style.display = tab === 'nm' ? '' : 'none';
    document.getElementById('tab-sg').classList.toggle('active', tab === 'sg');
    document.getElementById('tab-nm').classList.toggle('active', tab === 'nm');
    window.scrollTo({ top: 0, behavior: 'instant' });
  };

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ---------------------------------------------------------------------------
  // Per-section bulk bars
  // ---------------------------------------------------------------------------
  const sgCount = document.getElementById('sg-count');
  const sgBtn   = document.getElementById('sg-confirm-btn');
  const sgAll   = document.getElementById('sg-select-all');
  const nmCount = document.getElementById('nm-count');
  const nmBtn   = document.getElementById('nm-dismiss-btn');
  const nmAll   = document.getElementById('nm-select-all');

  function updateBars() {
    if (sgCount) sgCount.textContent = selSg.size;
    if (sgBtn)   sgBtn.disabled = selSg.size === 0;
    if (nmCount) nmCount.textContent = selNm.size;
    if (nmBtn)   nmBtn.disabled = selNm.size === 0;
  }

  function decrementBadge(n = 1) {
    const badge = document.getElementById('review-count-badge');
    if (!badge) return;
    const next = Math.max(0, (parseInt(badge.textContent) || 0) - n);
    badge.textContent = next;
    badge.style.display = next === 0 ? 'none' : 'inline';
  }

  // Single feedback helper so every review action reports success/failure. Returns
  // true only when the server accepted it, so callers don't optimistically remove
  // cards for requests that actually failed.
  async function sendReview(url, opts) {
    try {
      const resp = await fetch(url, opts);
      if (!resp.ok) {
        if (window.showToast) showToast('Action failed (' + resp.status + ').', 'error');
        return false;
      }
      return true;
    } catch (e) {
      if (window.showToast) showToast('Action failed (network error).', 'error');
      return false;
    }
  }

  async function bulkAction(ids, action, sel, allCb) {
    if (!ids.length) return;
    const items = ids.map(id => ({ detection_id: id, action }));
    const ok = await sendReview('/api/review/bulk', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(items),
    });
    if (!ok) return;
    ids.forEach(id => {
      const card = document.getElementById('rc-' + id);
      if (card) {
        if (card.closest('#suggested-list')) countSg = Math.max(0, countSg - 1);
        else countNm = Math.max(0, countNm - 1);
        card.remove();
      }
      sel.delete(id);
    });
    updateTabBadges();
    decrementBadge(ids.length);
    if (allCb) allCb.checked = false;
    updateBars();
    checkEmpty();
    if (window.showToast) {
      const verb = action === 'confirm' ? ' confirmed' : ' dismissed';
      showToast(ids.length + verb, 'success');
    }
  }

  window.sgConfirm = () => bulkAction([...selSg], 'confirm',     selSg, sgAll);
  window.nmDismiss = () => bulkAction([...selNm], 'unidentify',  selNm, nmAll);

  window.sgSelectAll = cb => toggleAll(suggestedList, selSg, cb.checked);
  window.nmSelectAll = cb => toggleAll(nomatchList, selNm, cb.checked);

  function toggleAll(listEl, sel, on) {
    listEl.querySelectorAll('.rc-check').forEach(c => {
      c.checked = on;
      const id = parseInt(c.dataset.id);
      if (on) sel.add(id); else sel.delete(id);
    });
    updateBars();
  }

  // ---------------------------------------------------------------------------
  // Load + render
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
    if (!hasMore) { sentinel.remove(); checkEmpty(); }
  }

  function checkEmpty() {
    emptyMsg(suggestedList, 'No suggested matches.');
    emptyMsg(nomatchList, 'No unmatched faces.');
  }
  function emptyMsg(listEl, text) {
    if (loading || hasMore) return;
    const has = listEl.querySelector('.rc-card');
    let msg = listEl.querySelector('.rc-empty');
    if (!has && !msg) {
      msg = document.createElement('p');
      msg.className = 'muted rc-empty';
      msg.style.cssText = 'text-align:center;padding:28px;font-size:13px';
      msg.textContent = text;
      listEl.appendChild(msg);
    } else if (has && msg) {
      msg.remove();
    }
  }

  function renderItem(item) {
    itemCache.set(item.detection_id, item);
    const matched = item.current_identity;
    const name = matched ? esc(matched.label) : null;
    const currentId = matched ? matched.identity_id : null;
    const listEl = matched ? suggestedList : nomatchList;
    const sel = matched ? selSg : selNm;
    if (matched) countSg++; else countNm++;
    updateTabBadges();

    const currentMatchData = item.suggested_matches.find(m => m.identity_id === currentId);
    const currentSimPct = currentMatchData ? `${(currentMatchData.similarity*100).toFixed(0)}%` : null;

    const suggestions = item.suggested_matches
      .filter(m => m.identity_id !== currentId)
      .map(m =>
        `<button class="suggest-btn" onclick="doReassign(${item.detection_id},${m.identity_id})">
           ${esc(m.label)} <span class="muted" style="font-size:11px">${(m.similarity*100).toFixed(0)}% match</span>
         </button>`
      ).join('');

    const dateStr = typeof formatDate !== 'undefined' ? formatDate(item.detected_at) : item.detected_at;
    const bestMatchPrefix = item.suggested_matches.length
      ? `${(item.suggested_matches[0].similarity*100).toFixed(0)}% best match · ` : '';

    const card = document.createElement('div');
    card.className = 'review-card rc-card';
    card.id = 'rc-' + item.detection_id;
    card.innerHTML = `
      <div style="display:flex;width:100%;align-items:flex-start;gap:12px">
        <input type="checkbox" class="rc-check" data-id="${item.detection_id}" style="margin-top:4px;flex-shrink:0">
        ${matched ? `
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;flex-shrink:0;width:110px">
          <img src="${esc(item.crop_url)}" alt=""
               style="width:110px;height:110px;object-fit:cover;border-radius:4px;${item.source_image_url ? 'cursor:zoom-in' : ''}"
               ${item.source_image_url ? `onclick="openSourceModal('${esc(item.source_image_url)}')"` : ''}>
          <div style="text-align:center;line-height:1.5;width:100%">
            <strong style="font-size:13px">${name}</strong>
            ${currentSimPct ? `<br><span class="muted" style="font-size:11px">${currentSimPct} similarity</span>` : ''}
          </div>
        </div>` : `
        <img src="${esc(item.crop_url)}" alt=""
             style="width:110px;height:110px;object-fit:cover;border-radius:4px;flex-shrink:0;${item.source_image_url ? 'cursor:zoom-in' : ''}"
             ${item.source_image_url ? `onclick="openSourceModal('${esc(item.source_image_url)}')"` : ''}>`}
        <div class="rc-info" style="flex:1;min-width:0">

          <div class="rc-meta-row" style="display:flex;width:100%;align-items:baseline;margin-bottom:10px">
            ${matched ? '' : '<div><span class="muted">No match found</span></div>'}
            <span class="muted rc-meta" style="font-size:11px;white-space:nowrap;margin-left:auto;padding-left:${matched ? 0 : 16}px">
              ${bestMatchPrefix}${dateStr}
            </span>
          </div>

          ${matched ? `
          <div style="display:flex;gap:6px;margin-bottom:10px;align-items:center">
            <button class="btn btn-success" onclick="doConfirm(${item.detection_id})">Yes, this is ${name}</button>
            <button class="btn btn-danger"  onclick="doReject(${item.detection_id})">No, not ${name}</button>
            <a class="rc-tag-link" href="/tag/${item.source_image_id}?focus=${item.detection_id}" style="font-size:12px;margin-left:4px;white-space:nowrap">View in image</a>
          </div>` : `
          <div style="margin-bottom:10px;display:flex;align-items:center;gap:8px">
            <button class="btn btn-ghost" onclick="doDismiss(${item.detection_id})">Dismiss</button>
            <a class="rc-tag-link" href="/tag/${item.source_image_id}?focus=${item.detection_id}" style="font-size:12px;white-space:nowrap">View in image</a>
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
      if (e.target.checked) sel.add(id); else sel.delete(id);
      updateBars();
    });
    card.querySelector('.rc-tag-link').addEventListener('click', () => {
      sessionStorage.removeItem('argus_nav_ids');
      sessionStorage.setItem('argus_nav_back', location.href);
      sessionStorage.setItem('argus_nav_depth', '0');
    });

    const raInput = card.querySelector('#ra-' + item.detection_id);
    listEl.appendChild(card);
    makeAutocomplete(raInput);
  }

  function removeCard(id) {
    const card = document.getElementById('rc-' + id);
    if (card) {
      const inSg = card.closest('#suggested-list');
      if (inSg) countSg = Math.max(0, countSg - 1);
      else countNm = Math.max(0, countNm - 1);
      card.remove();
      updateTabBadges();
    }
    selSg.delete(id); selNm.delete(id);
    decrementBadge(1);
    updateBars();
    checkEmpty();
  }

  window.doConfirm = async id => {
    if (await sendReview('/api/review/' + id + '/confirm', { method: 'POST' })) removeCard(id);
  };
  // "No, not [name]" — mark rejected, keep in queue under the no-match section.
  window.doReject = async id => {
    if (!await sendReview('/api/review/' + id + '/reject', { method: 'POST' })) return;
    removeCard(id);
    const item = itemCache.get(id);
    if (item) renderItem({ ...item, current_identity: null, suggested_matches: [] });
    checkEmpty();
  };
  // "Dismiss" — remove from queue and send to /unidentified.
  window.doDismiss = async id => {
    if (await sendReview('/api/review/' + id + '/unidentify', { method: 'POST' })) removeCard(id);
  };
  window.doReassign = async (id, identityId) => {
    const ok = await sendReview('/api/review/' + id + '/reassign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity_id: identityId }),
    });
    if (ok) removeCard(id);
  };
  window.doReassignLabel = async id => {
    const label = document.getElementById('ra-' + id)?.value.trim();
    if (!label) return;
    const ok = await sendReview('/api/review/' + id + '/reassign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (ok) { addFaceLabel(label); removeCard(id); }
  };

  const sentinel = document.createElement('div');
  document.getElementById('panel-nm').after(sentinel);
  new IntersectionObserver(([e]) => { if (e.isIntersecting) loadPage(); }, { rootMargin: '300px' }).observe(sentinel);

  function reset() {
    cursor = null; hasMore = true; loading = false;
    selSg.clear(); selNm.clear();
    countSg = 0; countNm = 0;
    updateBars();
    updateTabBadges();
    suggestedList.innerHTML = '';
    nomatchList.innerHTML = '';
    if (!sentinel.parentNode) document.getElementById('panel-nm').after(sentinel);
    loadPage();
  }

  loadPage();
  window.addEventListener('pageshow', e => { if (e.persisted) reset(); });
})();
