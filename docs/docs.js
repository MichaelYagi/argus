const DOCS_VERSION = 'v0.1.0-beta.1';

(function stampVersion() {
  let footer = document.querySelector('body > footer');
  if (!footer) {
    footer = document.createElement('footer');
    document.body.append(footer);
  } else if (footer.childNodes.length) {
    const content = document.createElement('span');
    content.append(...footer.childNodes);
    footer.append(content);
  }
  const version = document.createElement('span');
  version.className = 'docs-version';
  version.textContent = DOCS_VERSION;
  footer.append(version);
})();

document.querySelectorAll('.docs-nav [aria-current="page"]').forEach((el) => {
  el.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'instant' });
});

(function buildToc() {
  if ('noToc' in document.body.dataset) return;
  const headings = document.querySelectorAll('h2, h3');
  if (headings.length < 2) return;

  const usedIds = new Set(Array.from(document.querySelectorAll('[id]'), (el) => el.id));
  const slugify = (text) =>
    text
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');

  const list = document.createElement('ul');
  let lastTopLi = null;

  headings.forEach((h) => {
    if (!h.id) {
      const base = slugify(h.textContent || '') || 'section';
      let id = base;
      let n = 2;
      while (usedIds.has(id)) id = `${base}-${n++}`;
      h.id = id;
      usedIds.add(id);
    }

    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = `#${h.id}`;
    a.textContent = h.textContent;
    li.append(a);

    if (h.tagName === 'H2' || !lastTopLi) {
      list.append(li);
      lastTopLi = li;
    } else {
      let sub = lastTopLi.querySelector(':scope > ul');
      if (!sub) {
        sub = document.createElement('ul');
        lastTopLi.append(sub);
      }
      sub.append(li);
    }
  });

  const details = document.createElement('details');
  details.className = 'toc';
  details.open = true;
  const summary = document.createElement('summary');
  summary.textContent = 'On this page';
  details.append(summary, list);

  const nav = document.createElement('nav');
  nav.setAttribute('aria-label', 'On this page');
  nav.append(details);

  headings[0].before(nav);
})();

(function scrollToTopButton() {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'scroll-top-btn';
  button.textContent = '↑ Top';
  button.setAttribute('aria-label', 'Scroll to top');
  document.body.append(button);

  const SHOW_AFTER_PX = 400;
  const toggle = () => {
    button.classList.toggle('is-visible', window.scrollY > SHOW_AFTER_PX);
  };
  document.addEventListener('scroll', toggle, { passive: true });
  toggle();

  button.addEventListener('click', () => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
  });
})();
