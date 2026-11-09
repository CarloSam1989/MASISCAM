from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape


def construir_informe(s,fotografias=()):
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
