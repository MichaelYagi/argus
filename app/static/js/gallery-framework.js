// Justified infinite-scroll gallery with history.state scroll restoration.
//
// Usage:
//   const gallery = ArgusGallery({ container, emptyEl, loadingEl, stateKey,
//     ar, buildItem, findEl, fetchPage, [preInit], [extraState], [restoreExtra] });
//   window.resetGallery = gallery.reset;   // expose for filter bars etc.
//
// opts:
//   container   — DOM element for the gallery grid
//   emptyEl     — DOM element shown when no items exist (or null)
//   loadingEl   — DOM element shown while loading (or null)
//   stateKey    — string key for history.state, e.g. '_argus_images'
//   ar(item)    — returns aspect ratio for one item (number)
//   buildItem(item, ctx) — returns the .g-item DOM element (no width/height needed;
//                  framework sets those). ctx = { saveState(clickedId), removeItem(pred) }
//   findEl(container, clickedId) — returns the element to scrollIntoView on restore
//   fetchPage(cursor) — async, returns { items, nextCursor, hasMore }
//   preInit()   — optional async fn, called before first fetchPage on a fresh load
//   extraState() — optional, returns extra fields merged into the saved history state
//   restoreExtra(state) — optional, restores those extra fields on back-navigation
//   filterRestored(items) — optional, filters the restored items list before render
//                  (skipped on bfcache restores; use a pageshow handler for that)

