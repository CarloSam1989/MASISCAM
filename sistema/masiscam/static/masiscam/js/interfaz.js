(() => {
  'use strict';
  const main=document.querySelector('main');if(!main)return;
  const collator=new Intl.Collator('es',{numeric:true,sensitivity:'base'});
  let sequence=0;
  const node=(tag,text,attrs={})=>{const e=document.createElement(tag);if(text!==null)e.textContent=text;for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);return e;};
  // Existing secondary record lists keep their links, controls and no-JS fallback.
  main.querySelectorAll('ul[data-listado], [data-listado-tarjetas]').forEach(list=>{
    const cards=Array.from(list.children);const table=node('table',null,{'class':'table mc-table'});
    if(list.dataset.principal!==undefined)table.dataset.principal='';
    const taller=list.classList.contains('tw-work-grid')&&cards.some(c=>c.querySelector('dl'));
    const oficina=list.classList.contains('of-grid')&&cards.some(c=>c.querySelector('article')||c.matches('article'));
    const titles=taller?['Equipo','Serie','Cliente','Ubicación','Estado','Acciones']:oficina?['Expediente','Equipo','Cliente','Estado / detalle','Acciones']:[list.dataset.titulo||'Registro'];
    const head=table.createTHead().insertRow();titles.forEach(title=>head.append(node('th',title,{scope:'col'})));
    if(titles.length===1)table.dataset.listadoSimple='';
    const body=table.createTBody();for(const card of cards){
      const tr=body.insertRow();
      if(taller&&card.querySelector('dl')){
        const values=Array.from(card.querySelectorAll('dl dd'));
        for(const element of [card.querySelector('h3'),...values.slice(0,3),card.querySelector('.tw-status')]){const td=tr.insertCell();if(element)td.append(element);}
        const actions=tr.insertCell();card.querySelectorAll('a.btn,button').forEach(button=>actions.append(button));
      }else if(oficina&&card.matches('article')){
        const paragraphs=Array.from(card.querySelectorAll('p'));
        for(const element of [card.querySelector('.of-eyebrow'),card.querySelector('h2'),paragraphs[0],paragraphs[1]]){const td=tr.insertCell();if(element)td.append(element);}
        const actions=tr.insertCell();card.querySelectorAll('a.btn,button').forEach(button=>actions.append(button));
      }else{const td=tr.insertCell();if(titles.length>1)td.colSpan=titles.length;while(card.firstChild)td.append(card.firstChild);}
    }
    list.replaceWith(table);
  });
  let config=null;try{config=JSON.parse(document.querySelector('#mc-listado-config')?.textContent||'null');}catch{}
  const tables=Array.from(main.querySelectorAll('table')).filter(table=>table.tHead&&table.tBodies.length&&!table.closest('#pl-app,#oficina-cotizacion,[data-tabla-estatica]'));
  const primary=main.querySelector('table[data-principal]')||tables[0];
  for(const table of tables){
    if(table.dataset.mcReady)continue;table.dataset.mcReady='true';table.classList.add('mc-table');
    let wrap=table.parentElement;if(!wrap.classList.contains('table-responsive')){wrap=node('div',null,{class:'table-responsive'});table.before(wrap);wrap.append(table);}
    if(config&&table===primary){server(table,wrap,config);continue;}
    const rows=Array.from(table.tBodies[0].rows).filter(r=>!r.querySelector('[colspan]'));
    if(!rows.length)continue;
    const toolbar=node('div',null,{class:'mc-toolbar'}),id=`mc-search-${++sequence}`;
    const label=node('label','Buscar en este listado',{for:id}),search=node('input',null,{id,type:'search',placeholder:'Buscar registros…'});label.append(search);toolbar.append(label);
    const details=node('details'),summary=node('summary','Filtrar por columna'),filters=node('div'),column=node('select',null,{'aria-label':'Columna a filtrar'}),value=node('input',null,{type:'search','aria-label':'Valor del filtro',placeholder:'Valor del filtro'});
    const headings=Array.from(table.tHead.rows[table.tHead.rows.length-1].cells);
    headings.forEach((th,index)=>column.append(node('option',th.textContent.trim(),{value:String(index)})));
    filters.append(column,value);details.append(summary,filters);toolbar.append(details);wrap.before(toolbar);
    const pager=node('nav',null,{class:'mc-pagination','aria-label':'Páginas del listado'}),status=node('span',null,{'aria-live':'polite'}),buttons=node('div',null,{class:'mc-pages'}),prev=node('button','Anterior',{type:'button',class:'btn btn-outline-primary'}),next=node('button','Siguiente',{type:'button',class:'btn btn-outline-primary'}),position=node('span');buttons.append(prev,position,next);pager.append(status,buttons);wrap.after(pager);
    let page=1,sorted=rows.slice(),direction=1,sortColumn=-1;
    const text=row=>row.textContent.toLocaleLowerCase('es');
    function render(){const query=search.value.toLocaleLowerCase('es'),filter=value.value.toLocaleLowerCase('es');const matched=sorted.filter(row=>text(row).includes(query)&&(!filter||(row.cells[Number(column.value)]?.textContent||'').toLocaleLowerCase('es').includes(filter)));const pages=Math.max(1,Math.ceil(matched.length/10));page=Math.min(page,pages);rows.forEach(row=>row.hidden=true);matched.slice((page-1)*10,page*10).forEach(row=>row.hidden=false);status.textContent=`${matched.length} registros · 10 por página`;position.textContent=`${page} / ${pages}`;prev.disabled=page<=1;next.disabled=page>=pages;}
    for(const [index,th]of headings.entries()){
      if(th.colSpan>1||/acciones/i.test(th.textContent))continue;
      const button=node('button',th.textContent.trim(),{type:'button',class:'mc-sort','aria-label':`Ordenar por ${th.textContent.trim()}`});th.replaceChildren(button);
      button.addEventListener('click',()=>{direction=sortColumn===index?-direction:1;sortColumn=index;sorted.sort((a,b)=>direction*collator.compare(a.cells[index]?.textContent||'',b.cells[index]?.textContent||''));sorted.forEach(row=>table.tBodies[0].append(row));headings.forEach(h=>h.removeAttribute('aria-sort'));th.setAttribute('aria-sort',direction===1?'ascending':'descending');page=1;render();});
    }
    [search,value,column].forEach(input=>input.addEventListener('input',()=>{page=1;render();}));prev.addEventListener('click',()=>{page--;render();});next.addEventListener('click',()=>{page++;render();});render();
  }
  function server(table,wrap,cfg){
    // Server filtering/ordering always runs inside the authorized queryset.
    let form=Array.from(main.querySelectorAll('form')).find(f=>(f.method||'get').toLowerCase()==='get');
    if(!form){form=node('form',null,{method:'get',class:'mc-toolbar'});wrap.before(form);}
    form.classList.add('mc-toolbar');form.dataset.listadoServidor='true';
    const params=new URLSearchParams(location.search);
    if(!form.querySelector('[name=q]')){const label=node('label','Buscar registros');label.append(node('input',null,{type:'search',name:'q',value:params.get('q')||'',maxlength:'120'}));form.append(label);}
    const select=node('select',null,{name:'orden','aria-label':'Ordenar listado'});select.append(node('option','Orden original',{value:''}));for(const f of cfg.campos){select.append(node('option',`${f.label} ↑`,{value:f.clave}),node('option',`${f.label} ↓`,{value:'-'+f.clave}));}select.value=params.get('orden')||'';
    const label=node('label','Ordenar por');label.append(select);form.append(label);
    const details=node('details'),summary=node('summary','Filtros del listado'),fields=node('div'),filter=node('select',null,{name:'filtro','aria-label':'Campo del filtro'}),value=node('input',null,{name:'valor',value:params.get('valor')||'',maxlength:'120','aria-label':'Valor del filtro',placeholder:'Valor del filtro'});filter.append(node('option','Todos los campos',{value:''}));for(const f of cfg.campos)filter.append(node('option',f.label,{value:f.clave}));filter.value=params.get('filtro')||'';fields.append(filter,value);details.append(summary,fields);form.append(details);if(value.value)details.open=true;
    if(!form.querySelector('button'))form.append(node('button','Aplicar',{class:'btn btn-primary',type:'submit'}));
    for(const [name,v]of params)if(name!=='page'&&!form.querySelector(`[name="${CSS.escape(name)}"]`))form.append(node('input',null,{type:'hidden',name,value:v}));
    main.querySelectorAll('nav').forEach(nav=>{if(nav!==document.querySelector('#appMenu')&&Array.from(nav.querySelectorAll('a')).some(a=>new URL(a.href).searchParams.has('page')))nav.hidden=true;});
    const pager=node('nav',null,{class:'mc-pagination','aria-label':'Páginas del listado'});pager.append(node('span',`${cfg.total} registros · 10 por página`));const links=node('div',null,{class:'mc-pages'});
    function link(text,page){const url=new URL(location.href);url.searchParams.set('page',page);return node('a',text,{href:url.pathname+url.search,class:'btn btn-outline-primary'});}
    if(cfg.numero>1)links.append(link('Anterior',cfg.numero-1));links.append(node('span',`${cfg.numero} / ${cfg.paginas}`));if(cfg.numero<cfg.paginas)links.append(link('Siguiente',cfg.numero+1));pager.append(links);wrap.after(pager);
  }
})();

