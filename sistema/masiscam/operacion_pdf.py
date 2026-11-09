"""Internal operational reports. No signature or commercial acceptance."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape


def construir_documento(snapshot, fotografias=()):
    if snapshot.get('plantilla')!='operacion-v1':
        raise ValueError('Plantilla operativa desconocida.')
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, Image
    from reportlab.lib.utils import ImageReader
    width,height=A4
    usable=width-84
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,spaceAfter=6,textColor=colors.HexColor('#16283d'))
    small=ParagraphStyle('small',parent=body,fontSize=8,leading=11)
    caption=ParagraphStyle('caption',parent=small,keepWithNext=True)
    heading=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=13,leading=17,spaceBefore=12,spaceAfter=8,keepWithNext=True)
    p=lambda value,style=body:Paragraph(escape(str(value if value not in (None,'') else 'No registrado')).replace('\n','<br/>'),style)
    title='COMPROBANTE INTERNO DE REPUESTOS' if snapshot['tipo']=='REPUESTOS' else 'REPORTE INTERNO DE REPARACIÓN'
    logo=Path(__file__).parent/'static'/'masiscam'/'img'/'logo_transparente.png'
    def frame(c,doc):
        c.saveState()
        if logo.exists():
            c.drawImage(ImageReader(str(logo)),42,height-68,width=140,height=50,preserveAspectRatio=True,mask='auto')
        c.setFont('Helvetica',8)
        c.drawRightString(width-42,height-37,snapshot['numero'])
        c.drawRightString(width-42,height-51,f"Versión {snapshot['version']} | Documento privado")
        c.setStrokeColor(colors.HexColor('#315d87'))
        c.line(42,height-78,width-42,height-78)
        c.line(42,42,width-42,42)
        c.drawString(42,28,'MASISCAM | Registro interno. No constituye aceptación comercial del cliente.')
        c.drawRightString(width-42,28,f'Página {doc.page}')
        c.restoreState()
    story=[p(title,heading),p(f"Expediente: {snapshot['numero']} | Estado al emitir: {snapshot['estado']}"),
        p('Registro documental interno. No autoriza trabajos adicionales ni acredita calibración o aprobación técnica final.')]
    if snapshot.get('demostrativo'):
        story.append(p('DEMOSTRATIVO - DATOS FICTICIOS QA. SIN VALIDEZ OPERATIVA.',heading))
    receipt=snapshot['recepcion']
    for key in ('cliente','equipo'):
        value=receipt.get(key,{})
        story.append(p(key.capitalize(),heading))
        if isinstance(value,dict):
            story.extend(p(f'{k.replace("_"," ").capitalize()}: {v}') for k,v in value.items() if v not in (None,''))
        else:
            story.append(p(value))
    for row in snapshot['registros']:
        story.append(p(f"Registro {row['id']}",heading))
        rows=[[p('Campo',small),p('Información congelada',small)]]
        for key,value in row.items():
            if key=='id':
                continue
            if key=='usos':
                value='\n'.join(f"Recepción {u['recepcion']} | {u['descripcion']} | {u['cantidad']} {u['unidad']}" for u in value) or 'Sin repuestos utilizados registrados'
            rows.append([p(key.replace('_',' ').capitalize(),small),p(value,small)])
        table=LongTable(rows,colWidths=[usable*.27,usable*.73],repeatRows=1,splitInRow=1,hAlign='LEFT')
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef3f7')),
            ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d5dfe8')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(table)
    story.append(p('Evidencias y documentos fuente',heading))
    for item in snapshot['evidencias']:
        story.append(p(f"Documento {item['documento']} | {item['clase']} | {item['leyenda']}",small))
        story.append(p(f"SHA-256: {item['hash']}",small))
    for item,data in fotografias:
        image=Image(BytesIO(data))
        ratio=min(usable/image.imageWidth,245/image.imageHeight,1)
        image.drawWidth=image.imageWidth*ratio
        image.drawHeight=image.imageHeight*ratio
        story.extend([Spacer(1,8),p(f"{item['clase']}: {item['leyenda']}",caption),image])
    stream=BytesIO()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=42,rightMargin=42,topMargin=94,bottomMargin=58,
        title=title,author='MASISCAM').build(story,onFirstPage=frame,onLaterPages=frame)
    return stream.getvalue()
