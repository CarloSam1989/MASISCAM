/* Guardado automático de repuestos: una petición por fila a la vez, sin perder cambios intermedios. */
(function () {
  const tabla = document.querySelector('table[data-autoguardado]');
  if (!tabla) return;
  const url = tabla.dataset.url;
  const token = tabla.querySelector('input[name=csrfmiddlewaretoken]').value;
  const estados = new WeakMap();
  const CAMPOS = ['posicion', 'numero_parte', 'descripcion', 'unidad', 'cantidad_referencia', 'pagina'];

  const valores = (fila) => Object.fromEntries(CAMPOS.map((c) => [c, fila.querySelector('[data-campo="' + c + '"]').value.trim()]));
  const igual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const estado = (fila) => {
    if (!estados.has(fila)) estados.set(fila, { enVuelo: false, sucio: false, ultimo: fila.hasAttribute('data-nueva') ? {} : valores(fila), error: false, intento: null });
    return estados.get(fila);
  };

  function indicar(fila, texto, clase, reintento) {
    const el = fila.querySelector('.mc-autoguardado');
    el.className = 'mc-autoguardado ' + (clase || '');
    el.textContent = '';
    if (reintento) {
      const boton = document.createElement('button');
      boton.type = 'button';
      boton.className = 'btn btn-link btn-sm p-0';
      boton.textContent = texto + ' · Reintentar';
      boton.addEventListener('click', () => guardar(fila, true));
      el.appendChild(boton);
    } else {
      el.textContent = texto;
    }
  }

  function marcarErrores(fila, errores) {
    fila.querySelectorAll('[data-campo]').forEach((i) => i.removeAttribute('aria-invalid'));
    Object.keys(errores || {}).forEach((campo) => {
      const input = fila.querySelector('[data-campo="' + campo + '"]');
      if (input) { input.setAttribute('aria-invalid', 'true'); input.title = errores[campo].join(' '); }
    });
  }

  function nuevaFila() {
    const base = tabla.querySelector('tr[data-nueva]');
    const copia = base.cloneNode(true);
    copia.removeAttribute('title');
    copia.querySelectorAll('[data-campo]').forEach((i) => { i.value = ''; i.removeAttribute('aria-invalid'); i.removeAttribute('title'); });
    copia.querySelector('.mc-autoguardado').textContent = '';
    return copia;
  }

  async function guardar(fila, forzar) {
    const st = estado(fila);
    if (st.enVuelo) { st.sucio = true; return; }
    const v = valores(fila);
    if (!forzar && igual(v, st.ultimo)) return;
    const esNueva = fila.hasAttribute('data-nueva');
    if (!v.posicion || !v.descripcion) {
      if (esNueva && !v.posicion && !v.descripcion && !v.numero_parte) return;
      marcarErrores(fila, { posicion: v.posicion ? [] : ['Obligatoria.'], descripcion: v.descripcion ? [] : ['Obligatoria.'] });
      indicar(fila, esNueva ? 'Complete posición y descripción' : 'Error al guardar: posición y descripción son obligatorias', 'text-danger');
      st.error = !esNueva;
      return;
    }
    st.enVuelo = true;
    st.sucio = false;
    indicar(fila, 'Guardando…', 'text-muted');
    const cuerpo = new FormData();
    cuerpo.append('csrfmiddlewaretoken', token);
    cuerpo.append('op', 'parte');
    // Un reintento del mismo contenido reutiliza la clave: si la respuesta anterior se perdió, el servidor no duplica.
    const huella = JSON.stringify(v);
    if (!st.intento || st.intento.huella !== huella || st.intento.parte !== (fila.dataset.parte || '')) {
      st.intento = { huella, parte: fila.dataset.parte || '', clave: crypto.randomUUID() };
    }
    cuerpo.append('clave', st.intento.clave);
    if (fila.dataset.parte) {
      cuerpo.append('parte_id', fila.dataset.parte);
      // Solo los campos modificados: el servidor conserva los demás tal como estén en ese momento.
      cuerpo.append('campos', CAMPOS.filter((c) => v[c] !== (st.ultimo[c] || '')).join(','));
    }
    CAMPOS.forEach((c) => cuerpo.append(c, v[c]));
    let correcto = false;
    try {
      const respuesta = await fetch(url, { method: 'POST', body: cuerpo, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const datos = await respuesta.json().catch(() => ({}));
      if (respuesta.ok && datos.ok) {
        correcto = true;
        st.intento = null;
        st.ultimo = v;
        // Valores vigentes en el servidor: se muestran los campos que esta pantalla no tocó ni tiene enfocados.
        if (datos.valores) {
          CAMPOS.forEach((c) => {
            const input = fila.querySelector('[data-campo="' + c + '"]');
            if (input && input !== document.activeElement && input.value.trim() === v[c] && datos.valores[c] !== v[c]) {
              input.value = datos.valores[c];
              v[c] = datos.valores[c];
            }
          });
          st.ultimo = Object.assign({}, v);
        }
        st.error = false;
        marcarErrores(fila, {});
        fila.dataset.parte = datos.parte_id;
        fila.classList.toggle('table-warning', !v.descripcion);
        fila.classList.remove('table-light');
        if (esNueva) {
          const siguiente = nuevaFila();  // se clona antes de convertir esta fila en una pieza guardada
          fila.removeAttribute('data-nueva');
          const vacia = tabla.querySelector('.mc-sin-filas');
          if (vacia) vacia.remove();
          siguiente.className = 'table-light';
          siguiente.dataset.parte = '';
          fila.after(siguiente);
        }
        indicar(fila, 'Guardado', 'text-success');
      } else {
        st.error = true;
        marcarErrores(fila, datos.errores);
        const detalle = Object.values(datos.errores || {}).flat().join(' ');
        indicar(fila, 'Error al guardar' + (detalle ? ': ' + detalle : respuesta.status === 403 ? ': sin permiso o catálogo no editable' : ''), 'text-danger', true);
      }
    } catch (e) {
      st.error = true;
      indicar(fila, 'Error al guardar (sin conexión)', 'text-danger', true);
    } finally {
      st.enVuelo = false;
      // Cambios hechos durante el envío: se guardan en seguida, nunca se descartan.
      if (st.sucio || (correcto && !igual(valores(fila), st.ultimo))) { st.sucio = false; guardar(fila); }
    }
  }

  // Valores guardados de cada fila al cargar: el primer cambio debe compararse contra ellos.
  tabla.querySelectorAll('tr[data-parte]').forEach(estado);

  tabla.addEventListener('change', (e) => {
    const fila = e.target.closest('tr[data-parte]');
    if (fila && e.target.matches('[data-campo]')) guardar(fila);
  });
  // Un campo de texto aún enfocado debe guardarse si se abandona la página.
  window.addEventListener('beforeunload', (e) => {
    const pendiente = [...tabla.querySelectorAll('tr[data-parte]')].some((f) => { const s = estado(f); return s.enVuelo || s.sucio || s.error; });
    if (pendiente) { e.preventDefault(); e.returnValue = ''; }
  });
  window.addEventListener('online', () => tabla.querySelectorAll('tr[data-parte]').forEach((f) => { if (estado(f).error) guardar(f, true); }));
})();
