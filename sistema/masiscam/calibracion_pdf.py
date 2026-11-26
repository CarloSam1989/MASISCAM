from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape


def construir_informe(s,fotografias=()):
    if s.get('plantilla')=='hoja-calibracion-v1':
        return construir_hoja(s,fotografias)
    if s.get('plantilla')!='calibracion-v1':
        raise ValueError('Plantilla PDF desconocida.')
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,LongTable,TableStyle,Image,Spacer,CondPageBreak
    from reportlab.lib.utils import ImageReader
    width,height=A4; usable=width-72
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,spaceAfter=6,textColor=colors.HexColor('#16283d'))
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=10)
    head=ParagraphStyle('head',parent=body,fontName='Helvetica-Bold',fontSize=13,leading=18,spaceBefore=12,keepWithNext=True)
    table_head=ParagraphStyle('tablehead',parent=head,keepWithNext=False)
    caption=ParagraphStyle('caption',parent=body,keepWithNext=True)
    def p(value,style=body):
        return Paragraph(escape(str(value if value not in ('',None) else 'Pendiente / no documentado')).replace('\n','<br/>'),style)
    def ref(value):
        return 'No definido' if value in (None,'') else str(value)
    def table(rows,widths):
        t=LongTable([[p(v,small) for v in r] for r in rows],colWidths=widths,repeatRows=1,splitInRow=1,hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef3f7')),
            ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d5dfe8')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t
    logo=Path(__file__).parent/'static'/'masiscam'/'img'/'logo_transparente.png'
    def frame(c,doc):
        c.saveState()
        if logo.exists(): c.drawImage(ImageReader(str(logo)),36,height-66,width=140,height=50,preserveAspectRatio=True,mask='auto')
        c.setFont('Helvetica',8)
        c.drawRightString(width-36,height-35,s['numero'])
        c.drawRightString(width-36,height-49,f"Calibración r{s['revision']} | PDF v{s['version']}")
        c.setStrokeColor(colors.HexColor('#315d87'));c.line(36,height-76,width-36,height-76);c.line(36,42,width-36,42)
        c.setFont('Helvetica',7)
        c.drawString(36,28,'MASISCAM | Registro privado. No es certificado metrológico ni informe técnico final.')
        c.drawRightString(width-36,28,f'Página {doc.page}')
        c.restoreState()
    story=[p('REGISTRO DE CALIBRACIÓN',head),p(f"Estado de revisión: {s['estado']}"),
        p('No se declara conformidad automática. Los valores registrados, referencias y evaluación manual se presentan por separado.')]
    if s.get('demostrativo'):
        story.append(p('DEMOSTRATIVO - DATOS FICTICIOS QA. SIN VALIDEZ INDUSTRIAL.',head))
    rec=s['recepcion']
    story.extend([p(f"Cliente: {rec.get('cliente','')}"),p(f"Equipo: {rec.get('equipo','')} | Tipo: {s['tipo_equipo']}"),
        p(f"Protocolo: {s['protocolo']} v{s['protocolo_version']}"),p(f"Huella de protocolo: {s['protocolo_huella']}",small),
        p(f"Técnico: {s['tecnico']} | Fecha registrada: {s['fecha']}"),p('Instrumentos documentados',head),p(s['instrumentos'])])
    story.append(p('Parámetros aplicables según protocolo',head))
    if s['parametros']:
        story.append(table([['Parámetro','Valor registrado','Unidad','Referencia / fuente']]+[
            [r['nombre'],r.get('valor'),r.get('unidad'),r.get('fuente')] for r in s['parametros']],
            [usable*.25,usable*.19,usable*.16,usable*.4]))
    else:
        story.append(p('La plantilla no define parámetros adicionales. No se presupone aplicabilidad de shims, backlash o presión.'))
    story.extend([CondPageBreak(140),p('Mediciones: valores y evaluación independiente',table_head)])
    story.append(table([['Punto','Valor registrado','Unidad','Mín. / Máx. de referencia','Tolerancia documentada','Evaluación manual']]+[
        [r['nombre'],r.get('valor'),r.get('unidad'),f"{ref(r.get('minimo'))} / {ref(r.get('maximo'))}",r.get('tolerancia'),r['evaluacion']]
        for r in s['mediciones']], [usable*.2,usable*.15,usable*.12,usable*.18,usable*.18,usable*.17]))
    story.append(p('Fuentes, criterios y observaciones por punto',head))
    for r in s['mediciones']:
        story.append(p(f"{r['codigo']} | {r['nombre']} | Fuente: {r.get('fuente','')} | Criterio: {r.get('criterio','Pendiente de aprobación')} | Observaciones: {r.get('observaciones','')}",small))
    story.extend([p('Resultados registrados por el técnico',head),p(s['resultados']),p('Observaciones',head),p(s['observaciones'])])
    dec=s.get('decision')
    story.append(p('Revisión técnica',head))
    story.append(p(f"{s['estado']} | Responsable: {dec['responsable']} | Fecha: {dec['fecha']} | Motivo: {dec['motivo']}" if dec else 'Pendiente de revisión independiente. No existe aprobación técnica.'))
    if s['evidencias']:
        story.append(p('Evidencias y documentos del instrumento',head))
        for item in s['evidencias']:
            story.extend([p(f"Documento {item['documento']} | {item['clase']} | {item['leyenda']}",small),p(f"SHA-256: {item['hash']}",small)])
    for item,data in fotografias:
        img=Image(BytesIO(data));scale=min(usable/img.imageWidth,220/img.imageHeight,1)
        img.drawWidth=img.imageWidth*scale;img.drawHeight=img.imageHeight*scale
        story.extend([Spacer(1,8),p(item['leyenda'],caption),img])
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=94,bottomMargin=58,
        title='Registro privado de calibración',author='MASISCAM').build(story,onFirstPage=frame,onLaterPages=frame)
    return stream.getvalue()


