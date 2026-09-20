// Solo los controles Bootstrap usados por los formularios existentes.
document.addEventListener('click', event => {
  const button = event.target.closest('[data-bs-toggle="collapse"]');
  if (!button) return;
  const selector = button.getAttribute('data-bs-target');
  if (!selector || !selector.startsWith('#')) return;
  const target = document.getElementById(selector.slice(1));
  if (target) button.setAttribute('aria-expanded', String(target.classList.toggle('show')));
});
