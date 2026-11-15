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
    white=ParagraphStyle('white',parent=small,textColor=colors.white,fontName='Helvetica-Bold')
    rows=[[p(label,white) for label in ('N.º en\nplano','Código','Descripción','Daño observado','Cantidad','P. unitario\nUSD','Subtotal\nUSD')]]
    for row in s['lineas']:
        rows.append([p(row.get(key,''),small) for key in ('posicion','codigo','descripcion','condicion','cantidad','precio_unitario','total')])
    if len(rows)==1:rows.append([p('Sin repuestos',small)]+[p('',small) for _ in range(6)])
    table=LongTable(rows,colWidths=[45,58,126,95,50,65,76],repeatRows=1,splitInRow=1,hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
        ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f4f7fa')]),
        ('LINEBELOW',(0,0),(-1,-1),.35,line)]))
    totals=Table([[p('TOTAL GENERAL (USD)'),p(s['total'],heading)]],colWidths=[285,230])
    totals.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#edf3f8')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    acceptance=Table([[p('Nombre y cargo del cliente',small),p('Firma',small),p('Fecha',small)],['________________________','____________________','________________']],colWidths=[210,170,135])
    acceptance.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,1),(-1,1),23)]))
    story=[top,Spacer(1,18),info,Spacer(1,12),table,Spacer(1,12),totals,Spacer(1,18),
        p('OBSERVACIONES'),p(s['observaciones'] or 'Sin observaciones registradas.'),Spacer(1,8),
        p('CONDICIONES COMERCIALES'),p(s['condiciones'] or 'No registradas en esta versión.'),Spacer(1,14),
        KeepTogether([p('ACEPTACIÓN DEL CLIENTE'),acceptance,Spacer(1,10),
            p('La aceptación debe registrarse con respaldo verificable por OFICINA antes de aprobar el envío a TALLER.',small)])]
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
