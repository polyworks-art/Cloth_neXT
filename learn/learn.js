(() => {
  'use strict';

  const cards = [...document.querySelectorAll('.tutorial-card')];
  const search = document.querySelector('[data-library-search]');
  const sort = document.querySelector('[data-library-sort]');
  const count = document.querySelector('[data-result-count]');
  const empty = document.querySelector('[data-empty-state]');
  const clear = document.querySelector('[data-clear-filters]');
  const filters = [...document.querySelectorAll('[data-filter]')];
  const grid = document.querySelector('[data-tutorial-grid]');
  const pathLinks = [...document.querySelectorAll('[data-path-filter]')];

  const normalize = (value) => (value || '').toLowerCase().trim();

  const selected = (name) => filters
    .filter((input) => input.dataset.filter === name && input.checked)
    .map((input) => input.value);

  const matchesAnyToken = (source, requested) => {
    if (!requested.length) return true;
    const tokens = normalize(source).split(/\s+/).filter(Boolean);
    return requested.some((value) => tokens.includes(value));
  };

  const visibleCards = () => cards.filter((card) => !card.hidden);

  function applyFilters() {
    const query = normalize(search?.value);
    const levels = selected('level');
    const topics = selected('topic');

    cards.forEach((card) => {
      const haystack = normalize([
        card.dataset.title,
        card.dataset.level,
        card.dataset.topic,
        card.textContent
      ].join(' '));
      const matchesSearch = !query || query.split(/\s+/).every((token) => haystack.includes(token));
      const matchesLevel = !levels.length || levels.includes(card.dataset.level);
      const matchesTopic = matchesAnyToken(card.dataset.topic, topics);
      card.hidden = !(matchesSearch && matchesLevel && matchesTopic);
    });

    applySort();
    const shown = visibleCards().length;
    if (count) count.textContent = `${shown} tutorial${shown === 1 ? '' : 's'}`;
    if (empty) empty.hidden = shown !== 0;
  }

  function levelRank(level) {
    return { beginner: 0, intermediate: 1, advanced: 2 }[level] ?? 9;
  }

  function applySort() {
    if (!grid) return;
    const mode = sort?.value || 'featured';
    const ordered = [...cards];

    if (mode === 'beginner') {
      ordered.sort((a, b) => levelRank(a.dataset.level) - levelRank(b.dataset.level));
    } else if (mode === 'advanced') {
      ordered.sort((a, b) => levelRank(b.dataset.level) - levelRank(a.dataset.level));
    } else if (mode === 'title') {
      ordered.sort((a, b) => (a.dataset.title || '').localeCompare(b.dataset.title || ''));
    }

    ordered.forEach((card) => grid.appendChild(card));
  }

  search?.addEventListener('input', applyFilters);
  sort?.addEventListener('change', applyFilters);
  filters.forEach((input) => input.addEventListener('change', applyFilters));

  clear?.addEventListener('click', () => {
    filters.forEach((input) => { input.checked = false; });
    if (search) search.value = '';
    if (sort) sort.value = 'featured';
    applyFilters();
    search?.focus();
  });

  pathLinks.forEach((link) => {
    link.addEventListener('click', () => {
      const path = link.dataset.pathFilter;
      filters.forEach((input) => { input.checked = false; });
      if (search) search.value = '';
      cards.forEach((card) => {
        const paths = normalize(card.dataset.path).split(/\s+/);
        card.hidden = !paths.includes(path);
      });
      applySort();
      const shown = visibleCards().length;
      if (count) count.textContent = `${shown} tutorial${shown === 1 ? '' : 's'} in this path`;
      if (empty) empty.hidden = shown !== 0;
    });
  });

  document.querySelectorAll('a[href^="#tutorial-"]').forEach((link) => {
    link.addEventListener('click', () => {
      const target = document.querySelector(link.getAttribute('href'));
      if (!target) return;
      target.classList.remove('chapter-flash');
      requestAnimationFrame(() => {
        target.classList.add('chapter-flash');
        window.setTimeout(() => target.classList.remove('chapter-flash'), 900);
      });
    });
  });

  applyFilters();
})();