(()=>{const box=document.querySelector('.app-products');if(!box)return;
const btn=box.querySelector('.app-products-toggle'),list=box.querySelector('.app-products-list');
const place=()=>{if(window.innerWidth<1200){list.style.top=list.style.left='';return;}const r=btn.getBoundingClientRect();list.style.top=(r.bottom+6)+'px';list.style.left=Math.max(8,Math.min(r.left,window.innerWidth-list.offsetWidth-8))+'px';};
const set=open=>{list.hidden=!open;btn.setAttribute('aria-expanded',String(open));if(open)place();};
window.addEventListener('resize',()=>{if(!list.hidden)place();});window.addEventListener('scroll',()=>{if(!list.hidden)set(false);},true);
btn.addEventListener('click',e=>{e.stopPropagation();set(list.hidden);});list.addEventListener('click',e=>{if(e.target.closest('a'))set(false);});
document.addEventListener('click',e=>{if(!box.contains(e.target))set(false);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!list.hidden){set(false);btn.focus();}});
})();
(()=>{const form=document.querySelector('form[data-catalogo]');if(!form)return;
let cat=[];try{cat=JSON.parse(form.dataset.catalogo||'[]');}catch{return;}
const f=n=>form.querySelector('#id_'+n),dl=n=>document.getElementById('cat-'+n),estado=document.getElementById('cat-estado');
const norm=v=>(v||'').trim().toLowerCase(),uniq=a=>[...new Set(a)].filter(Boolean);
const igual=(o,campo)=>!norm(f(campo)?.value)||norm(o[campo])===norm(f(campo).value);
const llenar=(n,valores)=>{const l=dl(n);if(!l)return;l.replaceChildren(...uniq(valores).map(v=>{const o=document.createElement('option');o.value=v;return o;}));};
const refrescar=()=>{
  const m=cat.filter(o=>igual(o,'marca')),mo=m.filter(o=>igual(o,'modelo')),r=mo.filter(o=>igual(o,'ratio')),ro=r.filter(o=>igual(o,'rotacion'));
  llenar('marca',cat.map(o=>o.marca));llenar('modelo',m.map(o=>o.modelo));llenar('ratio',mo.map(o=>o.ratio));
  llenar('rotacion',r.map(o=>o.rotacion));llenar('operacion',ro.map(o=>o.operacion));
  if(!estado)return;
  const completo=['marca','modelo','ratio','rotacion'].every(n=>norm(f(n)?.value));
  const sinCat=form.querySelector('#id_sin_catalogo')?.checked;
  const coincide=completo&&ro.some(o=>norm(o.operacion)===norm(f('operacion')?.value));
  estado.textContent=sinCat?'Se registrará sin plano de Planos y piezas.':!completo?'Elija marca, modelo, ratio y rotación del catálogo: el plano y sus piezas se vinculan solos. Si no está en el catálogo, marque la casilla inferior para registrar sin plano.'
    :coincide?'Catálogo activo encontrado: el plano y sus piezas se vincularán al reductor al guardarlo.'
    :'No hay un catálogo activo con esta configuración: elíjala de la lista o marque que no está en Planos y piezas.';
  vistaPrevia(completo&&coincide&&!sinCat);
};
let vistaClave='',vistaSeq=0;
const vistaPrevia=activa=>{
  const caja=document.getElementById('cat-vista'),url=form.dataset.vistaPrevia;if(!caja||!url)return;
  if(!activa){vistaClave='';caja.replaceChildren();return;}
  const q=new URLSearchParams();['marca','modelo','ratio','rotacion','operacion'].forEach(n=>q.set(n,(f(n)?.value||'').trim()));
  const clave=q.toString();if(clave===vistaClave)return;vistaClave=clave;const mia=++vistaSeq;
  fetch(url+'?'+clave,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}}).then(r=>r.ok?r.json():{candidatos:[]}).then(d=>{
    if(mia!==vistaSeq)return;caja.replaceChildren();
    const lista=d.candidatos||[];
    if(!lista.length){caja.textContent='No hay un catálogo activo con esta configuración.';return;}
    if(lista.length>1){const aviso=document.createElement('p');aviso.className='text-warning-emphasis small mb-2';aviso.textContent='Hay '+lista.length+' catálogos activos con esta configuración: tras guardar, elija la revisión en la ficha del reductor.';caja.append(aviso);}
    lista.forEach(c=>{const bloque=document.createElement('div');bloque.className='border rounded p-2 mb-2';
      const t=document.createElement('p');t.className='mb-2 small';t.textContent=c.codigo+' · rev. '+c.revision+' · '+c.piezas+' piezas · '+c.marcadores+' marcadores';
      const abrir=document.createElement('a');abrir.href=c.pdf;abrir.target='_blank';abrir.rel='noopener';abrir.className='btn btn-sm btn-outline-primary ms-2';abrir.textContent='Abrir PDF';t.append(abrir);
      const fr=document.createElement('iframe');fr.src=c.pdf;fr.title='Vista previa del plano '+c.codigo;fr.style.cssText='width:100%;height:320px;border:0';
      bloque.append(t,fr);caja.append(bloque);});
  }).catch(()=>{if(mia===vistaSeq)caja.textContent='No se pudo cargar la vista previa; el plano se vinculará al guardar.';});
};
['marca','modelo','ratio','rotacion','operacion'].forEach(n=>f(n)?.addEventListener('input',refrescar));form.querySelector('#id_sin_catalogo')?.addEventListener('change',refrescar);refrescar();
})();
