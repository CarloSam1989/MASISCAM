"""La hoja de calibración es un documento técnico de TALLER: no requiere aprobación de ADMINISTRADOR.

Cambio aditivo y acotado de las guardas de base de datos (0026 calibración y 0028 informe final):

* estado nuevo FINALIZADA: solo con enviado_en, solo para hojas basadas en una ficha técnica (código FICHA-…) y solo con las
  16 mediciones y la fecha; no se exige instrumento ni resultado (el formato físico no los tiene).
* una calibración puede iniciarse sobre una ficha técnica de configuración sin validación de protocolo; los protocolos
  de ADMINISTRADOR siguen exigiendo su validación independiente y su aprobación del propietario.
* el informe final puede referirse a una calibración FINALIZADA solo si su PDF (tercer documento) ya fue GENERADO.

Todo lo demás (inmutabilidad tras el envío, borrado prohibido, decisiones independientes de las calibraciones con protocolo,
expedientes históricos) queda igual. No reescribe datos."""
import importlib

from django.db import migrations, models

CAL = importlib.import_module('masiscam.migrations.0026_guardas_calibracion')
FIN = importlib.import_module('masiscam.migrations.0028_guardas_cierre')
NOMBRES = {('calibracion', 'INSERT'): 'masiscam_c5_calibracion_insert',
           ('calibracion', 'UPDATE'): 'masiscam_c5_calibracion_update',
           ('informefinal', 'INSERT'): 'masiscam_f6_informefinal_insert'}

FICHA = "EXISTS (SELECT 1 FROM masiscam_plantillacalibracion fx WHERE fx.id=NEW.plantilla_id AND fx.codigo LIKE 'FICHA-%')"


def _sustituir(texto, pares):
    for antiguo, nuevo in pares:
        if texto.count(antiguo) != 1:
            raise RuntimeError(f'Guarda inesperada, no se puede ampliar: {antiguo[:60]}')
        texto = texto.replace(antiguo, nuevo)
    return texto


def nuevas(vendor):
    """(tabla, operación) → condición ampliada, partiendo exactamente de las guardas vigentes."""
    # Cada módulo agrupa sus propias guardas (c5 = calibración, f6 = cierre) y ambos incluyen la tabla calibracion: no se mezclan.
    cal = {(t, o): c for t, o, c in CAL.definitions(vendor)}
    fin = {(t, o): c for t, o, c in FIN.definitions(vendor)}
    resultado = {}
    resultado[('calibracion', 'INSERT')] = _sustituir(cal[('calibracion', 'INSERT')], [(
        'EXISTS (SELECT 1 FROM masiscam_validacionplantillacalibracion v WHERE v.plantilla_id=t.id)',
        "(t.codigo LIKE 'FICHA-%' OR EXISTS (SELECT 1 FROM masiscam_validacionplantillacalibracion v WHERE v.plantilla_id=t.id))")])
    resultado[('calibracion', 'UPDATE')] = _sustituir(cal[('calibracion', 'UPDATE')], [
        ("NEW.estado NOT IN ('NO_INICIADA','EN_PROCESO','PENDIENTE_REVISION','OBSERVADA','APROBADA','RECHAZADA')",
         "NEW.estado NOT IN ('NO_INICIADA','EN_PROCESO','PENDIENTE_REVISION','OBSERVADA','APROBADA','RECHAZADA','FINALIZADA')"),
        ("OR NEW.instrumentos='' OR NEW.resultados=''",
         "OR (NEW.estado<>'FINALIZADA' AND (NEW.instrumentos='' OR NEW.resultados=''))")])
    resultado[('calibracion', 'UPDATE')] += (" OR (NEW.estado='FINALIZADA' AND (NEW.enviado_en IS NULL OR NOT "
        + FICHA + "))")
    resultado[('informefinal', 'INSERT')] = _sustituir(fin[('informefinal', 'INSERT')], [(
        "c.estado='APROBADA')",
        "(c.estado='APROBADA' OR (c.estado='FINALIZADA' AND EXISTS (SELECT 1 FROM masiscam_emisioncalibracion ec WHERE ec.calibracion_id=c.id AND ec.estado='GENERADA'))))")])
    return resultado


def _aplicar(schema_editor, condiciones):
    vendor = schema_editor.connection.vendor
    for (tabla, operacion), condicion in condiciones.items():
        nombre = NOMBRES[(tabla, operacion)]
        mensaje = 'calibracion: informacion protegida' if nombre.startswith('masiscam_c5') else 'cierre: informacion protegida'
        if vendor == 'postgresql':
            schema_editor.execute(f"""CREATE OR REPLACE FUNCTION {nombre}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condicion}) THEN RAISE EXCEPTION '{mensaje}' USING ERRCODE='23514'; END IF;
                RETURN NEW; END $$""", params=None)   # sin parámetros: el LIKE contiene «%"
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {nombre}')
            schema_editor.execute(f"CREATE TRIGGER {nombre} BEFORE {operacion} ON masiscam_{tabla} WHEN ({condicion}) "
                                  f"BEGIN SELECT RAISE(ABORT,'{mensaje}'); END", params=None)


def instalar(apps, schema_editor):
    if schema_editor.connection.vendor not in {'postgresql', 'sqlite'}:
        raise RuntimeError('Calibración QA requiere PostgreSQL o SQLite.')
    _aplicar(schema_editor, nuevas(schema_editor.connection.vendor))


def restaurar(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    cal = {(t, o): c for t, o, c in CAL.definitions(vendor)}
    fin = {(t, o): c for t, o, c in FIN.definitions(vendor)}
    _aplicar(schema_editor, {('calibracion', 'INSERT'): cal[('calibracion', 'INSERT')], ('calibracion', 'UPDATE'): cal[('calibracion', 'UPDATE')],
                             ('informefinal', 'INSERT'): fin[('informefinal', 'INSERT')]})


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0048_guardas_concepto_tecnico')]
    operations = [
        migrations.AlterField(
            model_name='calibracion', name='estado',
            field=models.CharField(choices=[('NO_INICIADA', 'No iniciada'), ('EN_PROCESO', 'En proceso'),
                ('PENDIENTE_REVISION', 'Pendiente revision'), ('OBSERVADA', 'Observada'), ('APROBADA', 'Aprobada'),
                ('RECHAZADA', 'Rechazada'), ('FINALIZADA', 'Finalizada')], default='NO_INICIADA', max_length=20)),
        migrations.RunPython(instalar, restaurar),
    ]
