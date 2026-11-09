# Políticas de recepción: propuesta pendiente de aprobación

Actualización 2026-11-06: el propietario aprobó expresamente fotografía GENERAL, PLACA cuando exista, excepción SIN_PLACA justificada y ausencia de guía con motivo. El [cierre operativo local](cierre_operativo_fase2.md) reemplaza el bloqueo anterior para ese alcance. El contenido siguiente es el antecedente histórico; no implica aprobación de retención, publicación, correcciones u otras etapas.

Fecha: 2026-11-05. No activa reglas obligatorias ni constituye autorización comercial/técnica.

## Evidencia de aprobación

Se revisaron las siete fuentes de arquitectura/validación y `implementacion_controlada_fase2.md`. No se encontró aprobación expresa de las políticas siguientes. La decisión vigente del propietario es «Mantener finalización bloqueada hasta definir la política». El encargo de finalización describe estas reglas como propuestas y exige no activarlas sin aprobación. Por tanto, esta entrega no las impone ni habilita finalización, incluso si una recepción reúne todas las evidencias.

## Propuesta operativa para aprobación del propietario

| Política propuesta | Condición que deberá validarse si se aprueba | Estado actual |
| --- | --- | --- |
| Fotografía general obligatoria | Evidencia identificada como general, archivo válido y copia persistente íntegra | Pendiente; no se exige cantidad mínima ni se presume que cualquier FOTO es general |
| Fotografía de placa cuando exista | Registrar existencia de placa y evidencia clasificada como placa | Pendiente; el modelo actual conserva FOTO genérica sin inventar clasificación |
| Equipo sin placa | Excepción con motivo, usuario y fecha; no rellenar serie con valores ficticios | Pendiente; responsable facultado para aceptar la excepción por definir |
| Recepción sin guía de remisión | Motivo explícito y trazable, sin generar número/guía del cliente ficticios | Pendiente; no se exige todavía el motivo ni se considera excepción aprobada |
| Datos técnicos incompletos | Mostrar campos pendientes y completar posteriormente mediante mecanismo autorizado, sin alterar snapshots emitidos | Pendiente como política; los snapshots ya muestran vacíos como pendientes, sin relajar el alta de equipos existente |
| Trazabilidad | Usuario autenticado, empresa, cliente, equipo, fecha y condiciones de ingreso | Implementado en la recepción provisional; no equivale a finalización |

El propietario debe aprobar/rechazar las reglas y definir quién acepta excepciones de placa/guía, qué campos son obligatorios para finalizar y el procedimiento de corrección. La aprobación futura no puede inferirse de descargar esta muestra, subir fotografías o generar un PDF.

## Requisitos técnicos que ya se aplican

Permisos en servidor y aislamiento por empresa; correlativo único e inmutable; idempotencia; documento con versión, snapshot, copia persistente y SHA-256; lectura de vuelta del archivo antes de registrar generación; auditoría; conservación de emisiones; sincronización posterior con Drive mediante servicios existentes. Si el guardado o su verificación falla, no se vincula un Documento ni se marca la emisión como GENERADA. La finalización está bloqueada además por falta de aprobación de políticas.

## Alcance de esta entrega

Se mejora la guía A4 y se crea una muestra inequívocamente demostrativa, con espacios de firma vacíos. No se añaden firmas certificadas, tolerancias, aprobaciones ni un nuevo flujo técnico. No se crean campos/migraciones para políticas aún no aprobadas. El alta maestra de equipos conserva sus requisitos vigentes; gestionar un equipo nuevo sin placa/camaronera requerirá diseño aprobado antes de modificar esa funcionalidad.

La retención/restauración, publicación al cliente, correcciones y aceptación de la presentación definitiva siguen pendientes. Un PDF local íntegro permite sincronización posterior; no acredita por sí mismo recepción finalizada ni disponibilidad de respaldo remoto.
