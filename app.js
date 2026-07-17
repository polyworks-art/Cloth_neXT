const toast = document.querySelector('.toast');
let toastTimer;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 1800);
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    showToast('Repository URL copied');
  } catch (_error) {
    const input = document.createElement('textarea');
    input.value = value;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    input.remove();
    showToast('Repository URL copied');
  }
}

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', () => {
    const target = document.getElementById(button.dataset.copy);
    if (target) copyText(target.textContent.trim());
  });
});

document.querySelectorAll('[data-copy-value]').forEach((button) => {
  button.addEventListener('click', () => copyText(button.dataset.copyValue));
});

async function loadChannel(channel) {
  const card = document.querySelector(`[data-channel="${channel}"]`);
  if (!card) return;
  const output = card.querySelector('[data-version]');
  try {
    const response = await fetch(`${channel}/index.json`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const release = Array.isArray(payload.data)
      ? payload.data.find((item) => item.id === 'cloth_next')
      : null;
    output.textContent = release?.version ? `v${release.version}` : 'Unavailable';
  } catch (_error) {
    output.textContent = 'Unavailable';
  }
}

['stable', 'beta', 'dev'].forEach(loadChannel);
