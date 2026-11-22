async function cargarPDF(node) {
  const pdfjs = await import(node.dataset.pdfModule);
  pdfjs.GlobalWorkerOptions.workerSrc = node.dataset.pdfWorker;
  pdfjs.GlobalWorkerOptions.rendererSrc = node.dataset.pdfRenderer;
  const assets = new URL('./', new URL(node.dataset.pdfModule, location.href)).href;
  return pdfjs.getDocument({url: node.dataset.documento, withCredentials: true,
    isEvalSupported: false, enableXfa: false, cMapPacked: true, cMapUrl: assets+'cmaps/',
    standardFontDataUrl: assets+'standard_fonts/', wasmUrl: assets+'wasm/', iccUrl: assets+'iccs/'}).promise;
}

document.querySelectorAll('#pl-preview, [data-pdf-preview]').forEach(preview => {
  (async () => {
    const pdf = await cargarPDF(preview), page = await pdf.getPage(1);
    const canvas = preview.querySelector('canvas'), base = page.getViewport({scale:1});
    const scale = Math.min(2, (preview.clientWidth || 800) / base.width);
    const viewport = page.getViewport({scale});
    canvas.width = Math.round(viewport.width); canvas.height = Math.round(viewport.height);
    await page.render({canvas, canvasContext:canvas.getContext('2d'), viewport}).promise;
    preview.dataset.ready = 'true'; preview.querySelector('[role=status]').hidden = true;
  })().catch(() => {
    preview.dataset.ready = 'error';
    preview.querySelector('[role=status]').textContent = 'No se pudo visualizar el PDF. Puedes abrir el visor o descargar el original.';
  });
});

