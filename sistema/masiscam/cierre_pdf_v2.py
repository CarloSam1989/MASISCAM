"""Informe final de mantenimiento (final-v2): portada con contenido, identificación, diagnóstico, cotización, mantenimiento, calibración y trazabilidad;
la hoja de calibración original se incorpora sin cambios como tercer documento y las fotografías de la intervención actual van como anexos visuales numerados.
Lo histórico (documentos anteriores, recepciones y usos de repuestos) se separa y se rotula; nada se inventa: lo que falta se marca en ámbar."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

MARINO,ACERO,SUAVE,LINEA='#16283d','#315d87','#eef3f7','#c9d6e2'
GRIS,AMBAR,PEND_BG,PEND_TX='#5b6b7b','#e07b00','#fff4d6','#8a5a00'
ETIQUETAS=dict(cliente='Cliente',ruc='RUC',empresa='Empresa',equipo='Equipo',tipo_producto='Tipo de equipo',marca='Marca',modelo='Modelo',serie='N.º de serie',
    ratio='Ratio',potencia='Potencia (HP)',rpm='RPM',service_factor='Factor de servicio',rotacion='Rotación',tipo_aceite='Tipo de aceite',fabricante='Fabricante',
    ubicacion='Ubicación',sector='Sector',fecha='Fecha',responsable='Responsable',condicion='Condición de ingreso',observaciones='Observaciones',guia='Guía de remisión',
    estado_fisico='Estado físico',condiciones_generales='Condiciones generales',hallazgos='Hallazgos',motivo_sin_evidencia='Motivo si no hay evidencia',
    fallas='Fallas',causas='Causas',componentes_afectados='Componentes afectados',recomendaciones='Recomendaciones',conclusiones='Conclusiones',
    repuestos='Repuestos requeridos',placa='Placa',motivo_sin_placa='Motivo sin placa',motivo_sin_guia='Motivo sin guía',politica='Política aplicada',
    referencia='N.º de cotización',alcance='Alcance',version='Versión',decision='Decisión comercial',fecha_cliente='Fecha de respuesta del cliente')


def _fecha(valor):
    """Fecha legible en la zona horaria del sistema; None si no hay dato."""
    from datetime import datetime
    if valor in (None,''): return None
    texto=str(valor)
    try: d=datetime.fromisoformat(texto)
    except ValueError: return texto
    if d.tzinfo:
        from django.utils import timezone
        d=timezone.localtime(d)
    return d.strftime('%d/%m/%Y %H:%M') if ':' in texto else d.strftime('%d/%m/%Y')


def _estilos():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER,TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    c=colors.HexColor
    base=ParagraphStyle('base',fontName='Helvetica',fontSize=8.5,leading=11,textColor=c(MARINO))
    return dict(base=base,etq=ParagraphStyle('etq',parent=base,fontName='Helvetica-Bold',fontSize=7,leading=9,textColor=c(GRIS)),
        pend=ParagraphStyle('pend',parent=base,fontName='Helvetica-Oblique',textColor=c(PEND_TX)),nota=ParagraphStyle('nota',parent=base,fontSize=7.5,leading=10,textColor=c(GRIS)),
        barra=ParagraphStyle('barra',parent=base,fontName='Helvetica-Bold',fontSize=9,leading=11,textColor=colors.white),
        der=ParagraphStyle('der',parent=base,fontName='Helvetica-Bold',fontSize=9,leading=11,textColor=colors.white,alignment=TA_RIGHT),
        mono=ParagraphStyle('mono',parent=base,fontName='Courier',fontSize=6.3,leading=8,textColor=c(GRIS)),
        centro=ParagraphStyle('centro',parent=base,alignment=TA_CENTER),
        titulo=ParagraphStyle('titulo',parent=base,fontName='Helvetica-Bold',fontSize=21,leading=26,textColor=colors.white),
        sub=ParagraphStyle('sub',parent=base,fontSize=10.5,leading=14,textColor=colors.white),
        sub2=ParagraphStyle('sub2',parent=base,fontName='Helvetica-Bold',fontSize=8,leading=10,textColor=c(GRIS),spaceBefore=3))


def construir_informe(s,fotografias=(),documento_calibracion=None):
    """Devuelve el PDF completo: cuerpo del informe + hoja de calibración original + anexos visuales (dos pasadas para numerar y paginar)."""
    from pypdf import PdfReader,PdfWriter
    hoja=PdfReader(BytesIO(documento_calibracion)) if documento_calibracion else None
    k=len(hoja.pages) if hoja else 0
    marcas={};cuenta=dict(a=1,b=0)
    for paso in (1,2):
        total=cuenta['a']+k+cuenta['b'] if paso==2 else 0
        if paso==2: marcas.update(_cal=cuenta['a']+1,_anexos=cuenta['a']+k+1)   # páginas absolutas para el contenido de la portada
        cuerpo,cuenta['a'],_=_parte(s,fotografias,k,marcas,paso==2,1,total,anexos=False)
        anexos,cuenta['b'],_=_parte(s,fotografias,k,marcas,paso==2,cuenta['a']+k+1,total,anexos=True)
    salida=PdfWriter()
    for pagina in PdfReader(BytesIO(cuerpo)).pages: salida.add_page(pagina)
    if hoja:
        sellos=PdfReader(BytesIO(_sellos(s,cuenta['a'],k,cuenta['a']+k+cuenta['b'])))
        for i,pagina in enumerate(hoja.pages):
            pagina.merge_page(sellos.pages[i]);salida.add_page(pagina)
    if cuenta['b']:
        for pagina in PdfReader(BytesIO(anexos)).pages: salida.add_page(pagina)
    salida.add_outline_item('Informe final de mantenimiento',0)
    if hoja: salida.add_outline_item('Hoja de calibración (tercer documento, sin cambios)',cuenta['a'])
    if cuenta['b']: salida.add_outline_item('Anexos visuales',cuenta['a']+k)
    salida.add_metadata({'/Title':f"Informe final de mantenimiento {s['numero']} v{s['version']}",'/Author':'MASISCAM'})
    flujo=BytesIO();salida.write(flujo);return flujo.getvalue()


def _sellos(s,primera,k,total):
    """Numeración continua en el margen inferior de las páginas de la hoja incorporada, sin alterar su contenido."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    flujo=BytesIO();c=Canvas(flujo,pagesize=A4)
    for i in range(k):
        c.setFont('Helvetica',6.3);c.setFillColorRGB(.36,.42,.48)
        c.drawCentredString(A4[0]/2,14,f"{s['numero']} · Informe final v{s['version']} · hoja de calibración incorporada sin cambios · Página {primera+i+1} de {total}");c.showPage()
    c.save();return flujo.getvalue()


