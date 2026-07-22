const channels = ['stable', 'beta', 'dev'];

async function loadChannelVersion(channel) {
  const output = document.querySelector(`[data-channel-version="${channel}"]`);
  if (!output) return;

  try {
    const response = await fetch(`../${channel}/index.json`, {
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const release = Array.isArray(payload.data)
      ? payload.data.find((item) => item.id === 'cloth_next')
      : null;
    if (typeof release?.version === 'string' && release.version) {
      output.textContent = release.version;
    }
  } catch (_error) {
    // Retain the server-rendered version if a request is temporarily unavailable.
  }
}

channels.forEach(loadChannelVersion);
