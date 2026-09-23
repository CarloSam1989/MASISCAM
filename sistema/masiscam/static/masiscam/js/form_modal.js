(() => {
  const modal = document.getElementById('masiscamFormModal');
  if (!modal) return;
  const body = modal.querySelector('[data-modal-body]');
  const footer = modal.querySelector('[data-modal-footer]');
  const title = modal.querySelector('#masiscamFormModalTitle');
  const status = modal.querySelector('[role="alert"]');
  let trigger, controller, saving = false;
  const showStatus = (message, error = false) => {
    status.textContent = message;
    status.className = `masiscam-form-modal__status alert ${error ? 'alert-danger' : 'alert-info'}`;
    status.hidden = !message;
  };
  const mount = (html, url) => {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const content = parsed.querySelector('main') || parsed.body;
    const form = content.querySelector('form');
    if (!form || parsed.querySelector('.login-page')) throw new Error('form');
    const initial = parsed.getElementById('masiscam-cliente-inicial');
    content.querySelectorAll('script').forEach(script => script.remove());
    body.dispatchEvent(new Event('masiscam:unmount'));
    body.replaceChildren(...content.childNodes);
    if (initial) body.append(initial);
    form.action = form.getAttribute('action') || url;
    form.id = form.id || 'masiscam-modal-form';
    const submit = form.querySelector('button:not([type]), button[type="submit"], input[type="submit"]');
    const actions = submit && submit.closest('.d-flex');
    footer.replaceChildren();
    if (actions) {
      actions.querySelectorAll('button:not([type]), [type="submit"]').forEach(button => button.setAttribute('form', form.id));
      actions.querySelectorAll('a.btn-outline-secondary').forEach(link => {
        const cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = link.className;
        cancel.textContent = link.textContent; cancel.dataset.modalClose = '';
        link.replaceWith(cancel);
      });
      footer.append(actions);
    }
    window.masiscamEnhanceForms(body);
    window.masiscamInitClienteSelector(body);
    form.addEventListener('submit', submitForm);
  };
  const submitForm = async event => {
    event.preventDefault();
    if (saving) return;
    const form = event.currentTarget;
    const url = form.action;
    const payload = new FormData(form);
    saving = true;
    const buttons = [...modal.querySelectorAll('button')];
    const states = buttons.map(button => button.disabled);
    buttons.forEach(button => { button.disabled = true; });
    showStatus('Guardando...');
    try {
      const response = await fetch(url, {method: 'POST', body: payload,
        headers: {Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest'}});
      const data = await response.json();
      if (!response.ok || !data.success) {
        if (data.html) mount(data.html, url);
        showStatus(data.message || 'Revisa los datos del formulario.', true);
        return;
      }
      window.location.reload();
    } catch (_) {
      showStatus('No se pudo guardar. Revisa la conexión e inténtalo de nuevo.', true);
    } finally {
      saving = false;
      buttons.forEach((button, index) => { button.disabled = states[index]; });
    }
  };
  document.addEventListener('click', async event => {
    const link = event.target.closest('a[data-modal-form]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (modal.open || saving) return;
    trigger = link;
    modal.classList.toggle('masiscam-form-modal--wide', link.dataset.modalSize === 'wide');
    title.textContent = link.dataset.modalTitle || link.textContent.trim();
    body.replaceChildren(); footer.replaceChildren();
    showStatus('Cargando...'); modal.showModal();
    const request = new AbortController();
    controller = request;
    try {
      const response = await fetch(link.href, {signal: request.signal,
        headers: {Accept: 'text/html', 'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) throw new Error('load');
      const html = await response.text();
      if (request.signal.aborted || !modal.open) return;
      mount(html, link.href); showStatus('');
      body.querySelector('input:not([type="hidden"]), select, textarea')?.focus();
    } catch (error) {
      if (error.name !== 'AbortError') showStatus('No se pudo cargar el formulario. Cierra e inténtalo de nuevo.', true);
    }
  });
  modal.addEventListener('click', event => {
    if (event.target.closest('[data-modal-close]') && !saving) modal.close();
    if (event.target === modal && !saving) {
      const rect = modal.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) modal.close();
    }
  });
  modal.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  modal.addEventListener('close', () => {
    controller?.abort(); body.dispatchEvent(new Event('masiscam:unmount'));
    body.replaceChildren(); footer.replaceChildren(); showStatus(''); trigger?.focus();
  });
})();
