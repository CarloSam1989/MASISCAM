"""ReportLab is imported only when rendering; no network/file URLs from users."""
from io import BytesIO
from xml.sax.saxutils import escape


def _construir_v1(snapshot, fotografias=()):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

    salida = BytesIO()
    styles = getSampleStyleSheet()
    p = lambda value: Paragraph(escape(str(value or "—")).replace("\n", "<br/>"), styles["BodyText"])
    story = [Paragraph("MASISCAM", styles["Title"]),
             Paragraph("GUÍA DE INGRESO", styles["Heading1"]),
             p(f"{snapshot['numero']} · Versión {snapshot['version']}"), Spacer(1, 12),
             p("CONSTANCIA PROVISIONAL. Política de finalización pendiente de validación. No constituye autorización comercial ni aprobación técnica."), Spacer(1, 12)]
    labels = [("empresa", "Empresa"), ("cliente", "Cliente"), ("ruc", "RUC"),
        ("equipo", "Equipo"), ("tipo_producto", "Producto"), ("marca", "Marca"),
        ("modelo", "Modelo"), ("serie", "Serie"), ("ratio", "Ratio"), ("potencia", "Potencia"),
        ("fecha", "Recepción"), ("responsable", "Responsable"), ("tipo", "Intervención"), ("guia", "Guía de remisión")]
    rows = [[p(label), p(snapshot.get(key))] for key, label in labels]
    tabla = Table(rows, colWidths=[125, 370], hAlign="LEFT")
    tabla.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf0f5")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(tabla)
    for key, title in [("condicion", "Condición de ingreso"), ("observaciones", "Observaciones")]:
        story += [Paragraph(title, styles["Heading2"]), p(snapshot.get(key))]
    story += [Paragraph("Evidencias registradas", styles["Heading2"])]
    for item in snapshot.get("evidencias", []):
        story.append(p(f"{item['tipo']} · Documento {item['documento_id']} · SHA-256: {item['documento__hash_sha256']}"))
    for index, contenido in enumerate(fotografias, 1):
        story += [Paragraph(f"Fotografía de recepción {index}", styles["Heading2"])]
        img = Image(BytesIO(contenido))
        factor = min(495 / img.imageWidth, 340 / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * factor, img.imageHeight * factor
        story += [img, Spacer(1, 12)]
    # Only frozen evidence read from persistent storage, never remote URLs.
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 25, f"MASISCAM | {snapshot['numero']} | v{snapshot['version']}")
        canvas.drawRightString(A4[0]-42, 25, f"Página {doc.page}")
        canvas.restoreState()
    SimpleDocTemplate(salida, pagesize=A4, leftMargin=42, rightMargin=42,
        topMargin=40, bottomMargin=45, title=f"Guía {snapshot['numero']}").build(story, onFirstPage=footer, onLaterPages=footer)
    return salida.getvalue()


def construir_guia(snapshot, fotografias=()):
    """Select the frozen template; never silently restyle reserved v1 guides."""
    version = snapshot.get("plantilla", "recepcion-v2")
    if version == "recepcion-v1":
        return _construir_v1(snapshot, fotografias)
    if version not in {"recepcion-v2", "recepcion-v3"}:
        raise ValueError("Versión de plantilla no soportada.")
    return _construir_v2(snapshot, fotografias)


