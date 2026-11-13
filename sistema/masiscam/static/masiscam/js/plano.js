const root = document.querySelector('#pl-app');
if (root) {
  const $ = selector => root.querySelector(selector);
  const state = {data: null, pdf: null, image: null, page: 1, zoom: 1, active: null,
    point: null, configuring: false, dirty: false, rendering: null, generation: 0, saving: false};
  const stage = $('#pl-stage'), canvas = $('#pl-canvas'), viewport = $('#pl-viewport');
  const form = $('#pl-piece-form'), dialog = $('#pl-piece-dialog');
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function message(text, error = false) {
    $('#pl-message').textContent = text;
    $('#pl-message').classList.toggle('is-error', error);
  }
  async function request(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options,
      headers: {'X-CSRFToken': csrf, ...options.headers}});
    const data = await response.json().catch(() => ({error: 'No se pudo completar la operación. Revise su acceso o recargue.'}));
    if (!response.ok || data.error) throw new Error(data.error || 'No se confirmó la operación.');
    return data;
  }
  function tab(name) {
    root.dataset.tab = name;
    root.querySelectorAll('[data-tab]').forEach(button => {
      if (button === root) return;
      const active = button.dataset.tab === name;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    if (name === 'plano') setTimeout(() => render().catch(error => message(error.message, true)), 0);
  }
  root.querySelectorAll('.pl-mobile-tabs [data-tab]').forEach((button, index, buttons) => {
    button.addEventListener('click', () => tab(button.dataset.tab));
    button.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        const next = buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
        next.focus(); tab(next.dataset.tab);
      }
    });
  });
  function markers() {
    $('#pl-points').replaceChildren();
    if (!state.data) return;
    state.data.puntos.forEach(point => {
      if (Number(point.pagina) !== state.page) return;
      const button = el('button', point.numero, 'pl-point');
      button.type = 'button';
      button.style.left = `${Number(point.x) * 100}%`;
      button.style.top = `${Number(point.y) * 100}%`;
      button.dataset.parte = point.parte;
      button.setAttribute('aria-label', `Posición ${point.numero}`);
      button.classList.toggle('is-active', point.parte === state.active);
      button.classList.toggle('is-selected', state.data.piezas.some(piece => piece.parte === point.parte));
      button.addEventListener('click', event => {
        event.stopPropagation(); select(point.parte, point.id);
        if (!state.configuring && matchMedia('(max-width:991px)').matches) tab('componentes');
      });
      $('#pl-points').append(button);
    });
  }
  async function render() {
    if ((!state.pdf && !state.image) || !viewport.clientWidth) return;
    const generation = ++state.generation;
    if (state.rendering) {
      state.rendering.cancel();
      await state.rendering.promise.catch(() => {});
      state.rendering = null;
    }
    const page = state.pdf ? await state.pdf.getPage(state.page) : null;
    if (generation !== state.generation) return;
    const base = page ? page.getViewport({scale: 1}) : {width: state.image.naturalWidth, height: state.image.naturalHeight};
    const fit = Math.max(200, viewport.clientWidth - 26) / base.width;
    const width = Math.round(base.width * fit * state.zoom), height = Math.round(base.height * fit * state.zoom);
    const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(16000000 / (width * height)));
    stage.style.width = `${width}px`; stage.style.height = `${height}px`;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const context = canvas.getContext('2d');
    if (page) {
      const task = page.render({canvas, canvasContext: context, viewport: page.getViewport({scale: fit * state.zoom}),
        transform: [ratio, 0, 0, ratio, 0, 0]});
      state.rendering = task;
      try {await task.promise;} catch (error) {if (error.name !== 'RenderingCancelledException') throw error;}
      if (state.rendering === task) state.rendering = null;
    } else context.drawImage(state.image, 0, 0, canvas.width, canvas.height);
    $('#pl-zoom').textContent = `${Math.round(state.zoom * 100)}%`;
    markers();
  }
  function list() {
    if (!state.data) return;
    const query = $('#pl-search').value.trim().toLocaleLowerCase();
    const items = state.data.partes.filter(part => `${part.posicion} ${part.codigo} ${part.descripcion}`.toLocaleLowerCase().includes(query));
    $('#pl-components').replaceChildren();
    for (const part of items) {
      const button = el('button', undefined, 'pl-part'); button.type = 'button';
      button.dataset.parte = part.id;
      button.classList.toggle('is-active', part.id === state.active);
      button.setAttribute('aria-pressed', String(part.id === state.active));
      button.append(el('span', part.posicion, 'pl-position'));
      const body = el('span'); body.append(el('strong', part.codigo || 'Código por verificar'), el('small', part.descripcion));
      button.append(body); button.addEventListener('click', () => select(part.id)); $('#pl-components').append(button);
    }
    if (!items.length) $('#pl-components').append(el('p', 'No hay componentes con estos criterios.'));
    $('#pl-total').textContent = state.data.partes.length;
  }
  function select(id, pointId = null) {
    state.active = id;
    const part = state.data.partes.find(item => item.id === id);
    if (!part) return;
    const point = state.data.puntos.find(item => item.id === pointId) || state.data.puntos.find(item => item.parte === id);
    state.point = point?.id || null;
    if (point && state.page !== Number(point.pagina)) {
      state.page = Number(point.pagina); $('#pl-page').value = state.page;
      render().catch(error => message(error.message, true));
    }
    list(); markers();
    const detail = $('#pl-detail'); detail.replaceChildren();
    detail.append(el('h3', `Posición ${part.posicion} · ${part.codigo || 'Código pendiente'}`), el('p', part.descripcion),
      el('p', `Fuente: ${part.cantidad || 'cantidad no indicada'} ${part.unidad || '· unidad pendiente'}. La cantidad requerida debe indicarse expresamente.`));
    if (state.data.seleccionable && !state.configuring) {
      const button = el('button', 'Agregar al diagnóstico', 'btn btn-primary'); button.type = 'button';
      button.addEventListener('click', () => openPiece(part)); detail.append(button);
    } else detail.append(el('p', state.configuring ? 'Pulsa la ubicación numerada en el plano para vincular esta parte.' : 'Consulta de referencia; no se modifica el diagnóstico.'));
  }
  function selected() {
    const container = $('#pl-selected-list'); container.replaceChildren();
    $('#pl-count').textContent = $('#pl-count-mobile').textContent = state.data.piezas.length;
    for (const piece of state.data.piezas) {
      const card = el('article', undefined, 'pl-selected-card');
      card.append(el('strong', piece.codigo || piece.descripcion), el('p', piece.descripcion),
        el('p', `${piece.cantidad} ${piece.unidad} · ${piece.accion}`), el('p', piece.condicion));
      if (piece.observaciones) card.append(el('p', piece.observaciones));
      const source = piece.snapshot;
      if (source.provisional) card.append(el('small', 'Origen: plano provisional de TALLER; sujeto a revisión de PLANTA.'));
      if (source.referencia) card.append(el('small', `${source.referencia} r${source.revision} · posición ${source.posicion}${source.plano ? ` · mapa v${source.plano.version}, página ${source.plano.pagina}` : ' · selección por listado'}`));
      if (piece.fuente_url) {
        const sourceLink = el('a', 'Abrir plano fuente'); sourceLink.href = piece.fuente_url; card.append(sourceLink);
      }
      if (state.data.editable) {
        const edit = el('button', 'Editar', 'btn btn-outline-primary'); edit.type = 'button';
        edit.addEventListener('click', () => openPiece(state.data.partes.find(part => part.id === piece.parte), piece));
        const remove = el('button', 'Retirar de borrador', 'btn btn-outline-danger'); remove.type = 'button';
        remove.addEventListener('click', () => mutate('retirar', piece.id));
        const actions = el('div');
        if (state.data.seleccionable && state.data.partes.some(part => part.id === piece.parte)) actions.append(edit);
        actions.append(remove); card.append(actions);
      }
      container.append(card);
    }
    if (!state.data.piezas.length) container.append(el('p', 'Aún no hay piezas seleccionadas. Busca un componente para comenzar.'));
    markers();
  }
  function openPiece(part, piece = null) {
    form.reset(); $('#pl-form-error').hidden = true;
    form.elements.parte_id.value = part.id;
    form.elements.posicion_plano_id.value = piece ? (piece.posicion || '') : (state.point || '');
    form.elements.pieza_id.value = piece?.id || '';
    form.elements.version.value = state.data.version;
    form.elements.clave.value = crypto.randomUUID();
    for (const name of ['cantidad', 'unidad', 'condicion', 'accion', 'observaciones']) form.elements[name].value = piece?.[name] || (name === 'unidad' ? part.unidad : '');
    form.elements.unidad.readOnly = Boolean(part.unidad);
    $('#pl-piece-title').textContent = `${piece ? 'Editar' : 'Seleccionar'} posición ${part.posicion}`;
    $('#pl-piece-description').textContent = `${part.codigo || 'Código pendiente'} · ${part.descripcion}`;
    $('#pl-existing-photos').replaceChildren();
    for (const photo of piece?.fotos || []) {
      const box = el('div'), image = el('img'); image.src = photo.url + '?ver=1'; image.alt = photo.leyenda;
      const remove = el('button', 'Retirar foto', 'btn btn-outline-danger'); remove.type = 'button';
      remove.addEventListener('click', async () => {if (await mutate('retirar_foto', piece.id, photo.id)) {dialog.close();}});
      box.append(image, remove); $('#pl-existing-photos').append(box);
    }
    dialog.showModal(); form.elements.cantidad.focus();
  }
  function update(data) {
    state.data.version = data.version; state.data.piezas = data.piezas; selected();
    message(data.mensaje || 'Borrador guardado.');
  }
  async function mutate(operation, pieceId, evidenceId) {
    if (state.saving) return false;
    state.saving = true;
    const data = new FormData();
    Object.entries({operacion: operation, pieza_id: pieceId, version: state.data.version, clave: crypto.randomUUID()}).forEach(([name,value]) => data.set(name, value));
    if (evidenceId) data.set('evidencia_id', evidenceId);
    try {update(await request(root.dataset.pieza, {method: 'POST', body: data})); return true;}
    catch (error) {message(error.message, true); return false;}
    finally {state.saving = false;}
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (state.saving || !form.reportValidity()) return;
    state.saving = true; $('#pl-submit').disabled = true; $('#pl-submit').textContent = 'Guardando…';
    $('#pl-form-error').hidden = true;
    try {
      update(await request(root.dataset.pieza, {method: 'POST', body: new FormData(form)}));
      dialog.close();
    } catch (error) {$('#pl-form-error').hidden = false; $('#pl-form-error').textContent = error.message;}
    finally {state.saving = false; $('#pl-submit').disabled = false; $('#pl-submit').textContent = 'Guardar pieza';}
  });
  $('#pl-close-dialog').addEventListener('click', () => {if (!state.saving) dialog.close();});
  dialog.addEventListener('cancel', event => {if (state.saving) event.preventDefault();});
  $('#pl-search').addEventListener('input', list);
  for (const [selector, delta] of [['#pl-plus', .25], ['#pl-minus', -.25]]) $(selector).addEventListener('click', () => {
    state.zoom = Math.max(.5, Math.min(4, state.zoom + delta)); render().catch(error => message(error.message, true));
  });
  $('#pl-fit').addEventListener('click', () => {state.zoom = 1; render().catch(error => message(error.message, true));});
  $('#pl-page').addEventListener('change', event => {state.page = Number(event.target.value); render().catch(error => message(error.message, true));});
  let drag = null;
  viewport.addEventListener('pointerdown', event => {
    if (state.configuring || event.target.closest('button') || event.pointerType !== 'mouse') return;
    drag = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
    viewport.setPointerCapture(event.pointerId); viewport.classList.add('is-dragging');
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    viewport.scrollLeft = drag.left - event.clientX + drag.x; viewport.scrollTop = drag.top - event.clientY + drag.y;
  });
  const endDrag = () => {drag = null; viewport.classList.remove('is-dragging');};
  viewport.addEventListener('pointerup', endDrag); viewport.addEventListener('pointercancel', endDrag);
  stage.addEventListener('click', event => {
    if (!state.configuring || !state.active) return;
    const bounds = stage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width, y = (event.clientY - bounds.top) / bounds.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    const part = state.data.partes.find(part => part.id === state.active);
    state.data.puntos.push({id: null, parte: part.id, numero: part.posicion, pagina: state.page, x: x.toFixed(8), y: y.toFixed(8)});
    state.dirty = true; markers(); message(`Posición ${part.posicion} añadida. Guarda una nueva versión del mapa.`);
  });
  function configure(active) {
    state.configuring = active; $('#pl-config-tools').hidden = !active;
    $('#pl-config-toggle').hidden = active;
    if (state.active) select(state.active);
    message(active ? 'Modo configuración manual: selecciona una parte y pulsa su posición en el plano.' : 'Modo consulta.');
  }
  $('#pl-config-toggle')?.addEventListener('click', () => configure(true));
  $('#pl-remove-point')?.addEventListener('click', () => {
    if (!state.active) return message('Selecciona primero una parte.', true);
    state.data.puntos = state.data.puntos.filter(point => point.parte !== state.active); state.dirty = true; markers();
  });
  $('#pl-cancel-map')?.addEventListener('click', async () => {
    try {await load(); state.dirty = false; configure(false);} catch (error) {message(error.message, true);}
  });
  $('#pl-save-map')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const data = await request(root.dataset.mapa, {method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({clave: crypto.randomUUID(), version: state.data.mapa_version,
          posiciones: state.data.puntos.map(point => ({parte: point.parte, pagina: point.pagina, x: point.x, y: point.y}))})});
      await load(); state.dirty = false; configure(false); message(data.mensaje);
    } catch (error) {message(error.message, true);} finally {button.disabled = false;}
  });
  window.addEventListener('beforeunload', event => {if (state.dirty || state.saving) {event.preventDefault(); event.returnValue = '';}});
  let resizeTimer;
  window.addEventListener('resize', () => {clearTimeout(resizeTimer); resizeTimer = setTimeout(() => render().catch(error => message(error.message, true)), 200);});
  async function load() {
    state.data = await request(root.dataset.datos);
    list(); selected();
    $('#pl-map-label').textContent = state.data.mapa_version
      ? `Mapa v${state.data.mapa_version} · ${state.data.mapa_provisional ? 'provisional, pendiente de PLANTA' : 'publicado'} · coordenadas normalizadas`
      : 'Mapa sin configurar: puedes continuar por el listado y marcar posiciones si tienes permiso.';
  }
  async function start() {
    tab('plano'); await load();
    for (let index = 1; index <= state.data.documento.paginas; index++) $('#pl-page').append(new Option(String(index), index));
    if (state.data.documento.tipo === 'pdf') {
      const pdfjs = await import(root.dataset.pdfModule);
      pdfjs.GlobalWorkerOptions.workerSrc = root.dataset.pdfWorker;
      pdfjs.GlobalWorkerOptions.rendererSrc = root.dataset.pdfRenderer;
      const assets = new URL('./', new URL(root.dataset.pdfModule, location.href)).href;
      state.pdf = await pdfjs.getDocument({url: root.dataset.documento, withCredentials: true,
        isEvalSupported: false, enableXfa: false, cMapPacked: true, cMapUrl: assets+'cmaps/',
        standardFontDataUrl: assets+'standard_fonts/', wasmUrl: assets+'wasm/', iccUrl: assets+'iccs/'}).promise;
    } else {
      state.image = new Image(); state.image.src = root.dataset.documento; await state.image.decode();
    }
    await render(); root.dataset.ready = 'true';
    message(state.data.editable ? 'Selecciona una posición o componente para agregarlo al diagnóstico borrador.' : 'Plano autorizado · consulta de componentes y versiones.');
  }
  start().catch(error => {message(`No se pudo abrir el visor: ${error.message} Puedes utilizar el listado o descargar el original.`, true); root.dataset.ready = 'error';});
}
