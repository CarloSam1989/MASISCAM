"""Frozen initial-report template, no network or user-controlled paths."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

AVISO='Este informe no autoriza reparaciones ni constituye autorización comercial del cliente'


def construir_informe(snapshot,fotografias=()):
    if snapshot.get('plantilla')!='inicial-v1':
        raise ValueError('Plantilla técnica desconocida.')
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,Image,KeepTogether
    navy=colors.HexColor('#16283d')
    blue=colors.HexColor('#315d87')
    pale=colors.HexColor('#eef3f7')
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,textColor=navy,spaceAfter=6)
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=10,splitLongWords=True)
    heading=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=12,leading=16,spaceBefore=12,spaceAfter=8,keepWithNext=True)
    title=ParagraphStyle('title',parent=heading,fontSize=21,leading=26)
    p=lambda value,style=body: Paragraph(escape(str(value if value not in (None,'') else 'Pendiente / no registrado')).replace('\n','<br/>'),style)
    section=lambda label:p(label,heading)
    width,height=A4
    available=width-84
    def table(rows,widths,header=False):
        t=LongTable([[p(v,small) for v in row] for row in rows],colWidths=widths,repeatRows=1 if header else 0,splitInRow=1,hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),pale),
            ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#d5dfe8')),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        return t
    logo=Path(__file__).parent/'static'/'masiscam'/'img'/'logo_transparente.png'
    reader=ImageReader(str(logo)) if logo.exists() else None
    def frame(canvas,doc):
        canvas.saveState()
        if reader:
            canvas.drawImage(reader,42,height-68,width=144,height=54,preserveAspectRatio=True,mask='auto')
        canvas.setFont('Helvetica',8)
        canvas.setFillColor(navy)
        canvas.drawRightString(width-42,height-36,'INFORME TÉCNICO INICIAL')
        canvas.drawRightString(width-42,height-50,f"{snapshot['numero']} | v{snapshot['version']}")
        canvas.setStrokeColor(blue)
        canvas.line(42,height-76,width-42,height-76)
        canvas.line(42,42,width-42,42)
        canvas.setFont('Helvetica',7)
        canvas.drawString(42,28,'Documento interno - no autoriza reparación')
        canvas.drawRightString(width-42,28,f'Página {doc.page}')
        canvas.restoreState()
    story=[p('INFORME TÉCNICO INICIAL',title),p(f"{snapshot['numero']} · Versión documental {snapshot['version']}"),p(AVISO)]
    if snapshot.get('demostrativo'):
        story.append(p('DEMOSTRACIÓN - DATOS FICTICIOS. Ilustraciones sintéticas; sin intervención ni aprobación real.'))
    story += [section('1. Identificación y recepción'),table([
        ['Dato','Información congelada'],['Empresa',snapshot.get('empresa')],['Cliente / RUC',f"{snapshot.get('cliente','')} / {snapshot.get('ruc','')}"],
        ['Equipo',snapshot.get('equipo')],['Marca / modelo',f"{snapshot.get('marca','')} / {snapshot.get('modelo','')}"],['Serie',snapshot.get('serie')],
        ['Ratio / potencia / RPM',' / '.join(str(snapshot.get(k) or 'Pendiente') for k in ('ratio','potencia','rpm'))],
        ['Ingreso',snapshot.get('fecha')],['Condición de ingreso',snapshot.get('condicion')],['Guía remisión',snapshot.get('guia')]], [125,available-125],True)]
    story.append(section(f"2. Inspección - revisión {snapshot['revision_inspeccion']}"))
    for name,label in [('fecha','Fecha efectiva'),('estado_fisico','Estado físico'),('condiciones_generales','Condiciones generales'),('hallazgos','Hallazgos y daños'),('observaciones','Observaciones'),('motivo_sin_evidencia','Motivo de ausencia de evidencia')]:
        if snapshot['inspeccion'].get(name):
            story.extend([p(label,heading),p(snapshot['inspeccion'][name])])
    story.append(section(f"3. Diagnóstico - revisión {snapshot['revision_diagnostico']}"))
    for name,label in [('fecha','Fecha efectiva'),('fallas','Fallas identificadas'),('causas','Posibles causas y limitaciones'),('componentes_afectados','Componentes afectados'),('recomendaciones','Recomendaciones'),('conclusiones','Conclusiones técnicas'),('observaciones','Observaciones'),('motivo_sin_evidencia','Motivo de ausencia de evidencia')]:
        if snapshot['diagnostico'].get(name):
            story.extend([p(label,heading),p(snapshot['diagnostico'][name])])
    story.append(section('4. Repuestos requeridos y referencias técnicas'))
    rows=[['Descripción / parte','Cantidad / unidad','Condición / acción','Fuente / plano']]
    for pieza in snapshot['piezas']:
        ref=pieza.get('referencia') or {}
        rows.append([f"{pieza['descripcion']}\nParte: {pieza['numero_parte'] or 'Identificación pendiente'}",
            f"{pieza['cantidad']} {pieza['unidad'] or 'Unidad pendiente'}\n{pieza['motivo_unidad_pendiente']}",
            f"{pieza['condicion']}\n{pieza['accion']}",
            f"{pieza['origen']}\n{ref.get('referencia','')} r{ref.get('revision','')} {ref.get('posicion','')}\n{ref.get('hash','')}"])
    if len(rows)>1:
        story.append(table(rows,[available*.32,available*.18,available*.25,available*.25],True))
    else:
        story.append(p('El técnico declara que no se requieren repuestos.'))
    for item in snapshot['evidencias']:
        if item.get('referencia'):
            ref=item['referencia']
            story.append(p(f"{ref['tipo']} {ref['codigo']} · Revisión interna {ref['revision']} · {ref['titulo']} · Versión fabricante: {ref['version_fabricante'] or 'No consta'}"))
            story.append(p(f"{item['leyenda']} · SHA-256 {item['hash']}",small))
    story.append(section('5. Evidencias fotográficas'))
    for item,contenido in fotografias:
        img=Image(BytesIO(contenido))
        factor=min(available/img.imageWidth,220/img.imageHeight)
        img.drawWidth,img.drawHeight=img.imageWidth*factor,img.imageHeight*factor
        story.append(KeepTogether([p(f"{item['clase']} - {item['leyenda']}"),img,Spacer(1,10)]))
        story.append(p(f"Documento {item['documento_id']} · SHA-256 {item['hash']}",small))
    if not fotografias:
        story.append(p('Ausencia de fotografías documentada en las revisiones fuente.'))
    story += [section('6. Responsables y trazabilidad'),table([
        ['Técnico autor',snapshot['tecnico']],['Revisor independiente',snapshot['revisor']],['Decisión registrada',snapshot['decision_fecha']],
        ['Plantilla','inicial-v1'],['Limitación de alcance',AVISO]], [125,available-125]),
        p('Identificación de responsables; no incluye firmas certificadas ni simula firmas de aceptación.',small)]
    output=BytesIO()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=42,rightMargin=42,topMargin=92,bottomMargin=57,
        title=f"Informe inicial {snapshot['numero']}",author='MASISCAM').build(story,onFirstPage=frame,onLaterPages=frame)
    return output.getvalue()