def _fecha_legible(texto):
    from datetime import datetime
    try: return datetime.fromisoformat(str(texto)).strftime('%d/%m/%Y %H:%M')
    except ValueError: return str(texto or '')


def construir_ficha(conf,definicion,titulo):
    """Ficha técnica de la configuración del reductor: identifica el formato de la hoja; no contiene tolerancias ni aprobaciones."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    stream=BytesIO();c=Canvas(stream,pagesize=A4,invariant=1)
    width,height=A4;y=height-60
    c.setFont('Helvetica-Bold',14);c.drawString(40,y,'FICHA TÉCNICA DE CALIBRACIÓN');y-=20
    c.setFont('Helvetica',9);c.drawString(40,y,titulo[:110]);y-=22
    for etiqueta,clave in (('Marca','marca'),('Modelo','modelo'),('Ratio','ratio'),('Rotación','rotacion')):
        c.drawString(40,y,f'{etiqueta}: {conf.get(clave) or "sin dato registrado"}');y-=14
    y-=8;c.setFont('Helvetica-Bold',10);c.drawString(40,y,'Datos que registra TALLER en cada hoja');y-=16;c.setFont('Helvetica',9)
    for fila in definicion['parametros']:
        c.drawString(48,y,f"- {fila['nombre']}");y-=12
    c.drawString(48,y,'- 16 mediciones numeradas');y-=12
    c.drawString(48,y,'- Observaciones, fecha y técnico responsable');y-=22
    c.setFont('Helvetica-Oblique',8)
    for linea in ('Formato de hoja de calibración de MASISCAM elaborado por TALLER.',
            'No es un protocolo industrial ni contiene tolerancias aprobadas; no afirma conformidad técnica.'):
        c.drawString(40,y,linea);y-=11
    c.save()
    return stream.getvalue()


def construir_hoja(s,fotografias=()):
    """Hoja de calibración con el orden del formato físico: datos generales, shims, datos de calibración, 16 mediciones, observaciones."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Image,Spacer,KeepTogether
    from reportlab.lib.utils import ImageReader
    width,height=A4;usable=width-72
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,spaceAfter=4,textColor=colors.HexColor('#16283d'))
    small=ParagraphStyle('small',parent=body,fontSize=8,leading=11,spaceAfter=0)
    head=ParagraphStyle('head',parent=body,fontName='Helvetica-Bold',fontSize=11,leading=15,spaceBefore=10,keepWithNext=True)
    titulo=ParagraphStyle('titulo',parent=head,fontSize=15,leading=20,spaceBefore=0)
    def p(value,style=small):
        return Paragraph(escape(str(value if value not in ('',None) else '—')).replace('\n','<br/>'),style)
    def tabla(filas,anchos,cabecera=False):
        t=Table([[p(v) for v in f] for f in filas],colWidths=anchos,hAlign='LEFT',repeatRows=1 if cabecera else 0)
        estilo=[('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#c9d6e2')),
            ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]
        if cabecera: estilo.append(('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef3f7')))
        t.setStyle(TableStyle(estilo));return t
    logo=Path(__file__).parent/'static'/'masiscam'/'img'/'logo_transparente.png'
    def marco(c,doc):
        c.saveState()
        if logo.exists(): c.drawImage(ImageReader(str(logo)),36,height-66,width=140,height=50,preserveAspectRatio=True,mask='auto')
        c.setFont('Helvetica',8)
        c.drawRightString(width-36,height-35,s['numero'])
        c.drawRightString(width-36,height-49,f"Hoja de calibración r{s['revision']} | PDF v{s['version']}")
        c.setStrokeColor(colors.HexColor('#315d87'));c.line(36,height-76,width-36,height-76);c.line(36,42,width-36,42)
        c.setFont('Helvetica',7)
        c.drawString(36,28,'MASISCAM | Documento técnico elaborado por TALLER. No es certificado metrológico ni afirma conformidad técnica.')
        c.drawRightString(width-36,28,f'Página {doc.page}')
        c.restoreState()
    rec=s['recepcion'];eq=s.get('equipo',{});pv={r['codigo']:r for r in s['parametros']}
    def valor(codigo):
        r=pv[codigo]
        return f"{r['valor']} {r['unidad']}".strip() if r['valor'] is not None else 'Pendiente'
    mitad=usable/4
    historia=[p('HOJA DE CALIBRACIÓN',titulo),p('Documento técnico elaborado y generado por TALLER. No requiere aprobación de ADMINISTRADOR.')]
    if s.get('motivo_revision'): historia.append(p(f"Revisión {s['revision']} · motivo: {s['motivo_revision']}"))
    historia += [p('A · Datos generales',head),
        tabla([['Cliente',rec.get('cliente',''),'Equipo',eq.get('nombre') or rec.get('equipo','')],
            ['Marca',eq.get('marca'),'Modelo',eq.get('modelo')],['Serie',eq.get('numero_serie'),'Ratio',eq.get('ratio')],
            ['Potencia (HP)',eq.get('potencia_hp'),'RPM',eq.get('rpm')],['Rotación',eq.get('rotacion'),'Factor de servicio',eq.get('service_factor')]],
            [mitad*.8,mitad*1.2,mitad*.8,mitad*1.2]),
        p('B · Shims',head),
        tabla([['Vertical superior','Vertical inferior','Horizontal'],[valor('SHIM-VS'),valor('SHIM-VI'),valor('SHIM-H')]],[usable/3]*3,True),
        p('C · Datos de calibración',head),
        tabla([['Backlash','Tolerancia','Calibración total','Mínimo','Máximo'],
            [valor('BACKLASH'),valor('TOLERANCIA'),valor('CAL-TOTAL'),valor('MINIMO'),valor('MAXIMO')]],[usable/5]*5,True),
        Spacer(1,6),
        tabla([['Presión eje vertical','Presión eje horizontal'],[valor('PRES-V'),valor('PRES-H')]],[usable/2]*2,True)]
    for aviso in s.get('advertencias',[]): historia.append(p(aviso))
    filas=[['N.º','Valor','Unidad','Verificación']]+[[n,r.get('valor') if r.get('valor') is not None else 'Pendiente',r.get('unidad'),r.get('evaluacion')]
        for n,r in enumerate(s['mediciones'],1)]
    historia.append(p('D · Mediciones (1 a 16)',head))
    historia.append(tabla(filas,[usable*.1,usable*.25,usable*.15,usable*.5],True))
    verif=s.get('verificacion',{})
    historia += [Spacer(1,4),p(verif.get('texto','')),p('E · Observaciones, fecha y técnico responsable',head),
        tabla([['Observaciones',s.get('observaciones')],['Fecha',_fecha_legible(s['fecha'])],['Técnico responsable',s.get('tecnico_nombre') or s['tecnico']]],[usable*.25,usable*.75])]
    if verif.get('estado')=='TOLERANCIA_APROBADA':
        historia.append(p(f"Tolerancias aprobadas: protocolo {verif['protocolo']} v{verif['version']} · huella {verif['huella']}"))
    if s['evidencias']:
        historia.append(p('Evidencias',head))
        for item in s['evidencias']:
            historia.append(p(f"Documento {item['documento']} | {item['clase']} | {item['leyenda']} | SHA-256 {item['hash']}"))
    for item,data in fotografias:
        img=Image(BytesIO(data));escala=min(usable/img.imageWidth,220/img.imageHeight,1)
        img.drawWidth=img.imageWidth*escala;img.drawHeight=img.imageHeight*escala
        historia.append(KeepTogether([Spacer(1,6),p(item['leyenda']),img]))
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=94,bottomMargin=58,
        title='Hoja de calibración MASISCAM',author='MASISCAM').build(historia,onFirstPage=marco,onLaterPages=marco)
    return stream.getvalue()