def _construir_v2(snapshot, fotografias):
    from pathlib import Path
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image, KeepTogether)

    width, height = A4
    margin = 42
    available = width - margin * 2
    navy, blue = colors.HexColor("#16283d"), colors.HexColor("#315d87")
    pale, grey = colors.HexColor("#eef3f7"), colors.HexColor("#526170")
    body = ParagraphStyle("guide-body", fontName="Helvetica", fontSize=9, leading=13,
                          textColor=navy, spaceAfter=5, splitLongWords=True)
    small = ParagraphStyle("guide-small", parent=body, fontSize=8, leading=11)
    heading = ParagraphStyle("guide-section", parent=body, fontName="Helvetica-Bold",
                             fontSize=11, leading=15, spaceBefore=12, spaceAfter=7, keepWithNext=True)
    note = ParagraphStyle("guide-note", parent=small, borderColor=colors.HexColor("#d1dce5"),
                          borderWidth=.6, borderPadding=8, backColor=pale, spaceAfter=8)
    caption = ParagraphStyle("guide-caption", parent=small, alignment=TA_CENTER, spaceAfter=9)
    title = ParagraphStyle("guide-title", parent=heading, fontSize=20, leading=24, spaceBefore=0)

    def p(value, style=body):
        return Paragraph(escape(str(value if value is not None else "")).replace("\n", "<br/>"), style)

    def value(key, missing="No registrado"):
        result = snapshot.get(key)
        return str(result).strip() if result is not None and str(result).strip() else missing

    def section(text):
        return p(text, heading)

    def details(items, technical=False):
        rows = [[p(label, small), p(value(key, "Pendiente de identificación" if technical else "No registrado"))]
                for key, label in items]
        table = Table(rows, colWidths=[132, available - 132], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, -1), pale),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#dce4eb")),
        ]))
        return table

    demo = bool(snapshot.get("demostrativo"))
    confirmada = snapshot.get("plantilla") == "recepcion-v3" and snapshot.get("pendiente_validacion") is False
    logo = Path(__file__).resolve().parent / "static" / "masiscam" / "img" / "logo_transparente.png"
    logo_reader = ImageReader(str(logo)) if logo.is_file() else None

    def page_frame(canvas, doc):
        canvas.saveState()
        if logo_reader:
            canvas.drawImage(logo_reader, margin, height - 69, width=144, height=54,
                             preserveAspectRatio=True, anchor="sw", mask="auto")
        else:
            canvas.setFont("Helvetica-Bold", 18)
            canvas.setFillColor(navy)
            canvas.drawString(margin, height - 45, "MASISCAM")
        canvas.setFillColor(grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(width - margin, height - 35, "RECEPCIÓN DE EQUIPOS")
        canvas.drawRightString(width - margin, height - 49,
                              f"{snapshot['numero']} | v{snapshot['version']}")
        canvas.setStrokeColor(blue)
        canvas.line(margin, height - 76, width - margin, height - 76)
        canvas.setStrokeColor(colors.HexColor("#d1dce5"))
        canvas.line(margin, 42, width - margin, 42)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(margin, 29, "DEMOSTRACIÓN - DATOS FICTICIOS" if demo else
                         ("Recepción confirmada - sin aprobación técnica/comercial" if confirmada else "Constancia provisional - sin aprobación técnica/comercial"))
        canvas.drawRightString(width - margin, 29, f"Página {doc.page}")
        canvas.restoreState()

    story = [p("GUÍA DE INGRESO", title), p(f"Expediente {snapshot['numero']} | Versión {snapshot['version']}"), Spacer(1, 5)]
    if demo:
        story.append(p("DEMOSTRACIÓN - DATOS FICTICIOS. Fotografías ilustrativas; no representan una recepción real.", note))
    story.append(p("RECEPCIÓN CONFIRMADA. Este documento acredita el ingreso, sin autorización comercial ni aprobación técnica." if confirmada else
                   "CONSTANCIA PROVISIONAL. Finalización pendiente de políticas aprobadas. Este documento no constituye autorización comercial ni aprobación técnica.", note))
    if confirmada:
        story += [section("Confirmación de recepción"), details([
            ("confirmado_en", "Confirmación"), ("confirmado_por", "Confirmado por"), ("politica", "Política aplicada"),
            ("placa", "Placa"), ("motivo_sin_placa", "Justificación sin placa"), ("motivo_sin_guia", "Motivo sin guía")])]
    story += [section("1. Recepción y cliente"), details([
        ("empresa", "Empresa"), ("fecha", "Fecha de recepción"), ("responsable", "Responsable de recepción"),
        ("cliente", "Cliente / razón social"), ("ruc", "RUC / identificación"), ("tipo", "Tipo de intervención")])]
    story += [section("2. Identificación del equipo"), details([
        ("equipo", "Número de equipo"), ("tipo_producto", "Tipo de producto"), ("marca", "Marca"),
        ("modelo", "Modelo"), ("serie", "Número de serie")], technical=True)]
    story += [section("3. Características técnicas disponibles"), details([
        ("ratio", "Ratio"), ("potencia", "Potencia"), ("rpm", "RPM"),
        ("service_factor", "Factor de servicio"), ("rotacion", "Rotación"), ("tipo_aceite", "Tipo de aceite")], technical=True)]
    story.append(p("Los campos sin información se identifican como pendientes. No se han inferido valores técnicos ni condiciones de conformidad.", small))
    story += [section("4. Condición de ingreso y observaciones"),
              p("Condición de ingreso", ParagraphStyle("condition", parent=small, fontName="Helvetica-Bold")),
              p(value("condicion")), p("Observaciones", ParagraphStyle("observations", parent=small, fontName="Helvetica-Bold")),
              p(value("observaciones", "Sin observaciones adicionales registradas"))]
    story += [section("5. Guía de remisión"), p(value("guia", ("Sin guía: " + value("motivo_sin_guia")) if confirmada else "No registrada. La política de recepción sin guía permanece pendiente de aprobación."))]
    story.append(section("6. Fotografías y evidencias de ingreso"))
    if not fotografias:
        story.append(p("Sin fotografías adjuntas en esta constancia provisional. No implica aprobación de una excepción."))
    for index, content in enumerate(fotografias, 1):
        img = Image(BytesIO(content))
        factor = min(available / img.imageWidth, 225 / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * factor, img.imageHeight * factor
        story.append(KeepTogether([p(f"Fotografía de recepción {index}" + (" - ilustración demostrativa" if demo else ""), caption),
                                   img, Spacer(1, 12)]))
    for item in snapshot.get("evidencias", []):
        story.append(p(f"{item.get('clasificacion') or item['tipo']} | Documento {item['documento_id']} | SHA-256: {item['documento__hash_sha256']}", small))
    for item in snapshot.get("fotos_existentes", []):
        story.append(p(f"{item['clasificacion']} | Foto original: {item['nombre_original']} | SHA-256: {item['hash_sha256']}", small))
    # Blank signature spaces, never rasterized signatures or certified signing.
    signatures = Table([
        [p("ENTREGA", heading), p("RECEPCIÓN", heading)],
        [p("Nombre: __________________________", small), p("Nombre: __________________________", small)],
        [p("Firma: ___________________________", small), p("Firma: ___________________________", small)],
        [p("Fecha: ___________________________", small), p("Fecha: ___________________________", small)],
    ], colWidths=[available / 2, available / 2], hAlign="LEFT")
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#d1dce5")),
        ("LINEBEFORE", (1, 0), (1, -1), .6, colors.HexColor("#d1dce5"))]))
    story += [Spacer(1, 12), KeepTogether([section("7. Espacios para firmas de entrega y recepción"), signatures,
              p("Espacios sin firmar. No se incluye firma digital certificada ni se simula aceptación o aprobación.", small)])]
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=92, bottomMargin=57, title=f"Guía de ingreso {snapshot['numero']}",
        author="MASISCAM", subject="Demostración ficticia" if demo else ("Recepción confirmada" if confirmada else "Recepción provisional")).build(
            story, onFirstPage=page_frame, onLaterPages=page_frame)
    return output.getvalue()
