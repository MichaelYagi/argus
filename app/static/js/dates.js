'use strict';

const _TZ     = window.USER_TZ     || Intl.DateTimeFormat().resolvedOptions().timeZone;
const _LOCALE = window.USER_LOCALE || navigator.language || 'en-US';

const _FMT = new Intl.DateTimeFormat(_LOCALE, {
  year: 'numeric', month: 'short', day: 'numeric',
  hour: '2-digit', minute: '2-digit',
  timeZone: _TZ,
});

window.formatDate = str => {
  if (!str || str === '—') return '—';
  try {
    // SQLite stores datetimes as "YYYY-MM-DD HH:MM:SS" (no T, no Z) — treat as UTC
    const iso = str.includes('T') ? str : str.replace(' ', 'T') + 'Z';
    return _FMT.format(new Date(iso));
  } catch {
    return str;
  }
};

// Auto-format any <time class="fmt-date" datetime="..."> on page load
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('time.fmt-date[datetime]').forEach(el => {
    el.textContent = formatDate(el.getAttribute('datetime'));
  });
});
