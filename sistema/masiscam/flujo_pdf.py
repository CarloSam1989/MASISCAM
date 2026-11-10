"""Guía privada de entrega; no sustituye una guía fiscal ni acredita recepción."""
from io import BytesIO
from xml.sax.saxutils import escape


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
