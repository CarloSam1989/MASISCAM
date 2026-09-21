window.masiscamInitClienteSelector = (root = document) => {
  const block = root.querySelector('#cliente-bloque');
  if (!block || block.dataset.initialized) return;
  block.dataset.initialized = 'true';
  const ruc = root.querySelector('#id_ruc');
  const selected = root.querySelector('#id_cliente');
  const reason = root.querySelector('#id_razon_social');
  const name = root.querySelector('#cliente-nombre');
  const email = root.querySelector('#cliente-correo');
  const phone = root.querySelector('#cliente-telefono');
  const status = root.querySelector('#cliente-estado');
  const results = root.querySelector('#cliente-resultados');
  const create = root.querySelector('#cliente-crear');
  const change = root.querySelector('#cliente-cambiar');
  const legacy = root.querySelector('#cliente-sin-asociar');
  const initial = JSON.parse(root.querySelector('#masiscam-cliente-inicial').textContent);
  const old = {ruc: ruc.value, reason: reason.value};
  let timer, controller, revision = 0;
  const cancelSearch = () => {
    clearTimeout(timer);
    if (controller) controller.abort();
    revision += 1;
  };
  root.addEventListener('masiscam:unmount', cancelSearch, {once: true});
  const choose = client => {
    cancelSearch();
    selected.value = client.id;
    ruc.value = client.ruc;
    name.value = client.nombre_comercial;
    reason.value = client.razon_social;
    email.value = client.correo;
    phone.value = client.telefono;
    ruc.readOnly = reason.readOnly = true;
    status.textContent = 'Cliente encontrado';
    results.replaceChildren();
    if (create) create.hidden = true;
    change.hidden = false;
  };
  const clearSelection = () => {
    selected.value = '';
    name.value = email.value = phone.value = '';
    ruc.readOnly = false;
    reason.readOnly = block.dataset.legacy !== 'true';
    if (reason.readOnly) reason.value = '';
    change.hidden = true;
  };
  const search = async () => {
    const query = ruc.value.trim();
    cancelSearch();
    const current = revision;
    clearSelection();
    results.replaceChildren();
    if (create) create.hidden = true;
    if (!query) { status.textContent = 'Escribe el RUC para buscar.'; return; }
    controller = new AbortController();
    status.textContent = 'Buscando cliente…';
    try {
      const url = new URL(block.dataset.buscarUrl, location.origin);
      url.search = new URLSearchParams({formato: 'json', ruc: query});
      const response = await fetch(url, {signal: controller.signal, headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('search');
      const data = await response.json();
      if (current !== revision) return;
      status.textContent = data.clientes.length ? 'Cliente encontrado' : 'Cliente no registrado';
      data.clientes.forEach(client => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'list-group-item list-group-item-action';
        button.textContent = `${client.ruc} · ${client.nombre_comercial} — Seleccionar`;
        button.addEventListener('click', () => choose(client));
        results.append(button);
      });
      if (create) create.hidden = data.clientes.some(client => client.ruc === query);
    } catch (error) {
      if (current !== revision || error.name === 'AbortError') return;
      status.textContent = 'No se pudo buscar. Revisa la conexión e inténtalo de nuevo.';
    }
  };
  ruc.addEventListener('input', () => {
    cancelSearch();
    clearSelection();
    results.replaceChildren();
    if (create) create.hidden = true;
    status.textContent = 'Buscando cliente…';
    timer = setTimeout(search, 250);
  });
  change.addEventListener('click', () => { cancelSearch(); clearSelection(); ruc.value = ''; status.textContent = 'Escribe el RUC para buscar.'; ruc.focus(); });
  if (legacy) legacy.addEventListener('click', () => {
    cancelSearch(); clearSelection(); results.replaceChildren();
    ruc.value = old.ruc; reason.value = old.reason;
    if (create) create.hidden = true;
    status.textContent = 'Sin cliente asociado (registro anterior).';
  });
  if (initial) choose(initial);
  else {
    reason.readOnly = block.dataset.legacy !== 'true';
    if (reason.readOnly && ruc.value.trim()) search();
  }

  const modal = root.querySelector('#cliente-modal');
  if (!create || !modal) return;
  const form = root.querySelector('#cliente-modal-form');
  const errors = root.querySelector('#cliente-modal-errores');
  const save = root.querySelector('#cliente-modal-guardar');
  const close = root.querySelector('#cliente-modal-cerrar');
  let saving = false;
  create.addEventListener('click', () => {
    form.reset(); errors.hidden = true; errors.replaceChildren();
    root.querySelector('#id_nuevo-ruc').value = ruc.value.trim();
    modal.showModal(); root.querySelector('#id_nuevo-nombre_comercial').focus();
  });
  close.addEventListener('click', () => modal.close());
  modal.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || !form.reportValidity()) return;
    saving = true; save.disabled = close.disabled = true;
    save.textContent = 'Guardando…'; errors.hidden = true;
    const body = new FormData();
    for (const [key, value] of new FormData(form)) body.append(key.replace(/^nuevo-/, ''), value);
    try {
      const response = await fetch(form.action, {method: 'POST', body, headers: {Accept: 'application/json'}});
      const data = await response.json();
      if (!response.ok) {
        errors.textContent = Object.values(data.errores || {}).flat().map(error => error.message).join(' ') || 'No se pudo guardar el cliente.';
        errors.hidden = false; return;
      }
      choose(data.cliente);
      status.textContent = data.creado ? 'Cliente creado y seleccionado.' : 'Cliente encontrado. Se seleccionó el registro existente.';
      modal.close(); change.focus();
    } catch (_) {
      errors.textContent = 'No se pudo guardar. Revisa la conexión e inténtalo de nuevo.';
      errors.hidden = false;
    } finally {
      saving = false; save.disabled = close.disabled = false; save.textContent = 'Guardar y seleccionar';
    }
  });
};
window.masiscamInitClienteSelector();
