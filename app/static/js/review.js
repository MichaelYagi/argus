'use strict';
(() => {
  const suggestedList = document.getElementById('suggested-list');
  const nomatchList   = document.getElementById('nomatch-list');
  const mismatchList   = document.getElementById('mismatch-list');
  if (!suggestedList || !nomatchList) return;

  const selSg = new Set();
  const selNm = new Set();
  const selMm = new Set();
  const itemCache = new Map();
  let focusedCard = null;

  const toTop = document.getElementById('scroll-top');
  const updateToTop = () => { if (toTop) toTop.style.display = window.scrollY > 300 ? 'flex' : 'none'; };
  window.addEventListener('scroll', updateToTop, { passive: true });
  if (toTop) toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  function setFocusedCard(card) {
    if (focusedCard) focusedCard.classList.remove('rc-focused');
    focusedCard = card || null;
    if (focusedCard) {
      focusedCard.classList.add('rc-focused');
      focusedCard.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  function getActivePanelCards() {
    const panels = [
      document.getElementById('panel-sg'),
      document.getElementById('panel-nm'),
      document.getElementById('panel-mm'),
    ];
    const lists = [suggestedList, nomatchList, mismatchList];
    for (let i = 0; i < panels.length; i++) {
      if (panels[i] && panels[i].style.display !== 'none') {
        return [...lists[i].querySelectorAll('.rc-card')];
      }
    }
    return [];
  }

  window.switchTab = (tab, scroll = true) => {
    setFocusedCard(null);
    document.getElementById('panel-sg').style.display = tab === 'sg' ? '' : 'none';
    document.getElementById('panel-nm').style.display = tab === 'nm' ? '' : 'none';
    document.getElementById('panel-mm').style.display = tab === 'mm' ? '' : 'none';
    document.getElementById('tab-sg').classList.toggle('active', tab === 'sg');
    document.getElementById('tab-nm').classList.toggle('active', tab === 'nm');
    document.getElementById('tab-mm').classList.toggle('active', tab === 'mm');
    if (scroll) window.scrollTo({ top: 0, behavior: 'instant' });
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
  const mmCount      = document.getElementById('mm-count');
  const mmBtn        = document.getElementById('mm-confirm-btn');
  const mmDismissBtn = document.getElementById('mm-dismiss-btn');
  const mmAll        = document.getElementById('mm-select-all');

  function updateBars() {
    if (sgCount) sgCount.textContent = selSg.size;
    if (sgBtn)   sgBtn.disabled = selSg.size === 0;
    if (nmCount) nmCount.textContent = selNm.size;
    if (nmBtn)   nmBtn.disabled = selNm.size === 0;
    if (mmCount)      mmCount.textContent = selMm.size;
    if (mmBtn)        mmBtn.disabled = selMm.size === 0;
    if (mmDismissBtn) mmDismissBtn.disabled = selMm.size === 0;
  }

  function decrementBadge(n = 1) {
    const badge = document.getElementById('review-count-badge');
    if (!badge) return;
    const next = Math.max(0, (parseInt(badge.textContent) || 0) - n);
    badge.textContent = next;
    badge.style.display = next === 0 ? 'none' : 'inline';
  }

  // Tab badge totals — fetched upfront, decremented as items are reviewed.
  const tabCounts = { sg: 0, nm: 0, mm: 0 };

  function updateTabBadge(tab, delta) {
    tabCounts[tab] = Math.max(0, tabCounts[tab] + delta);
    const badge = document.getElementById(tab + '-tab-badge');
    if (!badge) return;
    const n = tabCounts[tab];
    badge.textContent = n || '';
    badge.style.display = n ? '' : 'none';
  }

  function setTabBadge(tab, n) {
    tabCounts[tab] = n;
    const badge = document.getElementById(tab + '-tab-badge');
    if (badge) { badge.textContent = n || ''; badge.style.display = n ? '' : 'none'; }
  }

  async function fetchTabCounts() {
    const [sgResp, nmResp] = await Promise.all([
      fetch('/api/review/count?has_suggestion=true'),
      fetch('/api/review/count?has_suggestion=false'),
    ]);
    if (sgResp.ok) { const d = await sgResp.json(); setTabBadge('sg', d.count); }
    if (nmResp.ok) { const d = await nmResp.json(); setTabBadge('nm', d.count); }
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
        const tab = card.closest('#suggested-list') ? 'sg' : 'nm';
        updateTabBadge(tab, -1);
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
  window.mmSelectAll = cb => toggleAll(mismatchList, selMm, cb.checked);

  window.mmConfirm = async () => {
    const ids = [...selMm];
    if (!ids.length) return;
    const ok = await sendReview('/api/review/mismatches/dismiss', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ detection_ids: ids }),
    });
    if (!ok) return;
    ids.forEach(id => removeMismatchCard(id));
    selMm.clear();
    if (mmAll) mmAll.checked = false;
    updateBars();
    if (window.showToast) showToast(ids.length + ' confirmed', 'success');
  };

  window.mmDismiss = async () => {
    const ids = [...selMm];
    if (!ids.length) return;
    const items = ids.map(id => ({ detection_id: id, action: 'unidentify' }));
    const ok = await sendReview('/api/review/bulk', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(items),
    });
    if (!ok) return;
    ids.forEach(id => removeMismatchCard(id));
    selMm.clear();
    if (mmAll) mmAll.checked = false;
    updateBars();
    if (window.showToast) showToast(ids.length + ' dismissed', 'success');
  };

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
          <img src="${esc(item.crop_url)}" alt="" class="rc-crop-img"
               style="width:110px;height:110px;object-fit:cover;border-radius:4px;${item.source_image_url ? 'cursor:zoom-in' : ''}">
          <div style="text-align:center;line-height:1.5;width:100%">
            <strong style="font-size:13px">${name}</strong>
            ${currentSimPct ? `<br><span class="muted" style="font-size:11px">${currentSimPct} similarity</span>` : ''}
          </div>
        </div>` : `
        <img src="${esc(item.crop_url)}" alt="" class="rc-crop-img"
             style="width:110px;height:110px;object-fit:cover;border-radius:4px;flex-shrink:0;${item.source_image_url ? 'cursor:zoom-in' : ''}">`}
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
      sessionStorage.setItem('argus_nav_tab', loader.hasSuggestion ? 'sg' : 'nm');
      sessionStorage.setItem('argus_nav_scroll', String(window.scrollY));
    });
    if (item.source_image_url) {
      card.querySelector('.rc-crop-img').addEventListener('click', e => {
        e.stopPropagation();
        openSourceModal(item.source_image_url, item.bbox);
      });
    }

    const raInput = card.querySelector('#ra-' + item.detection_id);
    listEl.appendChild(card);
    makeAutocomplete(raInput);
  }

  function removeCard(id) {
    const card = document.getElementById('rc-' + id);
    if (card) {
      if (card === focusedCard) {
        const cards = getActivePanelCards();
        const idx = cards.indexOf(card);
        setFocusedCard(cards[idx + 1] || cards[idx - 1] || null);
      }
      const tab = card.closest('#suggested-list') ? 'sg' : 'nm';
      updateTabBadge(tab, -1);
      card.remove();
    }
    selSg.delete(id); selNm.delete(id);
    decrementBadge(1);
    updateBars();
    sgLoader.checkEmpty();
    nmLoader.checkEmpty();
  }

  function removeMismatchCard(id) {
    const card = document.getElementById('rc-mm-' + id);
    if (card) {
      if (card === focusedCard) {
        const cards = getActivePanelCards();
        const idx = cards.indexOf(card);
        setFocusedCard(cards[idx + 1] || cards[idx - 1] || null);
      }
      card.remove();
    }
    selMm.delete(id);
    updateBars();
    updateTabBadge('mm', -1);
    if (mismatchList && !mismatchList.querySelector('.rc-card')) {
      let msg = mismatchList.querySelector('.rc-empty');
      if (!msg) {
        msg = document.createElement('p');
        msg.className = 'muted rc-empty';
        msg.style.cssText = 'text-align:center;padding:28px;font-size:13px';
        msg.textContent = 'No mismatches found.';
        mismatchList.appendChild(msg);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Mismatches tab — load, render, actions
  // ---------------------------------------------------------------------------
  function renderMismatchItem(item) {
    const name = esc(item.current_identity.label);
    const simPct = Math.round(item.similarity * 100);
    const dateStr = typeof formatDate !== 'undefined' ? formatDate(item.detected_at) : item.detected_at;

    const card = document.createElement('div');
    card.className = 'review-card rc-card';
    card.id = 'rc-mm-' + item.detection_id;
    card.innerHTML = `
      <div style="display:flex;width:100%;align-items:flex-start;gap:12px">
        <input type="checkbox" class="rc-check" data-id="${item.detection_id}" style="margin-top:4px;flex-shrink:0">
        <img src="${esc(item.crop_url)}" alt="" class="rc-crop-img"
             style="width:110px;height:110px;object-fit:cover;border-radius:4px;flex-shrink:0;${item.source_image_url ? 'cursor:zoom-in' : ''}">
        <div class="rc-info" style="flex:1;min-width:0">

          <div class="rc-meta-row" style="display:flex;width:100%;align-items:baseline;margin-bottom:10px">
            <div>
              <strong style="font-size:14px">${name}</strong>
              <span class="muted" style="font-size:12px;margin-left:8px">${simPct}% match</span>
            </div>
            <span class="muted rc-meta" style="font-size:11px;white-space:nowrap;margin-left:auto;padding-left:16px">${dateStr}</span>
          </div>

          <div style="display:flex;gap:6px;margin-bottom:10px;align-items:center;flex-wrap:wrap">
            <button class="btn btn-ghost" onclick="doMismatchOk(${item.detection_id})">Looks correct</button>
            <button class="btn btn-ghost" onclick="doMismatchDismiss(${item.detection_id})">Dismiss</button>
            ${item.source_image_id
              ? `<a class="rc-tag-link" href="/tag/${item.source_image_id}?focus=${item.detection_id}" style="font-size:12px;white-space:nowrap">View in image</a>`
              : ''}
          </div>

          <div style="display:flex;align-items:center;gap:6px">
            <span class="muted" style="font-size:11px;white-space:nowrap">Reassign to:</span>
            <span class="ra-wrap" style="display:inline-flex;gap:4px;position:relative;flex:1">
              <input type="text" id="ra-mm-${item.detection_id}" placeholder="Type a name…"
                     style="width:100%;max-width:180px" autocomplete="off">
              <button class="btn btn-ghost" onclick="doMismatchReassignLabel(${item.detection_id})">Assign</button>
            </span>
          </div>

        </div>
      </div>`;

    if (item.source_image_url) {
      card.querySelector('.rc-crop-img').addEventListener('click', e => {
        e.stopPropagation();
        openSourceModal(item.source_image_url, item.bbox);
      });
    }
    const tagLink = card.querySelector('.rc-tag-link');
    if (tagLink) {
      tagLink.addEventListener('click', () => {
        sessionStorage.removeItem('argus_nav_ids');
        sessionStorage.setItem('argus_nav_back', location.href);
        sessionStorage.setItem('argus_nav_depth', '0');
        sessionStorage.setItem('argus_nav_tab', 'mm');
        sessionStorage.setItem('argus_nav_scroll', String(window.scrollY));
      });
    }
    card.querySelector('.rc-check').addEventListener('change', e => {
      const id = item.detection_id;
      if (e.target.checked) selMm.add(id); else selMm.delete(id);
      updateBars();
    });
    const raInput = card.querySelector('#ra-mm-' + item.detection_id);
    mismatchList.appendChild(card);
    if (window.makeAutocomplete) makeAutocomplete(raInput);
  }

  async function loadMismatches() {
    if (!mismatchList) return;
    mismatchList.innerHTML = '';
    const resp = await fetch('/api/review/mismatches');
    if (!resp.ok) {
      mismatchList.innerHTML = '<p class="muted" style="text-align:center;padding:28px;font-size:13px">Failed to load.</p>';
      return;
    }
    const data = await resp.json();
    setTabBadge('mm', data.count);
    if (!data.items.length) {
      mismatchList.innerHTML = '<p class="muted rc-empty" style="text-align:center;padding:28px;font-size:13px">No mismatches found.</p>';
      return;
    }
    data.items.forEach(renderMismatchItem);
  }

  window.doMismatchOk = async id => {
    const ok = await sendReview('/api/review/mismatches/' + id + '/dismiss', { method: 'POST' });
    if (ok) {
      removeMismatchCard(id);
      if (window.showToast) showToast('Marked as correct', 'success');
    }
  };

  window.doMismatchDismiss = async id => {
    if (await sendReview('/api/review/' + id + '/unidentify', { method: 'POST' })) {
      removeMismatchCard(id);
      if (window.showToast) showToast('Dismissed', 'success');
    }
  };

  window.doMismatchReassignLabel = async id => {
    const label = document.getElementById('ra-mm-' + id)?.value.trim();
    if (!label) return;
    const ok = await sendReview('/api/review/' + id + '/reassign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (ok) { addFaceLabel(label); removeMismatchCard(id); }
  };

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
  // Keyboard navigation — ↑/↓ move focus between cards in the active tab
  // ---------------------------------------------------------------------------
  document.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement?.isContentEditable) return;
    e.preventDefault();
    const cards = getActivePanelCards();
    if (!cards.length) return;
    const idx = focusedCard ? cards.indexOf(focusedCard) : -1;
    if (e.key === 'ArrowDown') {
      setFocusedCard(idx < cards.length - 1 ? cards[idx + 1] : cards[Math.max(0, idx)]);
    } else {
      setFocusedCard(idx > 0 ? cards[idx - 1] : cards[0]);
    }
  });

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  const sgLoader = makeLoader(suggestedList, true,  selSg);
  const nmLoader = makeLoader(nomatchList,   false, selNm);

  const _initTab = sessionStorage.getItem('argus_nav_tab');
  const _initScroll = _initTab ? parseInt(sessionStorage.getItem('argus_nav_scroll') || '0', 10) : null;
  if (_initTab) {
    sessionStorage.removeItem('argus_nav_tab');
    sessionStorage.removeItem('argus_nav_scroll');
    switchTab(_initTab, false);
  }

  fetchTabCounts();
  sgLoader.loadPage();
  nmLoader.loadPage();
  if (_initScroll != null) {
    loadMismatches().then(() => window.scrollTo({ top: _initScroll, behavior: 'instant' }));
  } else {
    loadMismatches();
  }

  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    const savedTab = sessionStorage.getItem('argus_nav_tab');
    const savedScroll = savedTab ? parseInt(sessionStorage.getItem('argus_nav_scroll') || '0', 10) : null;
    if (savedTab) {
      sessionStorage.removeItem('argus_nav_tab');
      sessionStorage.removeItem('argus_nav_scroll');
      switchTab(savedTab, false);
    }
    fetchTabCounts();
    sgLoader.reset();
    nmLoader.reset();
    if (savedScroll != null) {
      loadMismatches().then(() => window.scrollTo({ top: savedScroll, behavior: 'instant' }));
    } else {
      loadMismatches();
    }
  });
})();
