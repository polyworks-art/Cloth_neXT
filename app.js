(() => {
  'use strict';

  const mediaBase = 'https://raw.githubusercontent.com/polyworks-art/Cloth_neXT/a62bf0d55119a28ab9f7c644c244c5edcfb8126d/assets/media/';
  document.querySelectorAll('img[src^="assets/media/"]').forEach((image) => {
    const filename = image.getAttribute('src').split('/').pop();
    image.src = `${mediaBase}${filename}`;
  });

  document.querySelectorAll('[data-year]').forEach((node) => {
    node.textContent = new Date().getFullYear();
  });

  const header = document.querySelector('[data-header]');
  const onScroll = () => header?.classList.toggle('scrolled', window.scrollY > 18);
  onScroll();
  window.addEventListener('scroll', onScroll, { passive: true });

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const reveals = document.querySelectorAll('.reveal');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    reveals.forEach((node) => node.classList.add('revealed'));
    return;
  }

  const observer = new IntersectionObserver((entries, instance) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('revealed');
      instance.unobserve(entry.target);
    });
  }, { threshold: .1, rootMargin: '0px 0px -7% 0px' });

  reveals.forEach((node) => observer.observe(node));
})();
