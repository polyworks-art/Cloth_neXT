(() => {
  'use strict';

  const banner = document.querySelector('[data-release-banner]');
  if (!banner) return;

  const releaseAt = new Date(banner.getAttribute('data-release-at') || '2026-07-30T18:00:00+02:00');
  const hoursNode = banner.querySelector('[data-countdown-hours]');
  const minutesNode = banner.querySelector('[data-countdown-minutes]');
  const secondsNode = banner.querySelector('[data-countdown-seconds]');
  const titleNode = banner.querySelector('[data-release-title]');
  const copyNode = banner.querySelector('[data-release-copy]');
  const actionNode = banner.querySelector('[data-release-action] span');
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
    if (copyNode) copyNode.textContent = 'Update now for the corrected animated-collider timeline, denser motion sampling and faster, more stable production bakes.';
    if (actionNode) actionNode.textContent = 'Get Beta 2.2.0';
    if (countdownNode) countdownNode.setAttribute('aria-label', 'Beta 2.2.0 is live');
    if (timerId) window.clearInterval(timerId);
  };

  const updateCountdown = () => {
    const remaining = releaseAt.getTime() - Date.now();
    if (remaining <= 0) {
      markLive();
      return;
    }

    const totalSeconds = Math.floor(remaining / 1000);
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