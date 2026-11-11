// Retain links produced before the introduction page was added.
const sharedView = new URLSearchParams(location.hash.slice(1));
if (['run', 'scope', 'round', 'node'].some(key => sharedView.has(key))) {
  location.replace(`sandbox.html${location.hash}`);
}

const motionPreference = matchMedia('(prefers-reduced-motion: reduce)');
const cards = document.querySelectorAll('[data-reveal]');
if (!motionPreference.matches && 'IntersectionObserver' in window) {
  document.documentElement.classList.add('js');
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-revealed');
      observer.unobserve(entry.target);
    });
  }, {threshold: 0.12, rootMargin: '0px 0px -8% 0px'});

  cards.forEach(card => {
    card.classList.add('reveal-ready');
    observer.observe(card);
  });
  motionPreference.addEventListener('change', event => {
    if (!event.matches) return;
    observer.disconnect();
    cards.forEach(card => card.classList.add('is-revealed'));
  });
}
