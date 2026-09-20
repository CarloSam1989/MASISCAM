(() => {
  const modal = document.getElementById('registro-modal');
  if (!modal) return;
  const submitLabel = modal.querySelector('[type="submit"]').innerHTML;
  const open = document.getElementById('registro-abrir');
  open.addEventListener('click', () => modal.showModal());
  modal.querySelectorAll('[data-registro-cerrar]').forEach(button => {
    button.addEventListener('click', () => modal.close());
  });
  document.getElementById('registro-form').addEventListener('submit', event => {
    const button = event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    button.textContent = 'Guardando…';
  });
  window.addEventListener('pageshow', () => {
    const button = modal.querySelector('[type="submit"]');
    button.disabled = false;
    button.innerHTML = submitLabel;
  });
  if (modal.dataset.open === 'true') modal.showModal();
})();
