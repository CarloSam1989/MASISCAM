"""Public demonstration only: no Django setup, database, Drive or real data."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from masiscam.recepcion_pdf import construir_guia


def ilustracion(placa=False):
    image = Image.new("RGB", (1200, 680), "#edf3f7")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    small = ImageFont.load_default(size=25)
    draw.rounded_rectangle((35, 35, 1165, 645), radius=24, fill="white", outline="#bacbd8", width=3)
    draw.text((75, 65), "IMAGEN DEMOSTRATIVA - NO ES UNA FOTOGRAFIA REAL", font=small, fill="#315d87")
    if placa:
        draw.rounded_rectangle((190, 180, 1010, 510), radius=12, fill="#dce4eb", outline="#526170", width=6)
        for line, y in [("PLACA DEMO", 220), ("IDENTIFICACION PENDIENTE", 305), ("SIN VALORES TECNICOS SIMULADOS", 390)]:
            draw.text((250, y), line, font=font, fill="#16283d")
    else:
        draw.rounded_rectangle((310, 230, 860, 490), radius=25, fill="#315d87")
        draw.rectangle((240, 320, 310, 395), fill="#8194a5")
        draw.rectangle((860, 300, 975, 415), fill="#8194a5")
        draw.ellipse((440, 255, 680, 470), fill="#ecf1f5", outline="#16283d", width=12)
        draw.ellipse((510, 315, 610, 415), fill="#8194a5")
        draw.text((310, 530), "EQUIPO FICTICIO - SOLO MAQUETACION", font=small, fill="#16283d")
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def main():
    from pypdf import PdfReader
    import pymupdf

    images = [ilustracion(), ilustracion(placa=True)]
    confirmada = "--confirmada" in sys.argv
    snapshot = dict(
        numero="EXP-DEMO-00000001", version=1, plantilla="recepcion-v2", demostrativo=True,
        empresa="MASISCAM - MUESTRA DEMOSTRATIVA", cliente="CLIENTE FICTICIO - DEMO",
        ruc="DEMO - NO VÁLIDO", equipo="EQUIPO FICTICIO DEMO-001", tipo_producto="REDUCTOR",
        marca="MARCA DEMOSTRATIVA", modelo="MODELO DEMOSTRATIVO", serie="", ratio="", potencia="", rpm="",
        service_factor="", rotacion="", tipo_aceite="", fecha="2026-11-05 10:30 (America/Bogota)",
        responsable="USUARIO FICTICIO - OFICINA DEMO", tipo="MANTENIMIENTO",
        condicion="Descripción demostrativa del aspecto visible y los accesorios recibidos. No representa una inspección técnica ni un equipo real.",
        observaciones="Muestra de presentación. La ausencia de datos se muestra como pendiente; no se han inventado valores de potencia, ratio, RPM o tolerancias. Las ilustraciones no acreditan una recepción real.",
        guia="", pendiente_validacion=True, evidencias=[])
    if confirmada:
        snapshot.update(plantilla="recepcion-v3", pendiente_validacion=False, version=2,
            fecha="2026-11-06 10:30 (America/Bogota)", confirmado_en="2026-11-06 10:35 (America/Bogota)",
            confirmado_por="USUARIO FICTICIO - OFICINA DEMO", politica="recepcion-2026-11-06",
            placa="CON_PLACA", motivo_sin_placa="", motivo_sin_guia="MOTIVO DEMOSTRATIVO: entrega sin guía de remisión.")
    for index, image in enumerate(images, 1):
        snapshot["evidencias"].append(dict(tipo="FOTO", documento_id=f"DEMO-{index}", documento__hash_sha256=hashlib.sha256(image).hexdigest()))
        if confirmada:
            snapshot["evidencias"][-1]["clasificacion"] = "GENERAL" if index == 1 else "PLACA"
    pdf = construir_guia(snapshot, images)
    output = ROOT.parent / "output" / "pdf"
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("MASISCAM_Guia_Confirmacion_DEMOSTRATIVA.pdf" if confirmada else "MASISCAM_Guia_Ingreso_DEMOSTRATIVA.pdf")
    target.write_bytes(pdf)
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    for token in ["DATOS FICTICIOS", "ENTREGA", "RECEPCIÓN", "Firma:", "Pendiente de identificación", snapshot["numero"]]:
        assert token in text, token
    assert all(abs(float(p.mediabox.width) - 595.276) < .02 and abs(float(p.mediabox.height) - 841.89) < .02 for p in reader.pages)
    assert any(page.images for page in reader.pages)
    assert not reader.get_fields(), "La muestra no debe simular formularios firmados"
    if confirmada:
        assert "RECEPCIÓN CONFIRMADA" in text and "CONSTANCIA PROVISIONAL" not in text
    qa = ROOT / "artifacts" / "recepcion-qa" / ("activacion-local" if confirmada else "finalizacion")
    qa.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(stream=pdf, filetype="pdf")
    for index, page in enumerate(document, 1):
        page.get_pixmap(matrix=pymupdf.Matrix(1.15, 1.15)).save(str(qa / f"muestra-pagina-{index}.png"))
    report = dict(path=str(target), pages=len(reader.pages), sha256=hashlib.sha256(pdf).hexdigest(),
                  a4=True, datos_ficticios=True, firma_digital=False)
    (qa / "muestra-verificacion.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
