"""A4 final maintenance report. Snapshots contain facts, never fake signatures."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape


def construir_informe(s,fotografias=()):
    if s.get('plantilla')!='final-v1': raise ValueError('Plantilla final desconocida.')
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import SimpleDocTemplate,Paragraph,LongTable,TableStyle,Image,Spacer,PageBreak,CondPageBreak
    width,height=A4;usable=width-80
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=9,leading=13,spaceAfter=6)
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=10)
    head=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=14,leading=19,spaceBefore=13,keepWithNext=True)
    title=ParagraphStyle('cover',parent=head,fontSize=26,leading=32,spaceBefore=40)
    def p(value,style=body):
        return Paragraph(escape(str(value if value not in ('',None) else 'No documentado / pendiente')).replace('\n','<br/>'),style)
    def table(rows,weights):
        tab=LongTable([[p(v,small) for v in r] for r in rows],colWidths=[usable*w for w in weights],repeatRows=1,splitInRow=1,hAlign='LEFT')
        tab.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf1f6')),
            ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#c8d6e3')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return tab
    def facts(data):
        for k,v in data.items():
            if isinstance(v,(dict,list)): continue
            story.append(p(f'{k.replace("_"," ").capitalize()}: {v if v not in (None,"") else "No documentado / pendiente"}'))
    logo=Path(__file__).parent/'static/masiscam/img/logo_transparente.png'
    def frame(canvas,doc):
        canvas.saveState()
        if logo.exists(): canvas.drawImage(ImageReader(str(logo)),40,height-64,width=135,height=46,preserveAspectRatio=True,mask='auto')
        canvas.setFont('Helvetica',8);canvas.drawRightString(width-40,height-35,s['numero'])
        canvas.drawRightString(width-40,height-49,f'Informe final | versión {s["version"]}')
        canvas.setStrokeColor(colors.HexColor('#315d87'));canvas.line(40,height-76,width-40,height-76)
        canvas.setFont('Helvetica',7);canvas.drawString(40,28,'MASISCAM | Documento privado. Aprobaciones en trazabilidad del expediente.')
        canvas.drawRightString(width-40,28,f'Página {doc.page}');canvas.restoreState()
    story=[p('INFORME FINAL DE MANTENIMIENTO',title),Spacer(1,16),p(s['numero'],head),p(f'Versión documental {s["version"]}'),
        p('Contenido congelado para revisión técnica independiente. La emisión del PDF no confirma el cierre administrativo.'),p(s['aviso'])]
    if s.get('demostrativo'): story.append(p('DEMOSTRATIVO - DATOS FICTICIOS QA. SIN VALIDEZ INDUSTRIAL.',head))
    story.extend([Spacer(1,20),p(f'Cliente: {s["recepcion"].get("cliente","")}'),p(f'Equipo: {s["recepcion"].get("equipo","")}'),
        p(f'Finalización técnica registrada: {s["fecha_finalizacion"]}'),PageBreak(),p('1. Cliente, equipo y recepción',head)])
    receipt=s['recepcion']
    story.append(table([['Dato de recepción','Información congelada']]+[[k.replace('_',' ').capitalize(),v] for k,v in receipt.items() if not isinstance(v,(dict,list))],[.3,.7]))
    story.append(p('Confirmación y excepciones documentadas',head));facts(s.get('confirmacion',{}))
    def photos(section):
        for item,data in fotografias:
            if item['seccion']!=section: continue
            im=Image(BytesIO(data));scale=min(usable/im.imageWidth,200/im.imageHeight,1)
            im.drawWidth=im.imageWidth*scale;im.drawHeight=im.imageHeight*scale
            from reportlab.platypus import KeepTogether
            story.extend([KeepTogether([p(item['leyenda']),im]),Spacer(1,8)])
    photos('Recepción')
    story.append(p('2. Inspección técnica',head));facts(s['tecnico']['inspeccion'])
    story.append(p('3. Diagnóstico aprobado y conclusiones',head));facts(s['tecnico']['diagnostico'])
    story.append(p(f'Técnico: {s["tecnico"]["tecnico"]} | Revisión independiente: {s["tecnico"]["revisor"]} | Fecha: {s["tecnico"]["decision_fecha"]}'))
    photos('Inspección / diagnóstico')
    story.append(p('4. Planos y repuestos requeridos',head))
    parts=s['tecnico']['piezas']
    if parts:
        story.append(table([['Pieza / parte','Cantidad / unidad','Condición / acción','Referencia']]+[[r['descripcion']+' / '+r['numero_parte'],r['cantidad']+' '+(r['unidad'] or 'Unidad pendiente justificada'),r['condicion']+' / '+r['accion'],str(r['referencia'])] for r in parts],[.27,.17,.26,.3]))
    else: story.append(p('Diagnóstico aprobado sin repuestos requeridos. No se simulan compras ni consumos.'))
    for e in s['tecnico']['evidencias']:
        if e.get('referencia'): facts(e['referencia'])
    if not any(e.get('referencia') for e in s['tecnico']['evidencias']):
        story.append(p('No hay planos/manuales citados en esta revisión. No se presume su existencia ni aplicabilidad.'))
    story.append(p('5. Repuestos recibidos',head))
    if s['repuestos']:
        story.append(table([['Descripción / parte','Cantidad / unidad','Procedencia / guía','Observaciones']]+[[r['descripcion']+' / '+r['numero_parte'],r['cantidad']+' '+r['unidad'],r['procedencia']+' / '+(r['guia'] or 'Sin guía registrada'),r['discrepancia']] for r in s['repuestos']],[.3,.18,.27,.25]))
    else: story.append(p('Sin recepciones de repuestos registradas. No se presupone recepción física.'))
    story.append(p('6. Reparación y montaje',head))
    for activity in s['reparaciones']:
        facts(activity);facts(activity['aprobacion'])
        for usage in activity['usos']: facts(usage)
    photos('Intervención / repuestos')
    cal=s['calibracion'];story.extend([PageBreak(),p('7. Calibración y aprobación técnica',head)])
    for k in ('protocolo','protocolo_version','protocolo_huella','tecnico','fecha','instrumentos','resultados','observaciones','estado'):
        story.append(p(f'{k.replace("_"," ")}: {cal[k]}'))
    story.append(table([['Punto','Valor / unidad','Mín. / Máx.','Tolerancia','Evaluación']]+[[r['nombre'],str(r['valor'])+' / '+r.get('unidad',''),str(r.get('minimo','No definido'))+' / '+str(r.get('maximo','No definido')),r.get('tolerancia','No definida'),r['evaluacion']] for r in cal['mediciones']],[.25,.2,.17,.2,.18]))
    for row in cal['mediciones']: story.append(p(f'{row["codigo"]} | Fuente: {row.get("fuente","")} | Criterio: {row.get("criterio", "Pendiente")} | Observaciones: {row.get("observaciones", "")}',small))
    for row in cal['parametros']: facts(row)
    facts(cal['decision'] or {})
    story.append(p('Las evaluaciones son decisiones humanas sobre el protocolo exacto validado. No se calcula conformidad ni se certifican instrumentos por este informe.'))
    photos('Calibración')
    story.append(p('8. Responsables, revisión final y cierre',head))
    facts(s['autorizacion'])
    story.append(p('Aprobación del contenido final y cierre administrativo: consultar registros inmutables asociados a esta versión y su SHA-256. Este PDF se emite antes de esas decisiones y no las simula. No contiene aceptación ni firma del cliente.'))
    story.append(p('9. Anexos y trazabilidad documental',head))
    for item in s['anexos']: story.append(p(f'{item["titulo"]} | Documento {item["documento"]} | SHA-256 {item["hash"]}',small))
    for item in s['evidencias']: story.append(p(f'{item["seccion"]}: {item["leyenda"]} | Documento {item["documento"]} | SHA-256 {item["hash"]}',small))
    stream=BytesIO();SimpleDocTemplate(stream,pagesize=A4,leftMargin=40,rightMargin=40,topMargin=94,bottomMargin=56,
        title='Informe final de mantenimiento MASISCAM',author='MASISCAM').build(story,onFirstPage=frame,onLaterPages=frame)
    return stream.getvalue()
