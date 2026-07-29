(() => {
  'use strict';

  const banner = document.querySelector('[data-release-banner]');
  if (!banner) return;

  // Keep the release notice visible inside the narrow Superhive iframe while
  // preserving its place in document flow for the fixed site header below it.
  banner.style.position = 'sticky';
  banner.style.top = '0';

  const releaseAtRaw = banner.getAttribute('data-release-at');
  const releaseAt = new Date(releaseAtRaw || '2026-07-29T21:00:00+02:00');
  const hoursNode = banner.querySelector('[data-countdown-hours]');
  const minutesNode = banner.querySelector('[data-countdown-minutes]');
  const secondsNode = banner.querySelector('[data-countdown-seconds]');
  const titleNode = banner.querySelector('[data-release-title]');
  const copyNode = banner.querySelector('[data-release-copy]');
  const actionNode = banner.querySelector('[data-release-action]');
  const actionLabelNode = actionNode?.querySelector('span');
  const countdownNode = banner.querySelector('[data-countdown]');

  if (Number.isNaN(releaseAt.getTime())) return;

  const pad = (value) => String(Math.max(0, value)).padStart(2, '0');
  let timerId = 0;

  const markLive = () => {
    banner.classList.add('is-live');
    if (hoursNode) hoursNode.textContent = '00';
    if (minutesNode) minutesNode.textContent = '00';
    if (secondsNode) secondsNode.textContent = '00';
    if (titleNode) titleNode.textContent = 'Beta 2.2.0 is live';
    if (copyNode) copyNode.textContent = 'Update now for the corrected animated-collider timeline, denser motion sampling and a faster, more stable production bake.';
    if (actionLabelNode) actionLabelNode.textContent = 'View Beta 2.2.0';
    if (countdownNode) countdownNode.setAttribute('aria-label', 'Beta 2.2.0 is live');
    if (timerId) window.clearInterval(timerId);
  };

  const updateCountdown = () => {
    const remainingMs = releaseAt.getTime() - Date.now();
    if (remainingMs <= 0) {
      markLive();
      return;
    }

    const totalSeconds = Math.floor(remainingMs / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (hoursNode) hoursNode.textContent = pad(hours);
    if (minutesNode) minutesNode.textContent = pad(minutes);
    if (secondsNode) secondsNode.textContent = pad(seconds);
    if (countdownNode) countdownNode.setAttribute('aria-label', `${hours} hours, ${minutes} minutes and ${seconds} seconds until Beta 2.2.0`);
  };

  updateCountdown();
  timerId = window.setInterval(updateCountdown, 1000);
})();
