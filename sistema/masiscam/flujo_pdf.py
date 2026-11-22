"""Guía privada de entrega; no sustituye una guía fiscal ni acredita recepción."""
from io import BytesIO
from xml.sax.saxutils import escape


def cotizacion_comercial(s):
    """PDF comercial desde una versión congelada; no genera documentos fiscales."""
    from pathlib import Path
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, Table, TableStyle, Spacer, Image, KeepTogether
    navy=colors.HexColor('#213b55'); line=colors.HexColor('#d5e0ea')
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,textColor=navy,spaceAfter=5)
    small=ParagraphStyle('small',parent=body,fontSize=7.5,leading=10,spaceAfter=0)
    heading=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=19,leading=24)
    def p(value,style=body):return Paragraph(escape(str(value or '—')).replace('\n','<br/>'),style)
    logo=Path(__file__).parent/'static/masiscam/img/logo_transparente.png'
    image=Image(str(logo));ratio=image.imageHeight/image.imageWidth
    image.drawWidth=145;image.drawHeight=145*ratio
    if image.drawHeight>48:image.drawWidth*=48/image.drawHeight;image.drawHeight=48
    top=Table([[image,[p('COTIZACIÓN',heading),p(s['referencia']),p(f"Versión {s['version']} | {s['fecha']}")]]],colWidths=[285,230])
    top.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,0),(0,0),'LEFT'),('LINEBELOW',(0,0),(-1,-1),1.2,navy),('BOTTOMPADDING',(0,0),(-1,-1),15)]))
    c=s['cliente']
    info=Table([[p('Cliente: '+str(c.get('cliente',''))),p('RUC: '+str(c.get('ruc','')))],
        [p('Equipo: '+str(c.get('equipo',''))),p('Serie: '+str(c.get('serie','')))],
        [p('Expediente: '+s['expediente']),p('Moneda: USD')]],colWidths=[300,215])
    info.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    heading_s=ParagraphStyle('heading_s',parent=body,fontName='Helvetica-Bold',fontSize=10.5,leading=14)
    white=ParagraphStyle('white',parent=small,textColor=colors.white,fontName='Helvetica-Bold')
    def tabla(cabecera, filas, anchos, vacio):
        rows = [[p(label, white) for label in cabecera]]
        for fila in filas:
            rows.append([p(value, small) for value in fila])
        if len(rows) == 1:
            rows.append([p(vacio, small)] + [p('', small) for _ in range(len(cabecera) - 1)])
        table = LongTable(rows, colWidths=anchos, repeatRows=1, splitInRow=1, hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
            ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f4f7fa')]),
            ('LINEBELOW',(0,0),(-1,-1),.35,line)]))
        return table
    servicios = tabla(('Tipo','Descripción','Cantidad','Unidad','P. unitario\nUSD','Subtotal\nUSD'),
        [[r.get('tipo','').capitalize(), r.get('descripcion','')+(('\nAdicional de OFICINA, pendiente de revisión técnica. Justificación: '+r['justificacion']) if r.get('origen')=='OFICINA' and r.get('justificacion') else ''), r.get('cantidad',''), r.get('unidad',''),
          r.get('precio_unitario',''), r.get('total','')] for r in s['lineas']],
        [70,185,50,50,80,80], 'Sin servicios cotizados')
    repuestos = tabla(('N.º en\nplano','Código','Descripción','Cantidad','Unidad','Compatibilidad'),
        [[r.get('posicion',''), r.get('codigo',''), r.get('descripcion',''), r.get('cantidad',''), r.get('unidad',''),
          r.get('compatibilidad','')] for r in s.get('repuestos', [])],
        [45,70,150,45,45,160], 'Sin repuestos requeridos')
    totals=Table([[p('TOTAL DE SERVICIOS Y CONSUMIBLES (USD)'),p(s['total'],heading)]],colWidths=[285,230])
    totals.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#edf3f8')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    acceptance=Table([[p('Nombre y cargo del cliente',small),p('Firma',small),p('Fecha',small)],['________________________','____________________','________________']],colWidths=[210,170,135])
    acceptance.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,1),(-1,1),23)]))
    d=s.get('diagnostico') or {}
    tecnico=[p('DIAGNÓSTICO TÉCNICO'+(f" (revisión {d['revision']})" if d.get('revision') else ''),heading_s)]
    for etiqueta,clave in (('Fallas','fallas'),('Causas','causas'),('Componentes afectados','componentes_afectados'),('Recomendaciones','recomendaciones'),('Conclusiones','conclusiones')):
        if d.get(clave):tecnico.append(p(f"{etiqueta}: {d[clave]}",body))
    if len(tecnico)==1:tecnico.append(p('Sin diagnóstico textual registrado.',small))
    story=[top,Spacer(1,18),info,Spacer(1,12)]+tecnico+[Spacer(1,8),p('SERVICIOS Y CONSUMIBLES COTIZADOS',heading_s),servicios,Spacer(1,12),totals,Spacer(1,18),
        p('REPUESTOS REQUERIDOS (no incluidos en el valor de la cotización)',heading_s),
        p('Lista informativa de TALLER. MASISCAM no vende ni factura estos repuestos en esta cotización.',small),Spacer(1,4),repuestos,Spacer(1,18),
        p('OBSERVACIONES'),p(s['observaciones'] or 'Sin observaciones registradas.'),Spacer(1,8),
        p('CONDICIONES COMERCIALES'),p(s['condiciones'] or 'No registradas en esta versión.'),Spacer(1,14),
        KeepTogether([p('ACEPTACIÓN DEL CLIENTE'),acceptance,Spacer(1,10),
            p('La aprobación de ADMINISTRADOR y la aceptación verificable del cliente son obligatorias antes de ejecutar el servicio.',small)])]
    def footer(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(line);canvas.line(40,36,555,36)
        canvas.setFont('Helvetica',7);canvas.setFillColor(navy)
        canvas.drawString(40,24,'MASISCAM | Cotización comercial - no es un comprobante fiscal')
        canvas.drawRightString(555,24,f"v{s['version']} | Página {doc.page}");canvas.restoreState()
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=40,rightMargin=40,topMargin=36,bottomMargin=50,
        title='Cotización '+s['referencia'],author='MASISCAM').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


