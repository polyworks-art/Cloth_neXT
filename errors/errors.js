const state = { errors: [], stage: 'all', query: '' };
const groupsRoot = document.querySelector('.error-groups');
const filtersRoot = document.querySelector('.stage-filters');
const search = document.querySelector('#error-search');
const resultCount = document.querySelector('#result-count');
const emptyState = document.querySelector('.empty-state');
const expandButton = document.querySelector('.expand-all');
const toast = document.querySelector('.toast');
let toastTimer;

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 1700);
}

function matches(error) {
  if (state.stage !== 'all' && error.stage !== state.stage) return false;
  if (!state.query) return true;
  const haystack = `${error.code} ${error.stage} ${error.cause} ${error.action}`.toLowerCase();
  return state.query.split(/\s+/).every((part) => haystack.includes(part));
}

function errorCard(error) {
  const details = document.createElement('details');
  details.className = 'error-item';
  details.id = error.code;
  details.innerHTML = `
    <summary>
      <span class="error-code">${error.code}</span>
      <span class="error-cause">${error.cause}</span>
      <span class="error-toggle" aria-hidden="true">+</span>
    </summary>
    <div class="error-detail">
      <span class="action-label">First action</span>
      <div class="error-action">${error.action}</div>
      <button class="copy-link" type="button">Copy link</button>
    </div>`;
  details.querySelector('.copy-link').addEventListener('click', async () => {
    const url = `${location.origin}${location.pathname}#${error.code}`;
    try { await navigator.clipboard.writeText(url); }
    catch (_error) {
      const field = document.createElement('textarea');
      field.value = url; document.body.appendChild(field); field.select();
      document.execCommand('copy'); field.remove();
    }
    showToast(`${error.code} link copied`);
  });
  return details;
}

function render() {
  groupsRoot.replaceChildren();
  const visible = state.errors.filter(matches);
  resultCount.textContent = visible.length;
  const byStage = new Map();
  visible.forEach((error) => {
    if (!byStage.has(error.stage)) byStage.set(error.stage, []);
    byStage.get(error.stage).push(error);
  });
  byStage.forEach((errors, stage) => {
    const section = document.createElement('section');
    section.className = 'error-group';
    section.dataset.stage = slug(stage);
    section.innerHTML = `<div class="error-group-heading"><h3>${stage}</h3><span>${errors.length} ${errors.length === 1 ? 'code' : 'codes'}</span></div>`;
    const items = document.createElement('div');
    items.className = 'error-items';
    errors.forEach((error) => items.appendChild(errorCard(error)));
    section.appendChild(items);
    groupsRoot.appendChild(section);
  });
  emptyState.hidden = visible.length !== 0;
  groupsRoot.hidden = visible.length === 0;
  openTarget();
}

function openTarget() {
  const code = decodeURIComponent(location.hash.slice(1)).toUpperCase();
  if (!/^CNX-E\d{3}$/.test(code)) return;
  const target = document.getElementById(code);
  if (!target) return;
  target.open = true;
  target.classList.add('targeted');
  requestAnimationFrame(() => target.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  setTimeout(() => target.classList.remove('targeted'), 2400);
}

function buildFilters(stages) {
  stages.forEach((stage) => {
    const button = document.createElement('button');
    button.type = 'button'; button.dataset.stage = stage; button.textContent = stage;
    filtersRoot.appendChild(button);
  });
  filtersRoot.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-stage]');
    if (!button) return;
    state.stage = button.dataset.stage;
    filtersRoot.querySelectorAll('button').forEach((item) => item.classList.toggle('active', item === button));
    render();
  });
}

search.addEventListener('input', () => { state.query = search.value.trim().toLowerCase(); render(); });
search.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { search.value = ''; state.query = ''; render(); search.blur(); }
});
emptyState.querySelector('button').addEventListener('click', () => {
  search.value = ''; state.query = ''; state.stage = 'all';
  filtersRoot.querySelectorAll('button').forEach((item) => item.classList.toggle('active', item.dataset.stage === 'all'));
  render(); search.focus();
});
expandButton.addEventListener('click', () => {
  const items = [...groupsRoot.querySelectorAll('details')];
  const expand = items.some((item) => !item.open);
  items.forEach((item) => { item.open = expand; });
  expandButton.textContent = expand ? 'Collapse all' : 'Expand all';
});
window.addEventListener('hashchange', openTarget);

fetch('errors.json', { cache: 'no-store' })
  .then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
  .then((payload) => {
    state.errors = payload.errors;
    const stages = [...new Set(state.errors.map((error) => error.stage))];
    document.querySelector('#code-count').textContent = state.errors.length;
    document.querySelector('#stage-count').textContent = stages.length;
    buildFilters(stages); render();
    const requested = new URLSearchParams(location.search).get('code');
    if (requested && !location.hash) location.hash = requested.toUpperCase();
  })
  .catch(() => {
    groupsRoot.innerHTML = '<p class="error-action">The error directory could not be loaded. Open the source documentation on GitHub and retry later.</p>';
    resultCount.textContent = '0';
  });
