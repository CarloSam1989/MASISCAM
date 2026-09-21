(() => {
  const modal = document.getElementById('masiscamFormModal');
  if (!modal) return;
  const body = modal.querySelector('[data-modal-body]');
  const title = modal.querySelector('#masiscamFormModalTitle');
  const status = modal.querySelector('.masiscam-form-modal__status');
  let trigger;
  let loading = false;

  const showStatus = (message, error = false) => {
    status.textContent = message;
    status.className = `masiscam-form-modal__status alert ${error ? 'alert-danger' : 'alert-info'}`;
    status.hidden = !message;
  };
  const enhance = container => {
    container.querySelectorAll('input:not([type=checkbox]):not([type=file]), select, textarea').forEach(element => element.classList.add('form-control'));
    container.querySelectorAll('input[type=checkbox]').forEach(element => element.classList.add('form-check-input'));
  };
  const extractForm = html => {
    const document = new DOMParser().parseFromString(html, 'text/html');
    return document.querySelector('form');
  };
  const loadForm = async url => {
    const response = await fetch(url, {headers: {Accept: 'text/html', 'X-Requested-With': 'XMLHttpRequest'}});
    if (!response.ok) throw new Error('load');
    const form = extractForm(await response.text());
    if (!form) throw new Error('form');
    if (!form.getAttribute('action')) form.action = url;
    body.replaceChildren(form);
    enhance(body);
    form.addEventListener('submit', submitForm);
  };
  const submitForm = async event => {
    event.preventDefault();
    if (loading) return;
    const form = event.currentTarget;
    loading = true;
    showStatus('');
    const button = form.querySelector('[type="submit"]');
    if (button) { button.disabled = true; button.dataset.label = button.textContent; button.textContent = 'Guardando...'; }
    try {
      const response = await fetch(form.action || window.location.href, {
        method: 'POST', body: new FormData(form),
        headers: {Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        const nextForm = extractForm(data.html || '');
        if (nextForm) {
          body.replaceChildren(nextForm);
          enhance(body);
          nextForm.addEventListener('submit', submitForm);
        }
        showStatus(data.message || 'Revisa los datos del formulario.', true);
        return;
      }
      modal.close();
      showStatus('');
      const scroll = window.scrollY;
      sessionStorage.setItem('masiscam-modal-scroll', String(scroll));
      window.location.reload();
    } catch (_) {
      showStatus('No se pudo cargar o guardar el formulario. Inténtalo de nuevo.', true);
    } finally {
      loading = false;
      if (button) { button.disabled = false; button.textContent = button.dataset.label || 'Guardar'; }
    }
  };
  document.addEventListener('click', async event => {
    const link = event.target.closest('[data-modal-form], [data-bs-target], a[href*="/proyectos/nuevo/"]');
    if (!link) return;
    let url = link.href;
    if (!url && link.dataset.bsTarget) {
      const inlineForm = document.querySelector(`${link.dataset.bsTarget} form`);
      url = inlineForm && inlineForm.action;
    }
    if (!url) return;
    event.preventDefault();
    if (loading) return;
    trigger = link;
    title.textContent = link.dataset.modalTitle || link.textContent.trim() || 'Nuevo registro';
    body.replaceChildren();
    showStatus('Cargando...');
    modal.showModal();
    try {
      await loadForm(url);
      showStatus('');
    } catch (_) {
      showStatus('No se pudo cargar el formulario.', true);
    }
  });
  modal.querySelector('[data-modal-close]').addEventListener('click', () => modal.close());
  modal.addEventListener('click', event => { if (event.target.closest('[data-modal-close]') && !loading) modal.close(); });
  modal.addEventListener('click', event => { if (event.target === modal && !loading) modal.close(); });
  modal.addEventListener('close', () => { body.replaceChildren(); showStatus(''); if (trigger) trigger.focus(); });
  const scroll = sessionStorage.getItem('masiscam-modal-scroll');
  if (scroll) { sessionStorage.removeItem('masiscam-modal-scroll'); window.scrollTo(0, Number(scroll)); }
})();
