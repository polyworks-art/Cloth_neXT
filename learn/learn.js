(() => {
  'use strict';

  const tocLinks = [...document.querySelectorAll('.learn-toc a[href^="#"]')];
  const sections = tocLinks
    .map((link) => document.querySelector(link.getAttribute('href')))
    .filter(Boolean);

  if ('IntersectionObserver' in window && sections.length) {
    const visible = new Map();
    const updateActive = () => {
      const candidates = [...visible.entries()]
        .filter(([, state]) => state)
        .map(([section]) => section)
        .sort((a, b) => Math.abs(a.getBoundingClientRect().top - 120) - Math.abs(b.getBoundingClientRect().top - 120));
      const active = candidates[0];
      if (!active) return;
      tocLinks.forEach((link) => {
        link.classList.toggle('is-active', link.getAttribute('href') === `#${active.id}`);
      });
    };

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => visible.set(entry.target, entry.isIntersecting));
      updateActive();
    }, {
      rootMargin: '-18% 0px -68% 0px',
      threshold: [0, .01]
    });

    sections.forEach((section) => observer.observe(section));
  }

  const search = document.querySelector('[data-learn-search]');
  const status = document.querySelector('[data-search-status]');
  if (!search) return;

  const items = [...document.querySelectorAll('.learn-topic, .role-card, .parameter-table tbody tr')];
  const sectionsWithSearchText = [...document.querySelectorAll('.searchable-section')];
  let emptyState = null;

  const normalize = (value) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  const searchText = (node) => normalize(`${node.dataset.searchText || ''} ${node.textContent || ''}`);

  const clearSearch = () => {
    items.forEach((item) => item.classList.remove('is-search-hidden', 'search-hit'));
    sectionsWithSearchText.forEach((section) => section.classList.remove('search-hit'));
    if (emptyState) emptyState.remove();
    emptyState = null;
    if (status) status.textContent = '';
  };

  const runSearch = () => {
    const query = normalize(search.value);
    if (!query) {
      clearSearch();
      return;
    }

    if (emptyState) emptyState.remove();
    emptyState = null;

    let matches = 0;
    items.forEach((item) => {
      const hit = searchText(item).includes(query);
      item.classList.toggle('is-search-hidden', !hit);
      item.classList.toggle('search-hit', hit);
      if (hit) matches += 1;
    });

    sectionsWithSearchText.forEach((section) => {
      const directHit = normalize(section.dataset.searchText).includes(query);
      section.classList.toggle('search-hit', directHit && !section.querySelector('.search-hit'));
      if (directHit && !section.querySelector('.search-hit')) matches += 1;
    });

    if (status) status.textContent = `${matches} ${matches === 1 ? 'match' : 'matches'}`;

    if (!matches) {
      emptyState = document.createElement('div');
      emptyState.className = 'no-search-results';
      emptyState.textContent = 'No direct match. Try a shorter term such as cloth, pinning, collider, shrink or cache.';
      const content = document.querySelector('.learn-content');
      content?.prepend(emptyState);
    }
  };

  search.addEventListener('input', runSearch);
  search.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    search.value = '';
    clearSearch();
    search.blur();
  });
})();
