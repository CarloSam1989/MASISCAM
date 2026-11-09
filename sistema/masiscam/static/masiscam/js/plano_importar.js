const form = document.querySelector('#pl-import-form');
if (form) {
  form.querySelector('[value="revisar"]').addEventListener('click', async event => {
    event.preventDefault();
    const button = event.currentTarget, status = document.querySelector('#pl-import-status');
    button.disabled = true; status.hidden = false; status.textContent = 'Revisando filas…';
    try {
      const data = new FormData(form); data.set('op','revisar'); data.delete('archivo');
      const response = await fetch(form.action || location.href, {method:'POST', body:data,
        credentials:'same-origin', headers:{Accept:'application/json','X-CSRFToken':data.get('csrfmiddlewaretoken')}});
      const result = await response.json().catch(()=>({error:'No se pudo verificar la lista. Revise su acceso y vuelva a intentarlo.'}));
      if (!response.ok || result.error) throw new Error(result.error || 'No se pudo verificar la lista.');
      const body = document.querySelector('#pl-import-rows'); body.replaceChildren();
      for (const row of result.filas) {
        const tr = document.createElement('tr');
        for (const name of ['posicion','numero_parte','descripcion','unidad','cantidad_referencia','pagina']) {
          const td = document.createElement('td'); td.textContent = row[name] || (name==='cantidad_referencia' ? 'No consta' : 'Por verificar'); tr.append(td);
        }
        body.append(tr);
      }
      document.querySelector('#pl-import-preview').hidden = false;
      status.textContent = `${result.filas.length} filas listas para revisión humana. No se guardó ni publicó ningún dato.`;
    } catch(error) {status.textContent=error.message;}
    finally {button.disabled=false;}
  });
}
