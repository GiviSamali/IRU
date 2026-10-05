/* Reuse the actual header controls, including their existing event handlers. */
(() => {
  const toggle = document.getElementById('mobileHeaderToggle');
  const panel = document.getElementById('headerActions');
  const mobile = matchMedia('(max-width: 768px)');
  function close(restoreFocus = false) {
    panel.classList.remove('mobile-open');
    toggle.setAttribute('aria-expanded', 'false');
    panel.querySelectorAll('.show, .usage-badge.open').forEach(el => el.classList.remove('show', 'open'));
    panel.querySelectorAll('[aria-expanded="true"]:not(#memoryBadge):not(#settingsToggle)').forEach(el => el.setAttribute('aria-expanded', 'false'));
    if (restoreFocus) toggle.focus();
  }
  toggle.addEventListener('click', () => {
    if (panel.classList.contains('mobile-open')) return close();
    panel.classList.add('mobile-open');
    toggle.setAttribute('aria-expanded', 'true');
  });
  document.addEventListener('click', event => {
    if (mobile.matches && !panel.contains(event.target) && !toggle.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && panel.classList.contains('mobile-open')) close(true);
  });
  panel.addEventListener('click', event => {
    if (mobile.matches && event.target.closest('a, #explorerToggle, #devModeToggle, #btnAdmin, #devicePassportToggle, #memoryBadge, #settingsToggle')) close();
  });
  panel.addEventListener('focusout', () => {
    setTimeout(() => {
      if (mobile.matches && !panel.contains(document.activeElement) && document.activeElement !== toggle) close();
    }, 0);
  });
  mobile.addEventListener('change', () => close());
  ['memoryBadge', 'usageBadge', 'deviceBtn'].forEach(id => {
    const el = document.getElementById(id);
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.addEventListener('keydown', event => {
      if (event.target === el && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); el.click();
      }
    });
  });
})();