def guia_repuestos(s):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, TableStyle, Spacer
    styles=getSampleStyleSheet()
    def p(value): return Paragraph(escape(str(value)),styles['BodyText'])
    rows=[[p(x) for x in ['Código / descripción','Cantidad','Unidad']]]
    rows += [[p(f"{r['codigo'] or 'Código pendiente'} · {r['descripcion']}"),p(r['cantidad']),p(r['unidad'])] for r in s['lineas']]
    table=LongTable(rows,colWidths=[305,75,115],repeatRows=1,splitInRow=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf1f6')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),9),
        ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#c8d6e3'))]))
    story=[Paragraph('MASISCAM · Entrega de repuestos',styles['Title']),p(f"Guía: {s['guia']}"),
        p(f"Expediente: {s['numero']} · Solicitud v{s['version_solicitud']}"),
        p(f"Cliente: {s['cliente']['cliente']} · RUC: {s['cliente']['ruc']}"),
        p(f"Equipo: {s['cliente']['equipo']} · Serie: {s['cliente']['serie']}"),
        p(f"Fecha de envío: {s['fecha']} · Responsable: {s['responsable']}"),
        p(f"SHA-256 de la solicitud: {s.get('solicitud_sha','No documentado')}"),Spacer(1,16),table,Spacer(1,16),
        p('Documento privado de trazabilidad interna. Cantidades enviadas por OFICINA; recepción de TALLER pendiente. No constituye aceptación comercial ni conformidad técnica.')]
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=40,rightMargin=40,topMargin=45,bottomMargin=45,
        title='Guía de entrega de repuestos MASISCAM',author='MASISCAM').build(story)
    return stream.getvalue()
