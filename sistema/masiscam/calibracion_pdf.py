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


MESES=('enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre')


def _fecha_local(texto):
    """Fecha de la hoja en la zona horaria del sistema (la base la guarda en UTC); None si no hay fecha válida."""
    from datetime import datetime
    try: d=datetime.fromisoformat(str(texto))
    except (TypeError,ValueError): return None
    if d.tzinfo:
        from django.utils import timezone
        d=timezone.localtime(d)
    return d


def _fecha_legible(texto):
    d=_fecha_local(texto)
    return d.strftime('%d/%m/%Y %H:%M') if d else str(texto or '')


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


def _imagen_anexo(data,ancho,alto):
    """Fotografía lista para incrustar: orientación corregida, tamaño acotado y JPEG ligero. None si el archivo no es una imagen legible."""
    from reportlab.platypus import Image
    try:
        from PIL import Image as PIL,ImageOps
        im=ImageOps.exif_transpose(PIL.open(BytesIO(data)))
        if im.mode in ('RGBA','LA','P'):
            im=im.convert('RGBA');fondo=PIL.new('RGB',im.size,'white');fondo.paste(im,mask=im.split()[-1]);im=fondo
        else:
            im=im.convert('RGB')
        im.thumbnail((1800,1800))
        salida=BytesIO();im.save(salida,'JPEG',quality=85,optimize=True)
        escala=min(ancho/im.width,alto/im.height,1.0)
        salida.seek(0);img=Image(salida,width=im.width*escala,height=im.height*escala)
        img.hAlign='CENTER'
        return img
    except Exception:
        return None


