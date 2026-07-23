'use strict';
(() => {
  const suggestedList = document.getElementById('suggested-list');
  const nomatchList   = document.getElementById('nomatch-list');
  if (!suggestedList || !nomatchList) return;

  const selSg = new Set();
  const selNm = new Set();
  const itemCache = new Map();

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

  function updateTabBadge(listEl, count) {
    const id = listEl === suggestedList ? 'sg-tab-badge' : 'nm-tab-badge';
    const badge = document.getElementById(id);
    if (!badge) return;
    badge.textContent = count || '';
    badge.style.display = count ? '' : 'none';
  }

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
        const loader = card.closest('#suggested-list') ? sgLoader : nmLoader;
        loader.count = Math.max(0, loader.count - 1);
        updateTabBadge(loader.listEl, loader.count);
        card.remove();
      }
      sel.delete(id);
    });
    decrementBadge(ids.length);
    if (allCb) allCb.checked = false;
    updateBars();
    sgLoader.checkEmpty();
    nmLoader.checkEmpty();
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
  // Independent loader factory — one instance per tab
  // ---------------------------------------------------------------------------
  function makeLoader(listEl, hasSuggestion, sel) {
    const loader = {
      listEl,
      hasSuggestion,
      sel,
      cursor: null,
      hasMore: true,
      loading: false,
      count: 0,
      sentinel: null,
    };

    loader.checkEmpty = function () {
      if (loader.loading || loader.hasMore) return;
      const hasCards = listEl.querySelector('.rc-card');
      let msg = listEl.querySelector('.rc-empty');
      const text = hasSuggestion ? 'No suggested matches.' : 'No unmatched faces.';
      if (!hasCards && !msg) {
        msg = document.createElement('p');
        msg.className = 'muted rc-empty';
        msg.style.cssText = 'text-align:center;padding:28px;font-size:13px';
        msg.textContent = text;
        listEl.appendChild(msg);
      } else if (hasCards && msg) {
        msg.remove();
      }
    };

    loader.loadPage = async function () {
      if (loader.loading || !loader.hasMore) return;
      loader.loading = true;
      const params = new URLSearchParams({ limit: 20, has_suggestion: hasSuggestion });
      if (loader.cursor) params.set('cursor', loader.cursor);
      const resp = await fetch('/api/review?' + params);
      if (!resp.ok) { loader.loading = false; return; }
      const data = await resp.json();
      loader.hasMore = data.has_more;
      loader.cursor = data.next_cursor;
      data.items.forEach(item => renderItem(item, loader));
      loader.loading = false;
      if (!loader.hasMore) {
        loader.sentinel.remove();
        loader.checkEmpty();
      }
    };

    loader.reset = function () {
      loader.cursor = null;
      loader.hasMore = true;
      loader.loading = false;
      loader.count = 0;
      updateTabBadge(listEl, 0);
      listEl.innerHTML = '';
      if (loader.sentinel && !loader.sentinel.parentNode) {
        listEl.after(loader.sentinel);
      }
      loader.loadPage();
    };

    const sentinel = document.createElement('div');
    loader.sentinel = sentinel;
    listEl.after(sentinel);
    new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) loader.loadPage(); },
      { rootMargin: '300px' }
    ).observe(sentinel);

    return loader;
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  function renderItem(item, loader) {
    itemCache.set(item.detection_id, item);
    const matched = item.current_identity;
    const name = matched ? esc(matched.label) : null;
    const currentId = matched ? matched.identity_id : null;
    const { listEl, sel } = loader;
    loader.count++;
    updateTabBadge(listEl, loader.count);

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
      const loader = card.closest('#suggested-list') ? sgLoader : nmLoader;
      loader.count = Math.max(0, loader.count - 1);
      updateTabBadge(loader.listEl, loader.count);
      card.remove();
    }
    selSg.delete(id); selNm.delete(id);
    decrementBadge(1);
    updateBars();
    sgLoader.checkEmpty();
    nmLoader.checkEmpty();
  }

  window.doConfirm = async id => {
    if (await sendReview('/api/review/' + id + '/confirm', { method: 'POST' })) removeCard(id);
  };
  window.doReject = async id => {
    if (!await sendReview('/api/review/' + id + '/reject', { method: 'POST' })) return;
    removeCard(id);
    const item = itemCache.get(id);
    if (item) renderItem({ ...item, current_identity: null, suggested_matches: [] }, nmLoader);
    nmLoader.checkEmpty();
  };
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

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  const sgLoader = makeLoader(suggestedList, true,  selSg);
  const nmLoader = makeLoader(nomatchList,   false, selNm);

  sgLoader.loadPage();
  nmLoader.loadPage();

  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    sgLoader.reset();
    nmLoader.reset();
  });
})();
