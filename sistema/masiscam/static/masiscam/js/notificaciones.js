/* Local SweetAlert2 integration. Server authorization remains authoritative. */
(() => {
  'use strict';
  const api = window.MasiscamNotificaciones = {};
  const opciones = {buttonsStyling:false,customClass:{popup:'mc-popup',confirmButton:'btn btn-primary',cancelButton:'btn btn-outline-secondary'},confirmButtonText:'Aceptar'};
  const seen = new WeakSet();
  api.mostrar = (text, icon='info') => window.Swal ? Swal.fire({...opciones,target:document.querySelector('dialog[open]') || document.body,text,icon,
    ...(icon==='success'?{toast:true,position:'top-end',timer:3500,timerProgressBar:true,showConfirmButton:false,showCloseButton:true,closeButtonAriaLabel:'Cerrar notificación'}:{})}) : Promise.resolve({isConfirmed:false});
  api.confirmar = text => window.Swal ? Swal.fire({...opciones,title:'Confirmar acción',text,icon:'question',showCancelButton:true,cancelButtonText:'Cancelar',confirmButtonText:'Sí, continuar',focusCancel:true,allowOutsideClick:false}) : Promise.resolve({isConfirmed:false});
  const preguntas = {solicitud:'¿Enviar la solicitud de repuestos? El listado quedará congelado para revisión.',cotizar:'¿Registrar esta cotización?',adquirir:'¿Registrar esta adquisición y sus cantidades?',enviar:'¿Generar la guía y registrar el envío?',recibir:'¿Confirmar las cantidades realmente recibidas?',aceptacion:'¿Registrar la respuesta verificable del cliente? Se conservarán su respaldo y autoría.'};
  const preguntaPara=(form,op)=>{
    if(form.dataset.confirm) return form.dataset.confirm;
    const path=new URL(form.action || location.href,location.href).pathname;
    if(path.includes('/flujo/')) return preguntas[op];
    if(op==='enviar' && path.includes('/calibracion/')) return '¿Enviar las 16 mediciones a revisión independiente? Los datos enviados quedarán congelados.';
    if(op==='enviar' && path.includes('/tecnica/')) return '¿Enviar la revisión técnica? Los datos y evidencias enviados quedarán congelados.';
    if(op==='decision' && path.includes('/operacion/')) return preguntas.aceptacion;
    if(['decision','revision','revisar'].includes(op)) return '¿Registrar esta decisión independiente? El servidor verificará el responsable y los requisitos técnicos.';
    if(op==='cerrar') return '¿Cerrar administrativamente el expediente? Se verificarán las aprobaciones independientes y se conservará el historial.';
    return null;
  };
  document.addEventListener('submit',async event => {
    const form=event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.mcConfirmed==='1') { delete form.dataset.mcConfirmed; return; }
    const submitter=event.submitter;
    const op=submitter?.name==='op' ? submitter.value : form.querySelector('[name="op"]')?.value;
    const pregunta=preguntaPara(form,op);
    if (op==='rechazar_plano' && form.closest('#revision-plano')) {
      event.preventDefault();event.stopImmediatePropagation();
      if (form.dataset.mcConfirming==='1' || !window.Swal) return;
      form.dataset.mcConfirming='1';
      try {
        const result=await Swal.fire({...opciones,title:'Rechazar plano',input:'textarea',
          inputLabel:'Motivo para TALLER',inputAttributes:{maxlength:'2000'},
          showCancelButton:true,cancelButtonText:'Cancelar',confirmButtonText:'Rechazar plano',
          inputValidator:value=>!value?.trim()?'Indique el motivo del rechazo.':undefined,
          allowOutsideClick:false});
        if(result.isConfirmed && form.isConnected) {
          form.querySelector('[name="motivo"]').value=result.value.trim();
          form.dataset.mcConfirmed='1';form.requestSubmit(submitter);
        }
      } finally {delete form.dataset.mcConfirming;}
      return;
    }
    if (!pregunta) return;
    event.preventDefault();event.stopImmediatePropagation();
    if (form.dataset.mcConfirming==='1') return;
    form.dataset.mcConfirming='1';
    try {
      const result=await api.confirmar(pregunta);
      if(result.isConfirmed && form.isConnected) {
        form.dataset.mcConfirmed='1';
        form.requestSubmit(submitter || undefined);
      }
    } finally {delete form.dataset.mcConfirming;}
  },true);
  document.addEventListener('submit',event=>{
    const form=event.target;
    const op=event.submitter?.name==='op'?event.submitter.value:form.querySelector('[name="op"]')?.value;
    if (!event.defaultPrevented && preguntaPara(form,op) && window.Swal) {
      Swal.fire({...opciones,title:'Guardando…',text:'Espera mientras se registra la operación.',allowOutsideClick:false,allowEscapeKey:false,showConfirmButton:false,didOpen:()=>Swal.showLoading()});
    }
  });
  document.addEventListener('masiscam:notify',event => api.mostrar(event.detail?.text || 'Operación completada.',event.detail?.icon || 'success'));
  const messages=[...document.querySelectorAll('[data-masiscam-message]')];
  if (messages.length && window.Swal) {
    const mensaje=messages.find(n=>/error|warning/.test(n.dataset.masiscamMessage)) || messages[0];
    // Leave security/account details inline; never copy secrets into a popup.
    if (!/contraseñ|password|token|clave|sha-?256/i.test(mensaje.textContent)) {
      mensaje.hidden=true;
      api.mostrar(mensaje.textContent.trim(),/error/.test(mensaje.dataset.masiscamMessage)?'error':/warning/.test(mensaje.dataset.masiscamMessage)?'warning':'success');
    }
  } else if(document.querySelector('form .errorlist,form .text-danger[role="alert"]')) {
    api.mostrar('Revisa los campos señalados. No se guardaron los cambios.','error');
  }
  // AJAX forms retain their inline errors and receive one accessible notification.
  const observer=new MutationObserver(records=>{
    for(const record of records) for(const node of record.addedNodes) {
      if(node.nodeType!==1) continue;
      const error=node.matches?.('.errorlist')?node:node.querySelector?.('.errorlist');
      if(error && !seen.has(error)) {seen.add(error);if(!window.Swal?.isVisible())api.mostrar('Revisa los campos señalados antes de continuar.','error');}
    }
  });
  observer.observe(document.body,{childList:true,subtree:true});
})();