def construir_hoja(s,fotografias=()):
    """Hoja de calibración: informe principal con el formato de la hoja física (encabezado, fecha y cliente, datos generales de transmisión,
    shims, datos de calibración con las 16 mediciones, observaciones, firmas y QR) y, después, los anexos visuales numerados."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader,simpleSplit
    from reportlab.pdfgen import canvas as lienzos
    from reportlab.platypus import BaseDocTemplate,PageTemplate,Frame,Paragraph,Table,TableStyle,Spacer,KeepTogether,PageBreak,CondPageBreak,Flowable
    from reportlab.platypus.flowables import HRFlowable
    width,height=A4;margen=32;W=width-2*margen
    MARINO,ACERO,SUAVE,LINEA=(colors.HexColor(h) for h in ('#16283d','#315d87','#eef3f7','#c9d6e2'))
    GRIS,AMBAR,PEND_BG,PEND_TX=(colors.HexColor(h) for h in ('#5b6b7b','#e07b00','#fff4d6','#8a5a00'))
    base=ParagraphStyle('base',fontName='Helvetica',fontSize=8.5,leading=11,textColor=MARINO)
    etq=ParagraphStyle('etq',parent=base,fontName='Helvetica-Bold',fontSize=7,leading=9,textColor=GRIS)
    val=ParagraphStyle('val',parent=base,fontSize=9,leading=11)
    grande=ParagraphStyle('grande',parent=base,fontName='Helvetica-Bold',fontSize=11,leading=13,alignment=TA_CENTER)
    centro=ParagraphStyle('centro',parent=etq,alignment=TA_CENTER)
    pend=ParagraphStyle('pend',parent=base,fontName='Helvetica-Oblique',textColor=PEND_TX)
    pendc=ParagraphStyle('pendc',parent=pend,alignment=TA_CENTER)
    nota=ParagraphStyle('nota',parent=base,fontSize=7.5,leading=10,textColor=GRIS)
    mini=ParagraphStyle('mini',parent=centro,fontName='Helvetica',fontSize=6.5,leading=8)
    barra=ParagraphStyle('barra',parent=base,fontName='Helvetica-Bold',fontSize=8.5,leading=11,textColor=colors.white)
    mono=ParagraphStyle('mono',parent=base,fontName='Courier',fontSize=6.5,leading=8,textColor=GRIS)
    def esc(v): return escape(str(v)).replace('\n','<br/>')
    estado=dict(pendientes=False)
    def dato(v,estilo=val,vacio='No registrado'):
        """Celda de dato: lo que falta se marca en ámbar para que no pase inadvertido."""
        if v in (None,''):
            estado['pendientes']=True;return Paragraph(vacio,pend if estilo is val else pendc),True
        return Paragraph(esc(v),estilo),False
    def tabla(filas,anchos,extra=(),padding=3):
        datos=[];fondos=[]
        for i,fila in enumerate(filas):
            out=[]
            for j,celda in enumerate(fila):
                contenido,falta=celda if isinstance(celda,tuple) else (celda,False)
                if falta: fondos.append(('BACKGROUND',(j,i),(j,i),PEND_BG))
                out.append(contenido)
            datos.append(out)
        t=Table(datos,colWidths=anchos,hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('GRID',(0,0),(-1,-1),.4,LINEA),('TOPPADDING',(0,0),(-1,-1),padding),
            ('BOTTOMPADDING',(0,0),(-1,-1),padding),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),*fondos,*extra]))
        return t
    def seccion(titulo,keep=True):
        t=Table([[Paragraph(esc(titulo),barra)]],colWidths=[W],hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),MARINO),('LINEBEFORE',(0,0),(0,-1),4,AMBAR),('TOPPADDING',(0,0),(-1,-1),3),
            ('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),9)]))
        t.keepWithNext=keep;return t
    def aviso(texto,fondo=SUAVE,borde=ACERO):
        t=Table([[Paragraph(esc(texto),nota)]],colWidths=[W],hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),fondo),('LINEBEFORE',(0,0),(0,-1),2.5,borde),('TOPPADDING',(0,0),(-1,-1),3),
            ('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),8)]))
        return t
    logo=Path(__file__).parent/'static'/'masiscam'/'img'/'logo_transparente.png'
    def marco(c,pagina,total):
        c.saveState()
        if logo.exists(): c.drawImage(ImageReader(str(logo)),margen,height-70,width=135,height=46,preserveAspectRatio=True,mask='auto')
        c.setFillColor(MARINO);c.setFont('Helvetica-Bold',13);c.drawRightString(width-margen,height-38,'HOJA DE CALIBRACIÓN')
        c.setFillColor(GRIS);c.setFont('Helvetica',8)
        c.drawRightString(width-margen,height-52,f"{s['numero']}  ·  Revisión {s['revision']}  ·  PDF v{s['version']}")
        c.setStrokeColor(MARINO);c.setLineWidth(1.6);c.line(margen,height-77,width-margen,height-77)
        c.setStrokeColor(AMBAR);c.setLineWidth(3.2);c.line(margen,height-77,margen+78,height-77)
        c.setStrokeColor(LINEA);c.setLineWidth(.6);c.line(margen,40,width-margen,40)
        c.setFillColor(GRIS);c.setFont('Helvetica',6.8)
        c.drawString(margen,28,'MASISCAM | Documento técnico elaborado por TALLER. No es certificado metrológico ni afirma conformidad técnica.')
        c.drawRightString(width-margen,28,f'Página {pagina} de {total}')
        c.restoreState()
    class Lienzo(lienzos.Canvas):
        """Lienzo de dos pasadas: el encabezado y el pie conocen el total de páginas (principal + anexos)."""
        def __init__(self,*a,**k):
            super().__init__(*a,**k);self._paginas=[]
        def showPage(self):
            self._paginas.append(dict(self.__dict__));self._startPage()
        def save(self):
            total=len(self._paginas)
            for pagina in self._paginas:
                self.__dict__.update(pagina);marco(self,self._pageNumber,total);super().showPage()
            super().save()
    class Relleno(Flowable):
        """Empuja el bloque de firmas y QR hacia el pie de la página para aprovechar todo el espacio."""
        def __init__(self,reservar): super().__init__();self.reservar=reservar
        def wrap(self,aw,ah): return aw,max(0,ah-self.reservar-2)
        def draw(self): pass
    class Renglones(Flowable):
        """Cuadro de observaciones con renglones, como la hoja física: ocupa el espacio libre sin invadir el bloque de firmas y QR."""
        def __init__(self,lineas,reservar,sin_texto):
            super().__init__();self.lineas=lineas;self.reservar=reservar;self.sin_texto=sin_texto
        def wrap(self,aw,ah):
            self.ancho=aw;self.filas=min(max(len(self.lineas),3,int((ah-self.reservar)//14)),12)
            return aw,self.filas*14
        def draw(self):
            c=self.canv;alto=self.filas*14
            c.setStrokeColor(LINEA);c.setLineWidth(.4);c.rect(0,0,self.ancho,alto)
            for i in range(1,self.filas): c.line(0,i*14,self.ancho,i*14)
            c.setFont('Helvetica-Oblique' if self.sin_texto else 'Helvetica',8.5);c.setFillColor(PEND_TX if self.sin_texto else MARINO)
            for i,linea in enumerate(self.lineas): c.drawString(7,alto-(i+1)*14+4,linea)

    rec=s['recepcion'];eq=s.get('equipo',{});pv={r['codigo']:r for r in s['parametros']}
    verif=s.get('verificacion',{});aprobadas=verif.get('estado')=='TOLERANCIA_APROBADA'
    def valor(codigo,estilo=grande):
        r=pv.get(codigo)
        if not r or r.get('valor') is None: return dato(None,estilo,'Pendiente')
        return dato(f"{r['valor']} {r.get('unidad') or ''}".strip(),estilo)
    historia=[Paragraph('Documento técnico elaborado y generado por TALLER. No requiere aprobación de ADMINISTRADOR.',nota),Spacer(1,3)]
    if s.get('motivo_revision'):
        historia += [aviso(f"Revisión {s['revision']} · motivo: {s['motivo_revision']}",PEND_BG,AMBAR),Spacer(1,5)]
    # 2. Bloque superior: día, mes, año, hora y cliente
    d=_fecha_local(s.get('fecha'))
    historia += [tabla([[Paragraph(t,centro) for t in ('Día','Mes','Año','Hora')]+[Paragraph('Cliente',etq)],
        [dato(f'{d.day:02}' if d else None,grande,'—'),dato(MESES[d.month-1].capitalize() if d else None,grande,'—'),dato(str(d.year) if d else None,grande,'—'),
         dato(d.strftime('%H:%M') if d else None,grande,'—'),dato(rec.get('cliente'),ParagraphStyle('cli',parent=val,fontName='Helvetica-Bold',fontSize=10,leading=12))]],
        [W*.09,W*.17,W*.1,W*.1,W*.54],[('BACKGROUND',(0,0),(-1,0),SUAVE),('BOX',(0,0),(-1,-1),.9,MARINO)],padding=2.5),Spacer(1,5)]
    # 3. Datos generales de transmisión
    def fila_dg(a,va,b,vb): return [Paragraph(a,etq),dato(va),Paragraph(b,etq),dato(vb)]
    historia += [seccion('DATOS GENERALES DE TRANSMISIÓN'),
        tabla([fila_dg('Equipo',eq.get('nombre') or rec.get('equipo'),'Tipo de equipo',s.get('tipo_equipo')),
            fila_dg('Marca',eq.get('marca'),'Modelo',eq.get('modelo')),fila_dg('N.º de serie',eq.get('numero_serie'),'Ratio',eq.get('ratio')),
            fila_dg('Potencia (HP)',eq.get('potencia_hp'),'RPM',eq.get('rpm')),fila_dg('Rotación',eq.get('rotacion'),'Factor de servicio',eq.get('service_factor'))],
            [W*.16,W*.34,W*.16,W*.34],[('BACKGROUND',(0,0),(0,-1),SUAVE),('BACKGROUND',(2,0),(2,-1),SUAVE)],padding=2),Spacer(1,5)]
    # 4. Datos de shims
    historia += [seccion('DATOS DE SHIMS'),
        tabla([[Paragraph(t,centro) for t in ('Vertical superior','Vertical inferior','Horizontal')],[valor('SHIM-VS'),valor('SHIM-VI'),valor('SHIM-H')]],
            [W/3]*3,[('BACKGROUND',(0,0),(-1,0),SUAVE)],padding=3),Spacer(1,5)]
    # 5. Datos de calibración y las 16 mediciones
    unidades=s.get('unidades') or {}
    texto_unidades=dato(f"Longitud: {unidades.get('longitud') or 'sin unidad'}\nPresión: {unidades.get('presion') or 'sin unidad'}" if unidades else None,
        ParagraphStyle('uni',parent=centro,fontName='Helvetica',fontSize=7.5,leading=9.5),'Pendiente')
    medidas=[]
    for n,r in enumerate(s['mediciones'],1):
        v,falta=dato(f"{r['valor']} {r.get('unidad') or ''}".strip() if r.get('valor') is not None else None,grande,'Pendiente')
        celda=[Paragraph(f'Medición {n}',centro),v]
        evaluacion=r.get('evaluacion','')
        if aprobadas and evaluacion.startswith('Dentro'): celda.append(Paragraph('<font color="#1f6f43">Dentro del rango aprobado</font>',mini))
        elif aprobadas and evaluacion.startswith('Fuera'): celda.append(Paragraph('<font color="#a12622">Fuera del rango aprobado</font>',mini))
        elif aprobadas and evaluacion not in ('','Pendiente'): celda.append(Paragraph('No se evalúa con el criterio aprobado',mini))
        medidas.append((celda,falta))
    while len(medidas)%4: medidas.append(('',False))
    cuadricula=[medidas[i:i+4] for i in range(0,len(medidas),4)]
    historia += [seccion('DATOS DE CALIBRACIÓN'),
        tabla([[Paragraph(t,centro) for t in ('Backlash','Tolerancia','Calibración total','Mínimo','Máximo')],
            [valor('BACKLASH'),valor('TOLERANCIA'),valor('CAL-TOTAL'),valor('MINIMO'),valor('MAXIMO')],
            [Paragraph('Presión eje vertical',centro),'',Paragraph('Presión eje horizontal',centro),'',Paragraph('Unidades usadas',centro)],
            [valor('PRES-V'),'',valor('PRES-H'),'',texto_unidades]],
            [W/5]*5,[('BACKGROUND',(0,0),(-1,0),SUAVE),('BACKGROUND',(0,2),(-1,2),SUAVE),('SPAN',(0,2),(1,2)),('SPAN',(2,2),(3,2)),('SPAN',(0,3),(1,3)),('SPAN',(2,3),(3,3))],padding=3),
        tabla([[Paragraph('Mediciones (1 a 16)',etq),'','','']]+cuadricula,[W/4]*4,
            [('SPAN',(0,0),(-1,0)),('BACKGROUND',(0,0),(-1,0),SUAVE),('ALIGN',(0,1),(-1,-1),'CENTER')],padding=2)]
    if aprobadas:
        historia += [Spacer(1,3),aviso(f"{verif.get('texto','')} Tolerancias del protocolo {verif.get('protocolo','')} v{verif.get('version','')} · huella {verif.get('huella','')}")]
    else:
        historia += [Spacer(1,3),aviso(verif.get('texto') or 'Sin tolerancia aprobada: esta hoja registra mediciones. No se afirma conformidad técnica.')]
    for advertencia in s.get('advertencias',[]): historia += [Spacer(1,2),aviso(advertencia,PEND_BG,AMBAR)]
    if estado['pendientes']: historia += [Spacer(1,2),aviso('Los recuadros en ámbar son datos pendientes o no registrados; no se han completado ni supuesto.',PEND_BG,AMBAR)]
    historia.append(Spacer(1,5))
    # 7. Firmas y QR
    evidencias=s['evidencias']
    tecnico=s.get('tecnico_nombre') or s.get('tecnico')
    def firma(titulo,nombre,pie):
        return [Paragraph(titulo,etq),Spacer(1,22),HRFlowable(width='100%',thickness=.7,color=MARINO,spaceBefore=0,spaceAfter=2),
            Paragraph(esc(nombre) if nombre else 'Nombre y firma',val if nombre else nota),Paragraph(esc(pie),nota)]
    payload='|'.join(['MASISCAM',str(s['numero']),f"CAL-R{s['revision']}",f"PDF-V{s['version']}"]+([f"SHA256:{s['huella']}"] if s.get('huella') else []))
    widget=QrCodeWidget(payload,barLevel='M');b=widget.getBounds();lado=50
    qr=Drawing(lado,lado,transform=[lado/(b[2]-b[0]),0,0,lado/(b[3]-b[1]),0,0]);qr.add(widget);qr.hAlign='CENTER'
    codigo=(s.get('huella') or '')[:16] or 'sin huella'
    caja=Table([[firma('Firma del técnico responsable',tecnico,f"Fecha: {_fecha_legible(s.get('fecha'))}" if s.get('fecha') else 'Fecha: pendiente'),'',
        firma('Recibido por (opcional)','',''),'',[qr,Paragraph('Código de verificación',centro),Paragraph(esc(codigo),ParagraphStyle('cod',parent=mono,alignment=TA_CENTER))]]],
        colWidths=[W*.33,W*.025,W*.33,W*.025,W*.29],hAlign='LEFT')
    caja.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(0,0),.5,LINEA),('BOX',(2,0),(2,0),.5,LINEA),('BOX',(4,0),(4,0),.5,LINEA),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),('ALIGN',(4,0),(4,0),'CENTER')]))
    reservar=caja.wrap(W,10000)[1]
    # 6. Observaciones (con renglones, como la hoja física)
    obs=str(s.get('observaciones') or '').strip()
    renglones=[]
    for parrafo in obs.split('\n'):
        renglones += simpleSplit(parrafo,'Helvetica',8.5,W-14) or ['']
    renglones=renglones if obs else ['Sin observaciones registradas']
    if len(renglones)>12:   # observaciones extensas: tabla que puede continuar en la página siguiente
        cuerpo=Table([[r] for r in renglones],colWidths=[W],rowHeights=14,hAlign='LEFT')
        cuerpo.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),8.5),('TEXTCOLOR',(0,0),(-1,-1),MARINO),
            ('LINEBELOW',(0,0),(-1,-1),.4,LINEA),('BOX',(0,0),(-1,-1),.4,LINEA),('VALIGN',(0,0),(-1,-1),'BOTTOM'),('LEFTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),2)]))
    else:
        cuerpo=None
    historia += [CondPageBreak(90),seccion('OBSERVACIONES',keep=False),cuerpo or Renglones(renglones,reservar+26,not obs),Spacer(1,5),
        Paragraph(f"Anexos: {len(evidencias)} adjunto(s) al final de este informe (Anexo 1 a Anexo {len(evidencias)})." if evidencias else 'Anexos: ninguno registrado en esta hoja.',nota),Spacer(1,4)]
    historia += [Relleno(reservar),caja]
    # 8. Anexos: después del documento principal, numerados y con evidencia visual
    if evidencias:
        imagenes={item['documento']:data for item,data in fotografias}
        historia += [PageBreak(),seccion('ANEXOS'),Spacer(1,3),
            Paragraph('Evidencias y respaldos registrados por TALLER para esta hoja. Cada anexo conserva la huella SHA-256 del documento original.',nota),Spacer(1,6)]
        for n,item in enumerate(evidencias,1):
            es_foto=item.get('clase')=='FOTO'
            titulo='Fotografía' if es_foto else 'Respaldo documental'
            leyenda=str(item.get('leyenda') or '').strip()
            descripcion=(leyenda[:240]+'…') if len(leyenda)>240 else (leyenda or 'Sin descripción registrada')
            cabecera=Table([[Paragraph(f'ANEXO {n}',barra),Paragraph(esc(titulo.upper()),ParagraphStyle('der',parent=barra,alignment=2))]],colWidths=[W*.5,W*.5],hAlign='LEFT')
            cabecera.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),ACERO),('LINEBEFORE',(0,0),(0,-1),4,AMBAR),('TOPPADDING',(0,0),(-1,-1),3),
                ('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(0,0),9),('RIGHTPADDING',(-1,0),(-1,0),8)]))
            img=_imagen_anexo(imagenes[item['documento']],W-16,220) if es_foto and item['documento'] in imagenes else None
            if img is not None:
                visual=img
            else:
                texto=('Imagen no legible en este PDF; consulte el documento original en el expediente.' if es_foto else
                    'Documento adjunto al expediente (no se incrusta en este PDF); consulte el original.')
                visual=Table([[Paragraph('FOTO' if es_foto else 'DOC',ParagraphStyle('ico',parent=grande,fontSize=15,leading=18,textColor=ACERO))],[Paragraph(texto,pendc if es_foto else centro)]],
                    colWidths=[W-16],rowHeights=[46,22])
                visual.setStyle(TableStyle([('BOX',(0,0),(-1,-1),.5,LINEA),('BACKGROUND',(0,0),(-1,-1),SUAVE if not es_foto else PEND_BG),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
            cuerpo=Table([[Paragraph('Descripción',etq)],[Paragraph(esc(descripcion),val)],[visual],
                [Paragraph(f"Documento {item['documento']} · SHA-256 {item['hash']}",mono)]],colWidths=[W],hAlign='LEFT')
            cuerpo.setStyle(TableStyle([('BOX',(0,0),(-1,-1),.5,LINEA),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
                ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('ALIGN',(0,2),(0,2),'CENTER'),('TOPPADDING',(0,2),(0,2),6),('BOTTOMPADDING',(0,2),(0,2),6)]))
            historia += [KeepTogether([cabecera,cuerpo]),Spacer(1,10)]
    stream=BytesIO()
    doc=BaseDocTemplate(stream,pagesize=A4,title='Hoja de calibración MASISCAM',author='MASISCAM')
    doc.addPageTemplates([PageTemplate(id='hoja',frames=[Frame(margen,46,W,height-46-82,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)])])
    doc.build(historia,canvasmaker=Lienzo)
    return stream.getvalue()