def _parte(s,fotografias,k,marcas,definitivo,inicio,total,anexos):
    """Una parte del informe (cuerpo o anexos). Devuelve (pdf, páginas, None). `inicio` es el número absoluto de su primera página."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as lienzos
    from reportlab.platypus import (BaseDocTemplate,PageTemplate,Frame,Paragraph,Table,TableStyle,Spacer,KeepTogether,PageBreak,CondPageBreak,Flowable)
    from .calibracion_pdf import _imagen_anexo
    width,height=A4;margen=32;W=width-2*margen
    C=colors.HexColor;E=_estilos();base,etq,pend,nota,barra,mono=E['base'],E['etq'],E['pend'],E['nota'],E['barra'],E['mono']
    def esc(v): return escape(str(v)).replace('\n','<br/>')
    def dato(v,estilo=None,vacio='No documentado'):
        """Celda de valor: lo que falta se ve en ámbar, sin completarse."""
        if isinstance(v,bool): v='Sí' if v else 'No'
        if v in (None,'',[]): return Paragraph(vacio,pend),True
        return Paragraph(esc(v),estilo or base),False
    def tabla(filas,anchos,extra=(),cabecera=False,padding=3):
        datos=[];fondos=[]
        for i,fila in enumerate(filas):
            fila_out=[]
            for j,celda in enumerate(fila):
                contenido,falta=celda if isinstance(celda,tuple) else (celda,False)
                if falta: fondos.append(('BACKGROUND',(j,i),(j,i),C(PEND_BG)))
                fila_out.append(contenido)
            datos.append(fila_out)
        t=Table(datos,colWidths=anchos,hAlign='LEFT',repeatRows=1 if cabecera else 0,splitInRow=1)
        estilo=[('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,C(LINEA)),('TOPPADDING',(0,0),(-1,-1),padding),('BOTTOMPADDING',(0,0),(-1,-1),padding),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),*fondos,*extra]
        if cabecera: estilo.append(('BACKGROUND',(0,0),(-1,0),C(SUAVE)))
        t.setStyle(TableStyle(estilo));return t
    def kv(pares,etiquetas=.26):
        """Tabla de dos columnas etiqueta/valor; las etiquetas se sombrean y lo faltante se resalta."""
        filas=[[Paragraph(esc(a),etq),dato(b)] for a,b in pares]
        return tabla(filas,[W*etiquetas,W*(1-etiquetas)],[('BACKGROUND',(0,0),(0,-1),C(SUAVE))])
    def kv4(pares):
        filas=[]
        for i in range(0,len(pares),2):
            par=pares[i:i+2]+[('','')]*(2-len(pares[i:i+2]))
            filas.append([Paragraph(esc(par[0][0]),etq),dato(par[0][1]),Paragraph(esc(par[1][0]),etq),dato(par[1][1]) if par[1][0] else ''])
        return tabla(filas,[W*.17,W*.33,W*.17,W*.33],[('BACKGROUND',(0,0),(0,-1),C(SUAVE)),('BACKGROUND',(2,0),(2,-1),C(SUAVE))],padding=2.5)
    def cabecera_tabla(textos): return [Paragraph(esc(t),etq) for t in textos]
    class Marca(Flowable):
        """Registra en qué página cae cada sección para el contenido de la portada."""
        def __init__(self,clave): super().__init__();self.clave=clave;self.keepWithNext=True   # cae en la misma página que su título
        def wrap(self,aw,ah): return 0,0
        def draw(self): marcas[self.clave]=self.canv.getPageNumber()
    def seccion(titulo,clave=None,keep=True):
        t=Table([[Paragraph(esc(titulo),barra)]],colWidths=[W],hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),C(MARINO)),('LINEBEFORE',(0,0),(0,-1),4,C(AMBAR)),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),9)]))
        t.keepWithNext=keep
        return [Marca(clave),t] if clave else [t]
    def subtitulo(texto): return Paragraph(esc(texto.upper()),E['sub2'])
    def aviso(texto,fondo=SUAVE,borde=ACERO):
        t=Table([[Paragraph(esc(texto),nota)]],colWidths=[W],hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),C(fondo)),('LINEBEFORE',(0,0),(0,-1),2.5,C(borde)),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),8)]))
        return t
    logo=Path(__file__).parent/'static/masiscam/img/logo_transparente.png'
    def marco(c,pagina,total_paginas):
        c.saveState()
        if logo.exists(): c.drawImage(ImageReader(str(logo)),margen,height-70,width=135,height=46,preserveAspectRatio=True,mask='auto')
        c.setFillColor(C(MARINO));c.setFont('Helvetica-Bold',12);c.drawRightString(width-margen,height-38,'ANEXOS VISUALES' if anexos else 'INFORME FINAL DE MANTENIMIENTO')
        c.setFillColor(C(GRIS));c.setFont('Helvetica',8);c.drawRightString(width-margen,height-52,f"{s['numero']}  ·  Versión {s['version']}")
        c.setStrokeColor(C(MARINO));c.setLineWidth(1.6);c.line(margen,height-77,width-margen,height-77)
        c.setStrokeColor(C(AMBAR));c.setLineWidth(3.2);c.line(margen,height-77,margen+78,height-77)
        c.setStrokeColor(C(LINEA));c.setLineWidth(.6);c.line(margen,40,width-margen,40)
        c.setFillColor(C(GRIS));c.setFont('Helvetica',6.8)
        c.drawString(margen,28,'MASISCAM | Documento privado. Aprobaciones y cierre en la trazabilidad del expediente; no incluye firma del cliente.')
        c.drawRightString(width-margen,28,f'Página {inicio+pagina-1} de {total}' if total else f'Página {inicio+pagina-1}')
        c.restoreState()
    class Lienzo(lienzos.Canvas):
        def __init__(self,*a,**kw): super().__init__(*a,**kw);self._paginas=[]
        def showPage(self): self._paginas.append(dict(self.__dict__));self._startPage()
        def save(self):
            for pagina in self._paginas:
                self.__dict__.update(pagina);marco(self,self._pageNumber,len(self._paginas));super().showPage()
            super().save()
    def construir(historia):
        flujo=BytesIO();doc=BaseDocTemplate(flujo,pagesize=A4,title='Informe final de mantenimiento MASISCAM',author='MASISCAM')
        doc.addPageTemplates([PageTemplate(id='h',frames=[Frame(margen,46,W,height-46-82,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)])])
        doc.build(historia,canvasmaker=Lienzo)
        return flujo.getvalue(),len(PdfReader(BytesIO(flujo.getvalue())).pages),None
    from pypdf import PdfReader

    # ---------------------------------------------------------------- datos
    rec=s['recepcion'];tec=s['tecnico'];cal=s['calibracion'];evid=s.get('evidencias',[]);docs=s.get('anexos',[])
    es_hoja=cal.get('plantilla')=='hoja-calibracion-v1'
    incorporada=next((d for d in docs if d.get('incorporado')),None)
    fotos={item['documento']:data for item,data in fotografias}
    anexos_visuales=[e for e in evid if e.get('foto') and e.get('origen','ACTUAL')=='ACTUAL']
    if anexos:
        if not anexos_visuales: return b'',0,None
        historia=[]
        historia += seccion('ANEXOS VISUALES — EVIDENCIA DE LA INTERVENCIÓN ACTUAL')
        historia += [Spacer(1,3),Paragraph('Fotografías de recepción, diagnóstico y mantenimiento de esta intervención, numeradas en el orden del informe. Cada fotografía se incluye una sola vez y conserva la huella SHA-256 del documento original; las fotografías de la hoja de calibración van dentro de ella.',nota),Spacer(1,6)]
        grupo=None
        for n,item in enumerate(anexos_visuales,1):
            titulo_grupo=[]
            if item['seccion']!=grupo:   # el rótulo del grupo viaja con su primera fotografía
                grupo=item['seccion'];titulo_grupo=[subtitulo(grupo),Spacer(1,2)]
            cab=Table([[Paragraph(f'ANEXO VISUAL {n}',barra),Paragraph(esc(item['seccion'].upper()),E['der'])]],colWidths=[W*.5,W*.5],hAlign='LEFT')
            cab.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),C(ACERO)),('LINEBEFORE',(0,0),(0,-1),4,C(AMBAR)),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),4),
                ('LEFTPADDING',(0,0),(0,0),9),('RIGHTPADDING',(-1,0),(-1,0),8)]))
            leyenda=str(item.get('leyenda') or '').strip();descripcion=(leyenda[:240]+'…') if len(leyenda)>240 else (leyenda or 'Sin descripción registrada')
            img=_imagen_anexo(fotos[item['documento']],W-16,215) if item['documento'] in fotos else None
            if img is None:
                img=Table([[Paragraph('Imagen no legible en este PDF; consulte el documento original en el expediente.',E['centro'])]],colWidths=[W-16],rowHeights=[40])
                img.setStyle(TableStyle([('BOX',(0,0),(-1,-1),.5,C(LINEA)),('BACKGROUND',(0,0),(-1,-1),C(PEND_BG)),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
            otras=[f'También usada en: {", ".join(item["tambien_en"])}'] if item.get('tambien_en') else []
            cuerpo=Table([[Paragraph('Descripción',etq)],[Paragraph(esc(descripcion),base)],[img],[Paragraph(esc(f"Documento {item['documento']} · SHA-256 {item['hash']}"),mono)],
                *[[Paragraph(esc(x),nota)] for x in otras]],colWidths=[W],hAlign='LEFT')
            cuerpo.setStyle(TableStyle([('BOX',(0,0),(-1,-1),.5,C(LINEA)),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),3),
                ('BOTTOMPADDING',(0,0),(-1,-1),3),('ALIGN',(0,2),(0,2),'CENTER'),('TOPPADDING',(0,2),(0,2),5),('BOTTOMPADDING',(0,2),(0,2),5)]))
            historia += [KeepTogether(titulo_grupo+[cab,cuerpo]),Spacer(1,9)]
        return construir(historia)

    # ---------------------------------------------------------------- portada
    h=[]
    hero=Table([[Paragraph('INFORME FINAL DE MANTENIMIENTO',E['titulo'])],[Paragraph(esc(f"{s['numero']}  ·  Versión documental {s['version']}"),E['sub'])]],colWidths=[W],hAlign='LEFT')
    hero.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),C(MARINO)),('LINEBEFORE',(0,0),(0,-1),6,C(AMBAR)),('TOPPADDING',(0,0),(0,0),16),('BOTTOMPADDING',(0,-1),(-1,-1),14),
        ('LEFTPADDING',(0,0),(-1,-1),16)]))
    h += [hero,Spacer(1,10)]
    if s.get('demostrativo'): h += [aviso('DEMOSTRATIVO - DATOS FICTICIOS QA. SIN VALIDEZ INDUSTRIAL.',PEND_BG,AMBAR),Spacer(1,6)]
    h += [kv4([('Cliente',rec.get('cliente')),('Equipo',rec.get('equipo')),('Marca',rec.get('marca')),('Modelo',rec.get('modelo')),('N.º de serie',rec.get('serie')),
            ('Tipo de equipo',rec.get('tipo_producto')),('Finalización técnica',_fecha(s.get('fecha_finalizacion'))),('Estado del informe','Pendiente de revisión independiente')]),Spacer(1,10)]
    def pagina(clave): return str(marcas.get(clave,'—')) if definitivo else '—'
    contenido=[('1','Identificación del cliente y del equipo','ident'),('2','Inspección, diagnóstico y piezas','diag'),('3','Cotización y autorización','cot'),
        ('4','Mantenimiento realizado','mant'),('5','Hoja de calibración (elaborada por TALLER)' if es_hoja else 'Calibración y revisión técnica','cal'),('6','Trazabilidad y documentos','traza')]
    filas=[cabecera_tabla(['N.º','Contenido','Página'])]+[[Paragraph(n,base),Paragraph(esc(t),base),Paragraph(pagina(c),base)] for n,t,c in contenido]
    if k:
        filas.append([Paragraph('—',base),Paragraph('Hoja de calibración original, incorporada sin cambios (tercer documento)',base),Paragraph(f"{marcas.get('_cal','—') if definitivo else '—'}",base)])
    anexo_paginas=len(anexos_visuales)
    if not anexo_paginas: filas.append([Paragraph('—',base),Paragraph('Anexos visuales: sin fotografías de la intervención actual registradas',base),Paragraph('—',base)])
    else: filas.append([Paragraph('—',base),Paragraph(f'Anexos visuales: {anexo_paginas} fotografía(s) numeradas (Anexo visual 1 a {anexo_paginas})',base),Paragraph(str(marcas.get('_anexos','—')) if definitivo else '—',base)])
    h += [subtitulo('Contenido'),Spacer(1,2),tabla(filas,[W*.09,W*.77,W*.14],cabecera=True),Spacer(1,10),
        aviso('Contenido congelado para revisión técnica independiente. La emisión del PDF no confirma el cierre administrativo. '+str(s.get('aviso',''))),Spacer(1,12)]

    # ---------------------------------------------------------------- 1. identificación
    pares=[(ETIQUETAS[k_],rec.get(k_)) for k_ in ('cliente','ruc','equipo','tipo_producto','marca','modelo','serie','ratio','potencia','rpm','service_factor','rotacion',
        'tipo_aceite','fabricante','ubicacion','sector')]
    h += seccion('1 · IDENTIFICACIÓN DEL CLIENTE Y DEL EQUIPO','ident')+[kv4(pares),Spacer(1,4)]
    pendientes=rec.get('datos_tecnicos_pendientes') or []
    if pendientes: h += [aviso('Datos técnicos no registrados en el equipo (no se completaron): '+', '.join(ETIQUETAS.get(p,p.replace('_',' ').capitalize()) for p in pendientes),PEND_BG,AMBAR),Spacer(1,4)]
    h += [subtitulo('Recepción del equipo'),Spacer(1,2),kv([('Fecha de ingreso',_fecha(rec.get('fecha'))),('Responsable del ingreso',rec.get('responsable')),
        ('Condición de ingreso',rec.get('condicion')),('Observaciones',rec.get('observaciones')),('Guía de remisión',rec.get('guia'))]),Spacer(1,4)]
    conf=s.get('confirmacion',{})
    if conf.get('estado'): h += [aviso(conf['estado'],PEND_BG,AMBAR)]
    else:
        h += [subtitulo('Confirmación de la recepción'),Spacer(1,2),kv([('Placa',conf.get('placa')),('Motivo sin placa',conf.get('motivo_sin_placa') or 'No aplica'),
            ('Motivo sin guía',conf.get('motivo_sin_guia') or 'No aplica'),('Confirmado por',conf.get('responsable')),('Fecha de confirmación',_fecha(conf.get('fecha')))])]
    h.append(Spacer(1,8))

    # ---------------------------------------------------------------- 2. inspección, diagnóstico y piezas
    ins,dia=tec['inspeccion'],tec['diagnostico']
    h += seccion('2 · INSPECCIÓN, DIAGNÓSTICO Y PIEZAS','diag')
    h += [subtitulo('Inspección técnica'),Spacer(1,2),kv([(ETIQUETAS[k_],_fecha(v) if k_=='fecha' else v) for k_,v in ins.items() if v or k_!='motivo_sin_evidencia']),Spacer(1,5)]
    h += [subtitulo('Diagnóstico aprobado y conclusiones'),Spacer(1,2),kv([(ETIQUETAS[k_],_fecha(v) if k_=='fecha' else v) for k_,v in dia.items() if v or k_!='motivo_sin_evidencia']),Spacer(1,3),
        Paragraph(esc(f"Técnico: {tec['tecnico']}  ·  Revisión independiente: {tec['revisor']}  ·  Fecha de aprobación: {_fecha(tec['decision_fecha']) or 'No documentada'}"),nota),Spacer(1,5)]
    piezas=tec['piezas']
    def referencia(pieza):
        fuente=pieza['referencia']
        if not s.get('flujo_operativo'): return str(fuente)
        if pieza.get('origen')=='MANUAL': return 'Registro manual documentado · '+str(fuente.get('contexto',''))
        return f"{fuente.get('referencia','Referencia')} · revisión {fuente.get('revision','pendiente')} · posición {fuente.get('posicion','pendiente')} · mapa v{fuente.get('version','sin configurar')}"
    h.append(subtitulo('Planos y piezas del diagnóstico'));h.append(Spacer(1,2))
    if piezas:
        h.append(tabla([cabecera_tabla(['Pieza / parte','Cantidad / unidad','Condición / acción','Referencia'])]+[[Paragraph(esc(f"{r['descripcion']} / {r['numero_parte']}"),base),
            Paragraph(esc(f"{r['cantidad']} {r['unidad'] or 'Unidad pendiente justificada'}"),base),Paragraph(esc(f"{r['condicion']} / {r['accion']}"),base),Paragraph(esc(referencia(r)),nota)] for r in piezas],
            [W*.32,W*.16,W*.22,W*.30],cabecera=True))
    else: h.append(Paragraph('Diagnóstico aprobado sin repuestos requeridos.',base))
    h += [Spacer(1,3),aviso('La lista de piezas del diagnóstico es informativa: no se registran recepciones de repuestos en esta intervención y no condicionó el mantenimiento.')]
    citados=[e['referencia'] for e in tec['evidencias'] if e.get('referencia')]
    if citados:
        h += [Spacer(1,4),tabla([cabecera_tabla(['Plano / manual citado','Revisión','Tipo','Versión del fabricante'])]+[[Paragraph(esc(f"{r['codigo']} · {r['titulo']}"),base),Paragraph(esc(r['revision']),base),
            Paragraph(esc(r['tipo']),base),dato(r['version_fabricante'])] for r in citados],[W*.46,W*.14,W*.2,W*.2],cabecera=True)]
    elif not any(p.get('origen')=='BIBLIOTECA' for p in piezas):
        h += [Spacer(1,4),Paragraph('No hay planos o manuales citados en esta revisión; no se presume su existencia ni aplicabilidad.',nota)]
    h.append(Spacer(1,8))

    # ---------------------------------------------------------------- 3. cotización
    h += seccion('3 · COTIZACIÓN Y AUTORIZACIÓN','cot')
    cot=s.get('cotizacion')
    if cot:
        h.append(kv([(ETIQUETAS['referencia'],cot.get('referencia')),(ETIQUETAS['alcance'],cot.get('alcance')),(ETIQUETAS['version'],cot.get('version')),
            (ETIQUETAS['decision'],cot.get('decision')),(ETIQUETAS['fecha_cliente'],_fecha(cot.get('fecha_cliente'))),('Registrada por',cot.get('responsable'))]))
        for sol in s.get('flujo_operativo') or []:
            h += [Spacer(1,3),Paragraph(esc(f"Solicitud v{sol['version']} · SHA-256 {sol['huella']}"),mono)]
    else: h += [Spacer(1,3),Paragraph('Esta versión no incluye datos de cotización; no se presumen.',nota)]
    aut=s['autorizacion']
    h += [Spacer(1,4),Paragraph(esc(f"Aprobación conjunta de ADMINISTRADOR (autoriza el mantenimiento): {aut['responsable']} · {_fecha(aut['fecha']) or 'fecha no documentada'}."),base),Spacer(1,8)]

    # ---------------------------------------------------------------- 4. mantenimiento
    h += seccion('4 · MANTENIMIENTO REALIZADO','mant')
    if not s['reparaciones']: h += [Spacer(1,3),Paragraph('No hay actividades de mantenimiento registradas.',pend)]
    for n,a in enumerate(s['reparaciones'],1):
        ap=a['aprobacion']
        h += [KeepTogether([subtitulo(f'Actividad {n}'),Spacer(1,2),kv([('Técnico responsable',a['autor']),('Inicio',_fecha(a['inicio'])),('Fin',_fecha(a['fin'])),('Trabajo',a['trabajo']),
            ('Componentes',a['componentes']),('Resultado',a['resultado']),('Trabajo realizado',a['trabajo_realizado']),('Observaciones',a['observaciones']),
            ('Autorización',f"{ap['responsable']} · {_fecha(ap['fecha']) or ''} · {ap['motivo']}")])]),Spacer(1,6)]
    h.append(Spacer(1,2))

    # ---------------------------------------------------------------- 5. calibración
    h += seccion('5 · HOJA DE CALIBRACIÓN (ELABORADA POR TALLER)' if es_hoja else '5 · CALIBRACIÓN Y REVISIÓN TÉCNICA','cal')
    if es_hoja:
        h.append(kv([('Documento','Hoja de calibración elaborada y generada por TALLER; no requiere aprobación de ADMINISTRADOR'),('Ficha técnica',f"{cal['protocolo']} v{cal['protocolo_version']}"),
            ('Técnico responsable',cal.get('tecnico_nombre') or cal['tecnico']),('Fecha de la hoja',_fecha(cal.get('fecha'))),('Revisión',f"{cal['revision']}"+(f" · motivo: {cal['motivo_revision']}" if cal.get('motivo_revision') else '')),
            ('Unidades',', '.join(f'{g}: {u}' for g,u in (cal.get('unidades') or {}).items()) or None),('Verificación',cal['verificacion']['texto'])]))
        for advertencia in cal.get('advertencias',[]): h += [Spacer(1,3),aviso(advertencia,PEND_BG,AMBAR)]
    else:
        dec=cal.get('decision') or {}
        h.append(kv([('Protocolo',f"{cal['protocolo']} v{cal['protocolo_version']}"),('Huella del protocolo',cal['protocolo_huella']),('Técnico',cal['tecnico']),('Fecha',_fecha(cal.get('fecha'))),
            ('Instrumentos',cal.get('instrumentos')),('Resultados',cal.get('resultados')),('Observaciones',cal.get('observaciones')),('Estado',cal.get('estado')),
            ('Revisión técnica independiente',f"{dec.get('responsable','')} · {_fecha(dec.get('fecha')) or ''} · {dec.get('motivo','')}" if dec else None)]))
    if incorporada:
        h += [Spacer(1,4),aviso(f"La hoja original ({incorporada['titulo']}, documento {incorporada['documento']}) se incorpora sin cambios como tercer documento, a continuación de la trazabilidad "
            f"(páginas {marcas.get('_cal','—') if definitivo else '—'} a {(marcas.get('_cal',0)+k-1) if definitivo and marcas.get('_cal') else '—'}). SHA-256 {incorporada['hash']}. Sus anexos conservan su numeración propia.")]
    if es_hoja and not incorporada:   # sin el PDF de la hoja, sus valores no se pierden
        h += [Spacer(1,4),tabla([cabecera_tabla(['N.º','Valor / unidad','Verificación'])]+[[Paragraph(str(n),base),Paragraph(esc(f"{r['valor']} {r.get('unidad') or ''}".strip()) if r['valor'] is not None else 'Pendiente',base),
            Paragraph(esc(r['evaluacion']),base)] for n,r in enumerate(cal['mediciones'],1)],[W*.12,W*.33,W*.55],cabecera=True)]
    if not es_hoja:   # calibraciones con protocolo aprobado (flujo anterior): puntos con su código, criterios y parámetros
        def medida(r): return Paragraph(esc('Pendiente' if r['valor'] is None else f"{r['valor']} {r.get('unidad') or ''}".strip()),base)
        h += [Spacer(1,4),tabla([cabecera_tabla(['Punto','Valor / unidad','Mín. / Máx.','Tolerancia','Evaluación'])]+[[[Paragraph(esc(f"{r['nombre']} ({r['codigo']})"),base),
            Paragraph(esc(f"Fuente: {r.get('fuente','')} · Criterio: {r.get('criterio') or 'Pendiente'}"),nota)],medida(r),Paragraph(esc(f"{r.get('minimo','No definido')} / {r.get('maximo','No definido')}"),base),
            Paragraph(esc(r.get('tolerancia') or 'No definida'),base),Paragraph(esc(r['evaluacion']),base)] for r in cal['mediciones']],[W*.31,W*.17,W*.17,W*.17,W*.18],cabecera=True)]
        if cal.get('parametros'):
            h += [Spacer(1,4),tabla([cabecera_tabla(['Parámetro','Valor / unidad','Evaluación'])]+[[Paragraph(esc(r['nombre']),base),medida(r),Paragraph(esc(r.get('evaluacion') or ''),base)] for r in cal['parametros']],[W*.4,W*.25,W*.35],cabecera=True)]
    h += [Spacer(1,4),Paragraph('Las evaluaciones no certifican instrumentos ni afirman conformidad técnica sin criterios aprobados.',nota),Spacer(1,8)]

    # ---------------------------------------------------------------- 6. trazabilidad
    h += seccion('6 · TRAZABILIDAD Y DOCUMENTOS','traza')
    h += [kv([('Aprobación conjunta de ADMINISTRADOR',f"{aut['responsable']} · {_fecha(aut['fecha']) or ''}"),('Técnico del diagnóstico',tec['tecnico']),('Revisión independiente del diagnóstico',tec['revisor']),
        ('Técnico de la calibración',cal.get('tecnico_nombre') or cal['tecnico'])]),Spacer(1,3),
        Paragraph('La aprobación de este informe y el cierre administrativo constan en los registros inmutables de esta versión y su SHA-256; este PDF se emite antes de esas decisiones y no las simula.',nota),Spacer(1,5)]
    def tabla_docs(lista):
        return tabla([cabecera_tabla(['Documento','ID','SHA-256'])]+[[Paragraph(esc(d['titulo']+(' (incorporada en este PDF)' if d.get('incorporado') else '')),base),Paragraph(str(d['documento']),base),Paragraph(esc(d['hash']),mono)] for d in lista],
            [W*.3,W*.07,W*.63],cabecera=True)
    actuales=[d for d in docs if d.get('origen','ACTUAL')=='ACTUAL'];historicos=[d for d in docs if d.get('origen')=='HISTORICO']
    h += [subtitulo('Documentos de la intervención actual'),Spacer(1,2),tabla_docs(actuales) if actuales else Paragraph('Sin documentos registrados.',pend),Spacer(1,5)]
    actuales_ev=[e for e in evid if e.get('origen','ACTUAL')=='ACTUAL']
    if actuales_ev:
        numero={e['documento']:n for n,e in enumerate(anexos_visuales,1)}
        h += [subtitulo('Evidencias de la intervención actual'),Spacer(1,2),tabla([cabecera_tabla(['Sección','Descripción','Ubicación','SHA-256'])]+[[Paragraph(esc(e['seccion']),base),Paragraph(esc(e['leyenda'] or 'Sin descripción'),base),
            Paragraph(f"Anexo visual {numero[e['documento']]}" if e['documento'] in numero else ('En la hoja de calibración' if e['seccion']=='Calibración' and incorporada else 'Documento '+str(e['documento'])),base),
            Paragraph(esc(e['hash']),mono)] for e in actuales_ev],[W*.14,W*.24,W*.13,W*.49],cabecera=True),Spacer(1,5)]
    hist_ev=[e for e in evid if e.get('origen')=='HISTORICO']
    rep=s.get('repuestos') or [];usos=[(a['id'],u) for a in s['reparaciones'] for u in a.get('usos',[])]
    flujo_h=[(sol,c_,e_) for sol in (s.get('flujo_operativo') or []) for c_,e_ in ((sol['adquisiciones'],sol['envios']),) if c_ or e_]
    if historicos or hist_ev or rep or usos or flujo_h:
        h += [subtitulo('Documentos y registros históricos (anteriores al flujo simplificado)'),Spacer(1,2),
            aviso('Se conservan por trazabilidad. No son operaciones de esta intervención: no se presentan como recepciones, compras o usos actuales.',PEND_BG,AMBAR),Spacer(1,3)]
        if historicos: h += [tabla_docs(historicos),Spacer(1,3)]
        if hist_ev: h += [tabla([cabecera_tabla(['Evidencia histórica','SHA-256'])]+[[Paragraph(esc(e['leyenda'] or 'Sin descripción'),base),Paragraph(esc(e['hash']),mono)] for e in hist_ev],[W*.4,W*.6],cabecera=True),Spacer(1,3)]
        if rep: h += [tabla([cabecera_tabla(['Recepción de repuestos (histórica)','Cantidad / unidad','Procedencia / guía','Fecha'])]+[[Paragraph(esc(f"{r['descripcion']} / {r['numero_parte']}"),base),
            Paragraph(esc(f"{r['cantidad']} {r['unidad']}"),base),Paragraph(esc(f"{r['procedencia']} / {r['guia'] or 'sin guía'}"),base),Paragraph(esc(_fecha(r['fecha']) or ''),base)] for r in rep],[W*.4,W*.18,W*.27,W*.15],cabecera=True),Spacer(1,3)]
        if usos: h += [tabla([cabecera_tabla(['Uso de repuesto (histórico)','Cantidad / unidad'])]+[[Paragraph(esc(f"Recepción {u['recepcion']} · actividad {aid}"),base),Paragraph(esc(f"{u['cantidad']} {u['unidad']}"),base)] for aid,u in usos],[W*.7,W*.3],cabecera=True),Spacer(1,3)]
        for sol,compras,envios in flujo_h:
            filas=[cabecera_tabla(['Compra / envío (histórico)','Detalle'])]+[[Paragraph(esc(f"Adquisición {c_['referencia']} · {c_['proveedor']}"),base),Paragraph(esc(f"{c_['cantidad']} {c_['unidad']}"),base)] for c_ in compras]
            filas += [[Paragraph(esc(f"Guía {e_['guia']} · {_fecha(e_['fecha']) or ''}"),base),Paragraph(esc(f"Recepción {_fecha(e_['recibido']) or 'pendiente'}"),base)] for e_ in envios]
            h += [tabla(filas,[W*.6,W*.4],cabecera=True),Spacer(1,3)]
    return construir(h)