const root = document.querySelector('#pl-app');
if (root) {
  const $ = selector => root.querySelector(selector);
  const state = {data: null, pdf: null, image: null, page: 1, zoom: 1, active: null,
    point: null, configuring: false, dirty: false, rendering: null, generation: 0, saving: false};
  const stage = $('#pl-stage'), canvas = $('#pl-canvas'), viewport = $('#pl-viewport');
  const positionDialog = $('#pl-position-dialog'), positionForm = $('#pl-position-form');
  let movingPoint = null, movingValues = null, positionKey = null;
  const pending = new Set();
  const quantity = value => String(value ?? '').replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1');
  for (const sheet of [positionForm].filter(Boolean)) {
    sheet.addEventListener('input', () => pending.add(sheet));
    sheet.addEventListener('change', () => pending.add(sheet));
  }
  const csrf = positionForm.querySelector('[name=csrfmiddlewaretoken]').value;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function message(text, error = false) {
    $('#pl-message').textContent = text;
    $('#pl-message').classList.toggle('is-error', error);
    if (error) window.MasiscamNotificaciones?.mostrar(text, 'error');
  }
  function saved(text) {
    message(text);
    requestAnimationFrame(() => window.MasiscamNotificaciones?.mostrar(text, 'success'));
  }
  async function closeSheet(sheet, modal) {
    if (state.saving) return;
    if (pending.has(sheet)) {
      const answer = await window.MasiscamNotificaciones?.confirmar('¿Descartar los cambios sin guardar de esta pieza?');
      if (!answer?.isConfirmed) return;
    }
    pending.delete(sheet); modal.close();
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
  $('#pl-select-pieces')?.addEventListener('click', () => tab('plano'));
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
        if (!state.configuring && state.data.marcable) selectPoint(point);
        else if (!state.configuring && matchMedia('(max-width:991px)').matches) tab('componentes');
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
      const body = el('span'); body.append(el('strong', part.codigo || `Posición ${part.posicion}`), el('small', part.descripcion));
      button.append(body); button.addEventListener('click', () => select(part.id)); $('#pl-components').append(button);
    }
    if (!items.length) $('#pl-components').append(el('p', state.data.partes.length ? 'No hay componentes con estos criterios.' : 'Aún no hay componentes registrados. Pulsa un número del PDF para registrarlo manualmente; no se usa OCR.'));
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
      el('p', `Fuente: ${quantity(part.cantidad) || 'cantidad no indicada'} ${part.unidad || '· unidad pendiente'}. La cantidad requerida debe indicarse expresamente.`));
    if (state.data.seleccionable && !state.configuring) {
      const button = el('button', 'Agregar al diagnóstico', 'btn btn-primary'); button.type = 'button';
      button.addEventListener('click', () => openPiece(part)); detail.append(button);
    } else detail.append(el('p', state.configuring ? 'Pulsa la ubicación numerada en el plano para vincular esta parte.' : 'Consulta de referencia; no se modifica el diagnóstico.'));
    if (point && state.data.marcable && state.data.mapa_provisional && !state.configuring) {
      const edit = el('button','Editar posición','btn btn-outline-primary'); edit.type='button';
      edit.addEventListener('click',()=>openPosition(point,point));
      const remove = el('button','Eliminar posición provisional','btn btn-outline-danger'); remove.type='button';
      remove.addEventListener('click',async()=>{
        const answer=await window.MasiscamNotificaciones?.confirmar('¿Retirar esta posición del mapa provisional? Se conservarán el componente, la selección y las versiones previas.');
        if (!answer?.isConfirmed) return;
        try {await positionRequest({operacion:'retirar',punto_id:point.id});}
        catch(error){message(error.message,true);}
      });
      detail.append(edit,remove);
    }
  }
  function selected() {
    const container = $('#pl-selected-list'); container.replaceChildren();
    $('#pl-count').textContent = $('#pl-count-mobile').textContent = state.data.piezas.length;
    const send = $('#pl-send-form');
    if (send) {
      send.elements.version.value = state.data.version;
      send.querySelector('button').disabled = !state.data.piezas.length;
    }
    for (const piece of state.data.piezas) {
      const card = el('article', undefined, 'pl-selected-card');
      card.dataset.parte = piece.parte;
      card.append(el('strong', piece.codigo || piece.descripcion), el('p', piece.descripcion),
        el('p', `${quantity(piece.cantidad)} ${piece.unidad} · ${piece.accion}`), el('p', piece.condicion));
      if (piece.observaciones) card.append(el('p', piece.observaciones));
      const source = piece.snapshot;
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
  function focusSelected(partId) {
    if (matchMedia('(max-width:991px)').matches) tab('seleccionados');
    const card=$(`#pl-selected-list [data-parte="${partId}"]`);
    if(card){card.tabIndex=-1;card.focus({preventScroll:true});card.scrollIntoView({block:'nearest'});}
  }
  async function positionRequest(values, key=crypto.randomUUID(), photos=[]) {
    if(state.saving) throw new Error('Espera a que termine el guardado.');
    state.saving=true;
    const scroll = {left:viewport.scrollLeft, top:viewport.scrollTop, x:window.scrollX, y:window.scrollY};
    try {
      const payload = {clave:key,diagnostico_id:root.dataset.diagnostico,version:state.data.version,
        mapa_version:state.data.mapa_version,...values};
      const options = {method:'POST'};
      if (photos.length) {
        options.body = new FormData();
        Object.entries(payload).forEach(([name,value])=>options.body.set(name,value));
        photos.forEach(photo=>options.body.append('fotografias',photo));
      } else {
        options.headers = {'Content-Type':'application/json'};
        options.body = JSON.stringify(payload);
      }
      const response=await request(root.dataset.posiciones,options);
      root.dataset.datos=response.datos_url;root.dataset.posiciones=response.posicion_url;root.dataset.mapa=response.mapa_url;
      const ref = response.referencia;
      if (ref) {
        root.dataset.documento = ref.documento_url + '?ver=1';
        $('#pl-reference-title').textContent = ref.titulo;
        $('#pl-reference-badge').textContent = ref.estado === 'BORRADOR' ? 'Borrador' : `r${ref.revision}`;
        $('#pl-original').href = ref.documento_url;
        $('#pl-original').textContent = ref.estado === 'BORRADOR' ? 'PDF original ↓' : 'Original autorizado ↓';
        $('#pl-draft-note').hidden = ref.estado !== 'BORRADOR';
        const manual = $('#pl-manual-component');
        if (manual) manual.action = ref.componente_url;
      }
      history.replaceState(null,'',response.plano_url);
      await load();
      if(state.active && state.data.partes.some(p=>p.id===state.active))select(state.active);
      saved(response.mensaje);return response;
    } finally {
      state.saving=false;
      requestAnimationFrame(()=>{viewport.scrollTo(scroll.left,scroll.top);window.scrollTo(scroll.x,scroll.y);});
    }
  }
  async function selectPoint(point) {
    if(state.saving) return;
    const piece=state.data.piezas.find(item=>item.parte===point.parte);
    if(piece){focusSelected(point.parte);return;}
    const part=state.data.partes.find(item=>item.id===point.parte);
    if(!part?.cantidad && !part?.cantidad_seleccion){openPosition(point,point);return;}
    try {await positionRequest({operacion:'seleccionar',punto_id:point.id});focusSelected(point.parte);}
    catch(error){message(error.message,true);}
  }
  function fillPositionPart(part, pieceOverride = null) {
    for(const [name,value] of Object.entries({posicion:part?.posicion||'',descripcion:part?.descripcion||'',unidad:part?.unidad||''})){
      positionForm.elements[name].value=value;
      if(name!=='unidad') positionForm.elements[name].readOnly=Boolean(part);
    }
    const piece=pieceOverride||state.data.piezas.find(item=>item.parte===part?.id);
    positionForm.elements.cantidad.value=quantity(piece?.cantidad||part?.cantidad_seleccion||part?.cantidad||'');
    positionForm.elements.condicion.value=piece?.condicion||'';
    const code=$('#pl-position-code');if(code)code.value=part?.codigo||'';
  }
  // --- Autocompletado desde el catálogo activo vinculado al equipo (solo lectura, no modifica el catálogo) ---
  const touched={descripcion:false,unidad:false},auto={descripcion:'',unidad:''};let lookupSeq=0,lookupTimer=null;
  function resetCatalogo(){touched.descripcion=touched.unidad=false;auto.descripcion=auto.unidad='';lookupSeq++;clearTimeout(lookupTimer);
    for(const id of ['#pl-position-code','#pl-position-refqty']){const input=$(id);if(input)input.value='';}
    const status=$('#pl-position-catalog');if(status){status.textContent='';status.classList.remove('text-danger');}}
  // Rellena solo campos vacíos o que aún tienen el valor precargado antes; lo que TALLER escribió nunca se pisa.
  function aplicarAuto(nuevo){
    for(const nombre of ['descripcion','unidad']){const campo=positionForm.elements[nombre];
      if(campo.readOnly)continue;
      if(campo.value.trim()===''||(!touched[nombre]&&campo.value===auto[nombre])){campo.value=nuevo[nombre];auto[nombre]=nuevo[nombre];touched[nombre]=false;}}
    if(nuevo.unidad)positionForm.querySelectorAll('details').forEach(section=>{section.open=true;});
  }
  async function consultarCatalogo(posicion,rellenar){
    const url=root.dataset.catalogoParte,status=$('#pl-position-catalog'),number=String(posicion||'').trim();
    if(!url||!status||!number)return;
    const mine=++lookupSeq;status.classList.remove('text-danger');status.textContent='Buscando en el catálogo…';
    try{
      const response=await fetch(url+'&posicion='+encodeURIComponent(number),{credentials:'same-origin',headers:{'Accept':'application/json'}});
      const data=await response.json().catch(()=>({error:'No se pudo completar la operación. Revise su acceso o recargue.'}));
      if(mine!==lookupSeq||String(positionForm.elements.posicion.value).trim()!==number)return;  // llegó una respuesta vieja
      if(!response.ok)throw new Error(data.error||'No se pudo consultar el catálogo.');
      const code=$('#pl-position-code'),qty=$('#pl-position-refqty');
      if(data.estado==='encontrada'){
        code.value=data.codigo||'';qty.value=quantity(data.cantidad_referencia||'');
        if(rellenar)aplicarAuto({descripcion:data.descripcion||'',unidad:data.unidad||''});
        status.textContent=data.mensaje+' Complete la cantidad requerida, el daño o condición y las fotografías.';
      }else{
        code.value='';qty.value='';if(rellenar)aplicarAuto({descripcion:'',unidad:''});
        status.textContent=(data.mensaje||'Sin datos de catálogo.')+(data.estado==='sin_posicion'||data.estado==='sin_catalogo'?' Puede registrarla como pieza nueva; el catálogo no se modifica.':'');
      }
    }catch(error){if(mine===lookupSeq){status.classList.add('text-danger');status.textContent='No se pudo consultar el catálogo: '+error.message+' Puede continuar con el registro manual.';}}
  }
  function posicionEscrita(){
    const number=positionForm.elements.posicion.value.trim();
    if(positionForm.elements.posicion.readOnly||!number)return;
    // Si ya existe en este plano, se usa ese componente (evita duplicados); si no, se consulta el catálogo.
    const existente=state.data.partes.find(item=>item.posicion===number);
    if(existente&&String(existente.id)!==positionForm.elements.parte_id.value){positionForm.elements.parte_id.value=existente.id;fillPositionPart(existente);consultarCatalogo(number,false);return;}
    if(!existente&&positionForm.elements.parte_id.value===''){consultarCatalogo(number,true);}
  }
  positionForm?.elements.posicion.addEventListener('input',()=>{clearTimeout(lookupTimer);lookupTimer=setTimeout(posicionEscrita,350);});
  positionForm?.elements.posicion.addEventListener('change',()=>{clearTimeout(lookupTimer);posicionEscrita();});
  positionForm?.elements.descripcion.addEventListener('input',()=>{touched.descripcion=true;});
  positionForm?.elements.unidad.addEventListener('input',()=>{touched.unidad=true;});
  positionForm?.elements.fotografias.addEventListener('change',event=>{
    if(event.target.files.length>6){event.target.value='';const box=$('#pl-position-error');box.hidden=false;box.textContent='Máximo 6 fotografías por guardado.';}});
  // Un único formulario para elegir una pieza, desde el plano (con ubicación) o desde el listado de componentes.
  function openPiece(part, piece = null) {
    piece ||= state.data.piezas.find(item=>item.parte===part.id) || null;
    const point = state.data.marcable ? (state.data.puntos.find(item=>item.parte===part.id) || null) : null;
    openPosition(point, point, part, piece);
  }
  function openPosition(coords, point=null, forcedPart=null, forcedPiece=null) {
    if(!positionDialog || state.saving) return;
    const ubicada = Boolean(coords) && Boolean(state.data.marcable);
    if(!ubicada && !(state.data.seleccionable && forcedPart)) return;
    positionForm.reset();$('#pl-position-error').hidden=true;positionKey=crypto.randomUUID();
    positionForm.dataset.modo = ubicada ? 'plano' : 'listado';
    positionForm.querySelectorAll('details').forEach(section=>{section.open=false;});
    const select = positionForm.elements.parte_id;
    select.replaceChildren(...(ubicada ? [new Option('Nuevo componente del plano','')] : []));
    if(ubicada) select.options[0].disabled = Boolean(point);
    for(const item of state.data.partes) select.add(new Option(`${item.posicion} · ${item.descripcion}`,item.id));
    for(const name of ['pagina','x','y']) positionForm.elements[name].value = ubicada ? (coords[name] ?? state.page) : '';
    positionForm.elements.punto_id.value = point?.id || '';
    const part = forcedPart || (point ? state.data.partes.find(item=>item.id===point.parte) : null);
    const piece = forcedPiece || (part ? state.data.piezas.find(item=>item.parte===part.id) : null) || null;
    select.value = part?.id || '';select.disabled = !ubicada;
    fillPositionPart(part, piece);
    positionForm.elements.accion.value = piece?.accion || 'EVALUAR';
    positionForm.elements.observaciones.value = piece?.observaciones || '';
    positionForm.elements.pieza_id.value = piece?.id || '';
    positionForm.elements.posicion_plano_id.value = point?.id || piece?.posicion || '';
    positionForm.elements.unidad.required = !ubicada;positionForm.elements.condicion.required = !ubicada;
    positionForm.dataset.parte = part?.id || '';
    const existing = $('#pl-position-existing');existing.replaceChildren();
    for (const photo of piece?.fotos || []) {
      const box = el('div'), image = el('img'); image.src = photo.url + '?ver=1'; image.alt = photo.leyenda;
      const remove = el('button', 'Retirar foto', 'btn btn-outline-danger'); remove.type = 'button';
      remove.addEventListener('click', async () => {if (await mutate('retirar_foto', piece.id, photo.id)) {positionDialog.close();}});
      box.append(image, remove); existing.append(box);
    }
    resetCatalogo();if(part)consultarCatalogo(part.posicion,false);
    $('#pl-position-title').textContent = piece ? 'Editar pieza' : (ubicada ? 'Registrar pieza en el plano' : 'Seleccionar pieza');
    $('#pl-position-move').hidden = !(point && ubicada && state.data.mapa_provisional);
    $('#pl-position-move').onclick=()=>{pending.add(positionForm);movingPoint=point;movingValues={...Object.fromEntries(new FormData(positionForm)),photos:Array.from(positionForm.elements.fotografias.files)};positionDialog.close();message('Pulsa la nueva ubicación en el PDF.');};
    positionDialog.showModal();positionForm.elements[part?'cantidad':'posicion'].focus();
  }
  positionForm?.elements.parte_id.addEventListener('change',event=>{
    const part=state.data.partes.find(p=>String(p.id)===event.target.value);fillPositionPart(part);
    $('#pl-position-refqty').value='';if(part)consultarCatalogo(part.posicion,false);else resetCatalogo();});
  $('#pl-position-cancel')?.addEventListener('click',()=>closeSheet(positionForm,positionDialog));
  positionDialog?.addEventListener('cancel',event=>{event.preventDefault();closeSheet(positionForm,positionDialog);});
  positionForm?.addEventListener('submit',async event=>{
    event.preventDefault();if(state.saving||!positionForm.reportValidity())return;
    $('#pl-position-save').disabled=true;$('#pl-position-error').hidden=true;
    const fields = new FormData(positionForm);
    const photos=fields.getAll('fotografias').filter(photo=>photo.size);
    try {
      if(photos.length>6) throw new Error('Máximo 6 fotografías por guardado.');
      if(positionForm.dataset.modo==='listado'){
        // Componente del listado sin ubicación en el plano: misma pieza, mismo formulario.
        const body=new FormData();
        body.set('parte_id',positionForm.dataset.parte);body.set('posicion_plano_id',positionForm.elements.posicion_plano_id.value);
        body.set('pieza_id',positionForm.elements.pieza_id.value);body.set('version',state.data.version);body.set('clave',positionKey);
        for(const name of ['cantidad','unidad','condicion','accion','observaciones'])body.set(name,fields.get(name)||'');
        photos.forEach(photo=>body.append('fotografias',photo));
        state.saving=true;
        const parteId=Number(positionForm.dataset.parte);
        try{update(await request(root.dataset.pieza,{method:'POST',body}));}finally{state.saving=false;}
        pending.delete(positionForm);positionDialog.close();positionForm.reset();positionKey=null;
        if(state.data.marcable&&!state.data.puntos.some(point=>point.parte===parteId))message('Pieza guardada. Para marcarla en el PDF, pulsa su número en el dibujo y elige este componente.');
        return;
      }
      const data=Object.fromEntries(Array.from(fields).filter(([name])=>name!=='fotografias'));
      await positionRequest({operacion:'guardar',...data},positionKey,photos);
      pending.delete(positionForm);positionDialog.close();const part=state.data.partes.find(p=>p.posicion===data.posicion);
      if(part)select(part.id);
      positionForm.reset();positionKey=null;
    }catch(error){$('#pl-position-error').hidden=false;$('#pl-position-error').textContent=error.message;message(error.message,true);}
    finally{$('#pl-position-save').disabled=false;}
  });
  $('#pl-position-close')?.addEventListener('click',()=>closeSheet(positionForm,positionDialog));
  function update(data) {
    // Pieza elegida desde otra revisión del mismo catálogo: se indica en esta (misma posición), sin duplicarla.
    for (const piece of data.piezas) {
      if (!state.data.partes.some(part => part.id === piece.parte)) {
        const equal = state.data.partes.find(part => part.posicion === piece.snapshot?.posicion);
        if (equal) piece.parte = equal.id;
      }
    }
    state.data.version = data.version; state.data.piezas = data.piezas; selected();
    saved(data.mensaje || 'Borrador guardado.');
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
  $('#pl-search').addEventListener('input', list);
  for (const [selector, delta] of [['#pl-plus', .25], ['#pl-minus', -.25]]) $(selector).addEventListener('click', () => {
    state.zoom = Math.max(.5, Math.min(4, state.zoom + delta)); render().catch(error => message(error.message, true));
  });
  $('#pl-fit').addEventListener('click', () => {state.zoom = 1; render().catch(error => message(error.message, true));});
  $('#pl-page').addEventListener('change', event => {state.page = Number(event.target.value); render().catch(error => message(error.message, true));});
  let drag = null;
  let dragged = false;
  viewport.addEventListener('pointerdown', event => {
    dragged=false;
    if (state.configuring || event.target.closest('button') || event.pointerType !== 'mouse') return;
    drag = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    if(!dragged && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6)return;
    dragged=true;viewport.setPointerCapture(event.pointerId);viewport.classList.add('is-dragging');
    viewport.scrollLeft = drag.left - event.clientX + drag.x; viewport.scrollTop = drag.top - event.clientY + drag.y;
  });
  const endDrag = () => {drag = null; viewport.classList.remove('is-dragging');};
  viewport.addEventListener('pointerup', endDrag); viewport.addEventListener('pointercancel', endDrag);
  stage.addEventListener('click', event => {
    if(dragged){dragged=false;return;}
    if ((!state.configuring && !state.data?.marcable) || (state.configuring && !state.active)) return;
    const bounds = stage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width, y = (event.clientY - bounds.top) / bounds.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    if(!state.configuring){
      const point=movingPoint, values=movingValues;movingPoint=null;movingValues=null;
      openPosition({pagina:state.page,x:x.toFixed(8),y:y.toFixed(8)},point);
      if(values)for(const name of ['cantidad','condicion','unidad'])positionForm.elements[name].value=values[name];
      if(values?.photos){const transfer=new DataTransfer();values.photos.forEach(photo=>transfer.items.add(photo));positionForm.elements.fotografias.files=transfer.files;}
      return;
    }
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
  window.addEventListener('beforeunload', event => {if (state.dirty || state.saving || pending.size || movingPoint) {event.preventDefault(); event.returnValue = '';}});
  let resizeTimer;
  window.addEventListener('resize', () => {clearTimeout(resizeTimer); resizeTimer = setTimeout(() => render().catch(error => message(error.message, true)), 200);});
  async function load() {
    state.data = await request(root.dataset.datos);
    list(); selected();
    $('#pl-map-label').textContent = state.data.mapa_version
      ? `Mapa v${state.data.mapa_version} · ${state.data.mapa_provisional ? 'provisional' : 'publicado'} · coordenadas normalizadas`
      : 'Mapa sin configurar: puedes continuar por el listado y marcar posiciones si tienes permiso.';
  }
  async function start() {
    tab('plano'); await load();
    for (let index = 1; index <= state.data.documento.paginas; index++) $('#pl-page').append(new Option(String(index), index));
    if (state.data.documento.tipo === 'pdf') {
      state.pdf = await cargarPDF(root);
    } else {
      state.image = new Image(); state.image.src = root.dataset.documento; await state.image.decode();
    }
    await render(); root.dataset.ready = 'true';
    message(state.data.marcable ? 'Pulsa un número del PDF para registrar o seleccionar su pieza. También puedes buscar en el listado. No se usa OCR.' : 'Plano autorizado · consulta de componentes y versiones.');
  }
  start().catch(error => {message(`No se pudo abrir el visor: ${error.message} Puedes utilizar el listado o descargar el original.`, true); root.dataset.ready = 'error';});
}
