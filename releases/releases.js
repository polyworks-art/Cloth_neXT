const CHANNELS = ['stable', 'beta', 'dev'];
const channelInfo = {
  stable: 'Production-ready releases.',
  beta: 'Release candidates and Stable builds.',
  dev: 'Latest experimental snapshots.'
};

function candidate(payload) {
  return (payload.data || []).find((entry) => entry.id === 'cloth_next');
}

function makeList(items) {
  const group = document.createElement('section');
  group.className = 'change-group';
  const heading = document.createElement('h4');
  heading.textContent = items.label;
  const list = document.createElement('ul');
  items.entries.forEach((text) => {
    const item = document.createElement('li');
    item.textContent = text;
    list.appendChild(item);
  });
  group.append(heading, list);
  return group;
}

function renderChannel(channel, entry) {
  const card = document.querySelector(`[data-channel="${channel}"]`);
  card.classList.remove('loading');
  card.querySelector('strong').textContent = entry.version;
  card.querySelector('p').textContent = channelInfo[channel];
  const link = document.createElement('a');
  link.href = `../${channel}/${entry.archive_url.replace(/^\.\//, '')}`;
  link.textContent = 'View package ↗';
  card.appendChild(link);
}

function renderNotes(current, catalog) {
  const root = document.querySelector('.release-notes');
  const versions = [];
  CHANNELS.forEach((channel) => {
    const version = current[channel].version;
    let item = versions.find((candidate) => candidate.version === version);
    if (!item) { item = { version, channels: [] }; versions.push(item); }
    item.channels.push(channel);
  });

  versions.forEach(({ version, channels }) => {
    const note = catalog.releases[version];
    const article = document.createElement('article');
    article.className = 'release-note';
    const meta = document.createElement('div');
    meta.className = 'release-meta';
    const badges = document.createElement('div');
    badges.className = 'channel-badges';
    channels.forEach((channel) => {
      const badge = document.createElement('span');
      badge.className = 'channel-badge'; badge.textContent = channel;
      badges.appendChild(badge);
    });
    const versionLabel = document.createElement('strong');
    versionLabel.className = 'version'; versionLabel.textContent = version;
    const date = document.createElement('time');
    date.textContent = note ? note.date : 'Current channel build';
    meta.append(badges, versionLabel, date);

    const body = document.createElement('div');
    body.className = 'release-body';
    const title = document.createElement('h3');
    title.textContent = note ? note.title : 'Release notes pending';
    const summary = document.createElement('p');
    summary.className = 'release-summary';
    summary.textContent = note ? note.summary : 'This build is published, but its curated notes have not reached the website yet.';
    body.append(title, summary);
    if (note) {
      const columns = document.createElement('div');
      columns.className = 'change-columns';
      note.sections.filter((section) => section.entries.length).forEach((section) => columns.appendChild(makeList(section)));
      body.appendChild(columns);
    }
    article.append(meta, body); root.appendChild(article);
  });
}

Promise.all([
  ...CHANNELS.map((channel) => fetch(`../${channel}/index.json`, { cache: 'no-store' }).then((response) => {
    if (!response.ok) throw new Error(`${channel}: HTTP ${response.status}`);
    return response.json();
  })),
  fetch('notes.json', { cache: 'no-store' }).then((response) => {
    if (!response.ok) throw new Error(`notes: HTTP ${response.status}`);
    return response.json();
  })
]).then(([stable, beta, dev, catalog]) => {
  const current = { stable: candidate(stable), beta: candidate(beta), dev: candidate(dev) };
  if (CHANNELS.some((channel) => !current[channel])) throw new Error('missing Cloth NeXt candidate');
  CHANNELS.forEach((channel) => renderChannel(channel, current[channel]));
  renderNotes(current, catalog);
}).catch(() => {
  document.querySelector('.channel-release-grid').hidden = true;
  document.querySelector('.notes-error').hidden = false;
});