window.ArgusGallery = function ArgusGallery(opts) {
  const { container, emptyEl, loadingEl, stateKey } = opts;
  const GAP = 4, TARGET_H = 200;

  let items = [], cursor = null, hasMore = true, loading = false, sentinelVisible = false;
  let pending = [], tailEl = null;

  function saveState(clickedId) {
    try {
      history.replaceState({
        [stateKey]: {
          clickedId, cursor, hasMore, items,
          scrollY: window.scrollY,
          ...(opts.extraState ? opts.extraState() : {}),
        },
      }, '');
    } catch (_) {}
  }

  function removeItem(pred) {
    const idx = items.findIndex(pred);
    if (idx !== -1) items.splice(idx, 1);
    relayout();
    if (items.length === 0 && !hasMore && emptyEl) emptyEl.hidden = false;
  }

  const ctx = { saveState, removeItem };

  function buildRow(rowItems, height) {
    const rowEl = document.createElement('div');
    rowEl.className = 'g-row';
    rowItems.forEach(item => {
      const el = opts.buildItem(item, ctx);
      el.style.width  = Math.floor((item._ar || 1) * height) + 'px';
      el.style.height = Math.floor(height) + 'px';
      rowEl.appendChild(el);
    });
    return rowEl;
  }

  function flushRows(work) {
    const W = container.clientWidth;
    let cur = [], sumAR = 0;
    work.forEach(raw => {
      const item = { ...raw, _ar: opts.ar(raw) };
      cur.push(item);
      sumAR += item._ar;
      if (sumAR * TARGET_H + GAP * (cur.length - 1) >= W) {
        container.appendChild(buildRow(cur, (W - GAP * (cur.length - 1)) / sumAR));
        cur = []; sumAR = 0;
      }
    });
    return cur;
  }

  function appendItems(newItems) {
    if (tailEl) { tailEl.remove(); tailEl = null; }
    pending = flushRows(pending.concat(newItems));
    if (pending.length) {
      tailEl = buildRow(pending, TARGET_H);
      container.appendChild(tailEl);
    }
  }

  function relayout() {
    const scrollY = window.scrollY;
    container.innerHTML = '';
    pending = []; tailEl = null;
    appendItems(items);
    requestAnimationFrame(() => { if (window.scrollY !== scrollY) window.scrollTo(0, scrollY); });
  }

  function removeItems(preds) {
    preds.forEach(pred => {
      const idx = items.findIndex(pred);
      if (idx !== -1) items.splice(idx, 1);
    });
    relayout();
    if (items.length === 0 && !hasMore && emptyEl) emptyEl.hidden = false;
  }

  async function loadPage() {
    if (loading || !hasMore) return;
    loading = true;
    if (loadingEl) loadingEl.hidden = false;

    let data;
    try {
      data = await opts.fetchPage(cursor);
    } catch (_) {
      loading = false;
      if (loadingEl) loadingEl.hidden = true;
      return;
    }

    hasMore = data.hasMore;
    cursor  = data.nextCursor;

    if (!items.length && !data.items.length) {
      if (emptyEl) emptyEl.hidden = false;
      if (loadingEl) loadingEl.hidden = true;
      loading = false;
      return;
    }

    items.push(...data.items);
    appendItems(data.items);

    loading = false;
    if (loadingEl) loadingEl.hidden = true;
    if (!hasMore) sentinel.remove();
    else if (sentinelVisible) loadPage();
  }

  function reset() {
    cursor = null; hasMore = true; loading = false;
    items = []; pending = [];
    if (tailEl) { tailEl.remove(); tailEl = null; }
    container.innerHTML = '';
    if (loadingEl) loadingEl.hidden = false;
    if (emptyEl) emptyEl.hidden = true;
    if (!sentinel.parentNode) container.after(sentinel);
    loadPage();
  }

  const sentinel = document.createElement('div');
  sentinel.style.height = '1px';
  container.after(sentinel);

  function observe() {
    new IntersectionObserver(([e]) => {
      sentinelVisible = e.isIntersecting;
      if (e.isIntersecting) loadPage();
    }, { rootMargin: '300px' }).observe(sentinel);
  }

  const navType    = performance.getEntriesByType('navigation')[0]?.type;
  const savedState = navType === 'back_forward' ? (history.state?.[stateKey] ?? null) : null;

  if (savedState) {
    cursor  = savedState.cursor  ?? null;
    hasMore = savedState.hasMore ?? true;
    if (opts.restoreExtra) opts.restoreExtra(savedState);
    requestAnimationFrame(() => {
      const restoredItems = opts.filterRestored ? opts.filterRestored(savedState.items) : savedState.items;
      items.push(...restoredItems);
      appendItems(restoredItems);
      if (loadingEl) loadingEl.hidden = true;
      if (!hasMore) sentinel.remove();
      observe();
      requestAnimationFrame(() => {
        const lastId = sessionStorage.getItem('argus_last_viewed');
        sessionStorage.removeItem('argus_last_viewed');
        // If the user navigated via Prev/Next on the tag page, last viewed differs
        // from the originally-clicked item — scroll to that element instead of
        // restoring scrollY (which points to the original item's position).
        if (lastId && opts.findEl && String(lastId) !== String(savedState.clickedId)) {
          const el = opts.findEl(container, parseInt(lastId, 10));
          if (el) {
            const r = el.getBoundingClientRect();
            const top = r.top + window.scrollY - Math.max(0, (window.innerHeight - r.height) / 2);
            window.scrollTo(0, Math.max(0, top));
            return;
          }
        }
        if (savedState.scrollY != null) {
          window.scrollTo(0, savedState.scrollY);
        } else if (savedState.clickedId != null && opts.findEl) {
          const el = opts.findEl(container, savedState.clickedId);
          if (el) el.scrollIntoView({ block: 'center' });
        }
      });
    });
  } else {
    (async () => {
      if (opts.preInit) await opts.preInit();
      loadPage();
    })();
    observe();
  }

  window.addEventListener('pageshow', e => {
    if (!e.persisted || !opts.findEl) return;
    const lastId = sessionStorage.getItem('argus_last_viewed');
    if (!lastId) return;
    sessionStorage.removeItem('argus_last_viewed');
    const clickedId = history.state?.[stateKey]?.clickedId;
    if (String(lastId) === String(clickedId)) return; // bfcache scroll already correct
    const el = opts.findEl(container, parseInt(lastId, 10));
    if (el) {
      const r = el.getBoundingClientRect();
      const top = r.top + window.scrollY - Math.max(0, (window.innerHeight - r.height) / 2);
      window.scrollTo(0, Math.max(0, top));
    }
  });

  let resizeT, lastW = 0;
  window.addEventListener('resize', () => {
    if (!items.length) return;
    const w = container.clientWidth;
    if (w === lastW) return;
    lastW = w;
    clearTimeout(resizeT);
    resizeT = setTimeout(relayout, 150);
  });

  return { reset, removeItem, removeItems, items: () => items };
};
