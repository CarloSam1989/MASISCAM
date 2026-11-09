# Implementación local de Fase 5

Fecha: 2026-11-06, America/Bogota. **Componentes técnicos locales implementados y verificados en QA; listos para revisión. Calibración operativa bloqueada por aprobaciones pendientes. Sin autorización de producción.**

## Autorización, fuentes y límites

El adjunto «MASISCAM — FASE 5: CALIBRACIÓN, MEDICIONES Y APROBACIÓN TÉCNICA» autoriza implementar y probar los componentes técnicos locales. Se revisaron arquitectura, diccionario, flujo de etapas e implementaciones de Fases 3/4, servicios y migraciones hasta 0024. No se encontró una plantilla industrial validada de las 16 posiciones ni un procedimiento comercial aprobado. Las propuestas antiguas del diccionario no se tratan como aprobación industrial.

Se mantiene intacta `MASISCAM_POLITICA_COMERCIAL_LOCAL = None` y no se modifica ni elude `operacion_services.exigir_autorizacion`. Diagnóstico, reparación registrada, recepción de repuestos o PDF no sustituyen autorización comercial válida. No se han aprobado tolerancias, unidades, fórmulas, procedimientos de bombas/reductores o excepciones de aceptación.

`MASISCAM_CALIBRACION_LOCAL` queda deshabilitada por defecto, condicionada a DEBUG en configuración habitual; solo el sandbox explícito `config.recepcion_local_settings` la habilita sobre su base/media locales propios, con Drive deshabilitado. No se inició ni reinició el despliegue existente.

La lista `MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL = []` permanece vacía. Una validación operativa requiere aprobación expresa ligada a la huella de empresa/código/versión/tipo/definición/Documento/hash exactos y responsable PLANTA independiente. No existe formulario que conceda esa aprobación del propietario ni un booleano genérico que apruebe todas las plantillas. Aprobar código/QA o subir un manual no aprueba un protocolo.

Las pruebas usan exclusivamente expedientes, personas, protocolos, unidades, límites y decisiones **FICTICIOS QA**, rotulados sin validez industrial, en bases desechables. Sus anclas de aprobación y política comercial se habilitan mediante override temporal de pruebas; no se migran ni instalan en datos/configuración operativos. No se crean aprobaciones ficticias en bases reales.

## Modelos y reutilización

Ocho entidades en `calibracion_models.py`, importadas desde `models.py`:

| Entidad | Contrato implementado |
| --- | --- |
| PlantillaCalibracion | Empresa, código y versión únicos, tipo REDUCTOR/BOMBA, título, definición configurable, fuente Documento, autor, clave y procedencia; contenido siempre inmutable |
| ValidacionPlantillaCalibracion | Una validación por versión exacta, PLANTA independiente del autor, fundamento, fecha y ancla de aprobación congelada |
| AplicabilidadCalibracion | Plantilla/equipo explícitos de la misma empresa y tipo, fundamento y autor; no se aplica automáticamente por marca, modelo o ratio |
| Calibracion | Expediente, diagnóstico y autorización exactos, plantilla, snapshot de actividades/resultados de reparación, técnico principal, revisión/procedencia, fecha, parámetros, instrumentos, observaciones/resultados, estado y versión de edición |
| MedicionCalibracion | Punto definido en plantilla, Decimal nullable y observaciones; unicidad revisión/punto. Vacío significa pendiente, nunca cero |
| EvidenciaCalibracion | Documento privado, FOTO o documento de instrumento, leyenda, autor y clave. Evidencias enviadas congeladas |
| DecisionCalibracion | Una decisión por revisión enviada, evaluación manual por punto, motivo, PLANTA responsable y fecha; historial inmutable |
| EmisionCalibracion | Revisión fuente exacta, versión, snapshot/huella, Documento privado, SHA-256, estado/error, responsable y clave idempotente |

Se reutilizan Empresa/Perfil/RolMasiscam, Equipo, Cliente, RegistroEquipo, ExpedienteMantenimiento, asignación técnica, diagnóstico, autorización comercial, actividades/resultados/revisión de reparación, Documento, Auditoria, ReportLab y servicios Drive/Celery existentes. No se crea otro equipo/QR, contador, repositorio binario, catálogo de clientes, inventario ni autorización comercial.

Las decisiones y emisiones se vinculan a una revisión exacta. Corrección crea nueva revisión autorizada por PLANTA con motivo y técnico asignado vigente; inicia valores vacíos para no presentar mediciones anteriores como realizadas otra vez. No borra ni altera revisiones anteriores, incluso rechazadas o aprobadas.

## Definiciones y valores

La plantilla contiene dos listas configurables:

- `mediciones`: códigos únicos, nombre/punto físico, unidad, fuente y, solo cuando estén documentados, mínimo/máximo, tolerancia y criterio de evaluación.
- `parametros`: definiciones aplicables con código/nombre/unidad/fuente/obligatoriedad explícita y referencias opcionales. Permite representar shims, backlash, presión u otras magnitudes cuando la fuente realmente las defina; no se precargan ni se exige que apliquen a todo equipo.

El borrador de plantilla puede estar incompleto. Validarla exige exactamente 16 puntos distintos con nombre, unidad y fuente. No se generan nombres industriales, posiciones o secuencias predeterminadas. El orden es el de la fuente aprobada, no el de una fórmula del sistema. Si el protocolo real de un equipo no corresponde a estas 16 posiciones, debe revisarse el diseño con esa fuente; no completar posiciones inventadas.

Mínimo/máximo son opcionales; si ambos existen se exige mínimo ≤ máximo. Referencias decimales se configuran como texto exacto; no floats ni booleanos. La tolerancia y el criterio son texto documentado, nunca código ejecutable. La huella aprobada abarca toda la definición y el Documento fuente.

Valores se validan con Decimal, incluyendo cero y negativos; se rechazan NaN, infinito, texto inválido o precisión que obligue a redondear. La capacidad técnica del campo es Decimal(30,12): hasta 18 enteros y 12 decimales. **Es capacidad de almacenamiento, no resolución, incertidumbre ni precisión industrial aprobada.** Las escalas reales por magnitud/instrumento siguen pendientes del protocolo validado. El PDF muestra el valor decimal sin ceros de almacenamiento ni exponente artificial; no calcula ni modifica su valor.

Cada medición se lee de vuelta antes de confirmar el cambio: si el motor devuelve un valor distinto o no puede convertirlo exactamente, se revierte la transacción, sin fila nueva/auditoría de éxito/incremento de versión. Se informa la limitación y se solicita PostgreSQL para ese valor. La comprobación de magnitud usa una operación Decimal sin redondeo contextual, incluso en el extremo permitido del campo.

Durante borrador se admiten valores/fecha/instrumentos pendientes. Al enviar se exigen fecha, instrumentos identificados, resultados narrados, las 16 posiciones exactas con valor no nulo, parámetros que el protocolo declare obligatorios y evidencia o ausencia documentada. No se inventa un certificado de instrumento ni se deduce vigencia por adjuntarlo; su política metrológica queda pendiente.

Las unidades, mínimos, máximos, tolerancias y criterios proceden de la plantilla inmutable; el técnico registra valores, no puede cambiarlos mediante campos añadidos a una petición. Parámetros no incluidos y puntos arbitrarios se rechazan en servidor.

## Flujo y dependencia comercial

Estados implementados: NO_INICIADA → EN_PROCESO → PENDIENTE_REVISION → APROBADA / OBSERVADA / RECHAZADA. Una decisión no borra el estado histórico ni convierte observación/rechazo en aprobación. Una corrección requiere nueva revisión, nuevo envío y decisión independiente.

Inicio y cada operación dependiente comprueban:

1. Bandera local, usuario/perfil/empresa activos, rol y empresa del expediente/equipo.
2. Asignación técnica activa cuando corresponde.
3. Autorización comercial válida según el servicio existente de Fase 4 y diagnóstico vigente exacto.
4. Reparación de esa autorización finalizada y revisada independientemente; se congela el conjunto de actividades y versiones de resultado.
5. Plantilla aprobada exacta, validación independiente, aplicabilidad al equipo y copia íntegra de su fuente.
6. Revisión actual; sin edición después de envío y con versión optimista esperada.

Cambiar diagnóstico, autorización, actividades/resultados de reparación, calibración vigente o retirar el ancla técnica bloquea el avance dependiente. Las decisiones anteriores sobreviven como historial, no se recalifican ni se usan automáticamente para otra fuente.

La interfaz distingue ese historial del avance actual: si las dependencias no están vigentes o no se pueden verificar, muestra el bloqueo y oculta edición, revisión y emisión dependientes. Una pantalla histórica con estado APROBADA no convierte una autorización retirada en vigente.

Envíos, decisiones y ediciones se serializan bajo transaction.atomic/select_for_update del expediente. UUID/huella en auditoría recuperan el resultado de reintentos idénticos; una clave con contenido diferente se rechaza. No se cambian números de expediente ni QR.

## Revisión y permisos

| Rol | Facultades nuevas y límites |
| --- | --- |
| TECNICO | Autor principal asignado inicia, guarda borradores, mide, adjunta evidencias y envía; solicita informe de revisión enviada. No aprueba ni define referencias de una medición |
| PLANTA | Define/versiona plantillas, documenta aplicabilidad y valida una versión solo con aprobación expresa independiente; autoriza correcciones y decide sobre revisión enviada. No revisa trabajo propio ni derivado tras cambio de rol o reasignación |
| OFICINA | Lectura autorizada y solicitud de PDF desde revisión enviada. No mide ni aprueba |
| GERENCIA / ADMIN / CONSULTA | Lectura interna autorizada; sin escritura técnica o equivalencias de aprobación |
| CLIENTE | Permisos históricos; ningún acceso nuevo a calibración interna o aprobación digital |

El veto de autoaprobación recorre autores de calibración y sus revisiones predecesoras; no se elude cambiando de rol o asignando a otro técnico la corrección. La validación de plantilla también exige revisor diferente de su autor.

Evaluaciones manuales por punto: PENDIENTE, CONFORME o NO_CONFORME. Sin criterio aprobado solo puede registrarse PENDIENTE; disponer de valores/límites no calcula conformidad. Aprobar exige las 16 evaluaciones manuales favorables y dependencias vigentes; no se autorizaron excepciones implícitas para puntos pendientes/no conformes. PLANTA puede observar o rechazar con motivo sin inventar evaluación favorable. La interpretación industrial y autenticidad de las fuentes corresponden a validación humana; no se han codificado fórmulas no aprobadas.

## PDF y protección documental

Se reutiliza ReportLab, ya disponible; sin dependencias nuevas. `calibracion_pdf.py`, plantilla `calibracion-v1`, genera A4: identidad MASISCAM/expediente/equipo/tipo, técnico y fecha, protocolo/versión/huella, instrumentos, parámetros aplicables, tabla de 16 valores/unidades/referencias/tolerancias/evaluaciones, criterios y observaciones, resultados, evidencias y responsable/fecha/motivo de decisión si existe.

Se permite informe de revisión enviada pendiente, claramente identificado sin aprobación técnica. No se emite un borrador como calibración realizada. Tras una decisión, otra emisión requiere versión nueva y motivo; el informe pendiente anterior permanece. No es certificado metrológico, firma certificada, informe final ni cierre global de Fase 6.

Reserva bajo bloqueo con snapshot/huella; render fuera de transacción; nuevo bloqueo y comprobación de fuente/estado antes de asociar Documento. Cambiar revisión/decisión o dependencias durante render impide publicar esa reserva como vigente. La misma emisión generada se recupera, no se regenera con otros bytes.

Se reutilizan DocumentoForm/FileField y verificación de copia persistente por lectura de vuelta, tamaño y SHA-256. Caída del storage o falso éxito no produce emisión GENERADA. Archivo corrupto/ausente bloquea uso/descarga y requiere restaurar bytes originales; no se cambia el hash para aceptar corrupción.

Drive se encola después del commit mediante `encolar_drive` y `sincronizar_documento`; sin nuevas carpetas ni reorganización. Si broker/Drive fallan, reserva/copia persistente permanecen. La tarea guarda error seguro sin exponer rutas/credenciales. `reintentar_informes_calibracion` lista por defecto y solo encola con opción explícita y bandera local.

Guardas ORM y SQL protegen identidad, enviados, hijos, decisiones, plantillas, emisiones y archivos privados. SQL rechaza saltos de estado sin envío/decisión y envíos sin 16 valores, cambios masivos/borrados, auto-revisión, relaciones de otra empresa y alteraciones de contenido documental. Documentos nuevos quedan excluidos de listados heredados de proyecto/Drive; descargar exige permiso/ámbito/integridad, incluso por rutas heredadas. No se expone /media/ públicamente.

## Archivos

| Área | Archivos |
| --- | --- |
| Nuevos modelos/servicios/guardas | `sistema/masiscam/calibracion_models.py`, `calibracion_services.py`, `calibracion_guards.py` |
| Formularios/vistas | `calibracion_forms.py`, `calibracion_views.py` |
| Plantillas responsivas | `calibracion_inicio.html`, `calibracion_plantillas.html`, `calibracion_plantilla.html`, `calibracion_expediente.html`, `calibracion_revision.html` |
| PDF/tarea/recuperación | `calibracion_pdf.py`, `calibracion_tasks.py`, `management/commands/reintentar_informes_calibracion.py` |
| Integración existente | `models.py`, `apps.py`, `recepcion_guards.py`, `tecnica_services.py`, `drive_documents.py`, `views.py`, `urls.py`, `templatetags/masiscam_navigation.py`, `templates/masiscam/base.html` |
| Configuración | `sistema/config/settings.py`, `recepcion_local_settings.py` |
| Migraciones | `0025_calibracion_fase5.py`, `0026_guardas_calibracion.py` |
| QA | `test_calibracion.py`, `sistema/tools/verificar_fase5_local.py`, `verificar_migracion_fase5.py` |
| Entrega | Este documento y `output/pdf/MASISCAM_Calibracion_DEMOSTRATIVO.pdf` |

El árbol ya contenía cambios no confirmados de fases anteriores. No se presentan como nuevos cambios de Fase 5 ni se revierten. No se modifican migraciones hasta 0024, requirements, autorizaciones de Fase 4, datos reales o Drive real.

## Migraciones y conservación

0025 añade ocho tablas con FK PROTECT, unicidades de versión/punto/decisión/emisión y comprobación de Documento generado. 0026 añade triggers PostgreSQL/SQLite de protección y coherencia; no hay backfill, plantilla, medición o aprobación creada durante migrate.

Ensayos en SQLite temporal y PostgreSQL 16 desechable en tmpfs/127.0.0.1: desde 0024 poblada con históricos sintéticos de Fases 2/3/4, incluyendo QR, IDs Drive ficticios, recepción, guía, diagnóstico, plano, piezas, autorización no operativa ficticia, actividad, uso, resultado, revisión e informe de reparación. Respaldo previo nativo SQLite/pg_dump; comparación de todas las columnas de 37 tablas y hashes de archivos tras 0026: intactos en ambos motores, nuevas tablas vacías. No se usan datos ni respaldos de producción.

Artefactos: `sistema/artifacts/fase5-qa/`, excluidos del versionado. Los ensayos no ejecutan Compose ni acceden a los contenedores/bases reales de MASISCAM.

`resumen-final.json` consolida resultados después de verificar los logs, los hashes de la muestra, las 15 capturas sin desbordamiento, ambos ensayos de conservación y la retirada del contenedor QA. Las ejecuciones dirigidas se mantienen separadas para no contar repeticiones como casos únicos.

## Pruebas y evidencias reales

| Verificación | Resultado |
| --- | --- |
| Primera suite calibración SQLite | 26 casos: 23 aprobados, tres omisiones de concurrencia; cero fallos/errores |
| Primera suite calibración PostgreSQL 16 | 26 aprobadas, cero fallos/errores/omisiones |
| Regresión completa SQLite | 368 casos: 352 aprobados y 16 omisiones PostgreSQL, cero fallos/errores; `sqlite-suite.txt` |
| Regresión completa PostgreSQL 16 | 369 aprobadas, cero fallos/errores/omisiones; `postgres-suite-final.txt` |
| Comprobación final guardas SQL/PDF | Tres aprobadas por motor sobre guardas finales, persistencia y versionado pendiente/aprobado; `sqlite-validacion-final.txt`, `postgres-validacion-final.txt` |
| Comprobación final interfaz/fuentes retiradas | Dos aprobadas por motor; `sqlite-interfaz-vigencia-final.txt`, `postgres-interfaz-vigencia-final.txt` |
| Lectura de vuelta / precisión | Tres aprobadas SQLite y cinco PostgreSQL, incluyendo dos de concurrencia; `sqlite-precision-final.txt`, `postgres-precision-final.txt` |
| Extremo Decimal(30,12) | Una prueba ampliada aprobada por motor con dos valores extremos; lectura exacta PostgreSQL y rechazo/rollback SQLite, sin redondeo contextual ni error 500; `sqlite-limite-decimal-final.txt`, `postgres-limite-decimal-final.txt` |
| Migración 0024 → 0026 | Pasó en ambos motores; 37 tablas y todos sus archivos conservados |
| HTTP/worker/descarga | Recorrido funcional con Test Client y worker local; bloqueo por defecto y escenario válido únicamente con política/protocolo ficticios temporales |
| Interfaz | 15 capturas, cinco estados en 1400/768/390 px; sin desbordamiento horizontal; Edge headless con red bloqueada |
| PDF | Muestra ficticia A4 de tres páginas; extracción pypdf, ausencia de formularios/firmas, render y revisión visual de todas las páginas; valores y referencias separados, tablas paginadas e imagen sintética |
| Check/coherencia | Django check sin incidencias; makemigrations --check --dry-run sin cambios; diff sin errores de espacios (avisos LF/CRLF anteriores) |

Cobertura: decimales/cero/negativos/no redondeo, 16 puntos exactos y valores pendientes, parámetros desconocidos/obligatorios, plantillas/aplicabilidad/fuentes, aprobación retirada, autoría/cambio de rol/reasignación, observación/rechazo/correcciones versionadas, edición optimista, idempotencia, concurrencia independiente, bloqueo comercial/reparación, datos manipulados, historia ORM/SQL, PDF pendiente/aprobado/versionado, corrupción, storage caído/falso éxito, worker/Drive simulados y QR/históricos.

Los avisos Drive de tests heredados corresponden a fallos simulados; no son llamadas a Drive real. Omisiones SQLite no se cuentan como aprobaciones ni certifican locks PostgreSQL. Los escenarios FICTICIOS QA no autorizan uso industrial.

La suite SQLite comenzó antes de añadir el caso específico de salto de estado por SQL; ese caso está cubierto por la verificación final dirigida con migraciones frescas. Las cinco ejecuciones dirigidas por motor se informan separadamente porque incluyen repeticiones de casos de regresión, no cinco pruebas únicas nuevas.

La suite PostgreSQL incluye los 335 casos anteriores de Fases 2/3/4 y 34 casos nuevos de Fase 5. La guarda de lectura de vuelta y su caso específico de precisión se añadieron después: se probaron de forma dirigida con ceros/negativos, idempotencia y concurrencia. No se atribuyen esas ejecuciones a la suite completa ni se inflan los totales sumando duplicados.

El ensayo final de migración se repitió con las guardas reforzadas. Solo en la base raíz QA sintética se retiraron las ocho tablas nuevas después de comprobar que estaban vacías, se respaldaron nuevamente los históricos ficticios de 0024 y se reaplicaron 0025/0026; las 37 tablas y archivos anteriores volvieron a compararse intactos. No se hizo rollback ni migración sobre datos reales. Un primer ejecutor QA falló al resolver la ruta Python antes de conectar a la base; se corrigió el ejecutor, sin cambio funcional.

La muestra entregada es independiente de los documentos persistidos en QA: usa identidad ficticia, protocolo/unidades/límites rotulados sin validez industrial y estado PENDIENTE_REVISION, sin aprobación técnica. `aceptacion-visual.json` registra el hash de la muestra y los hashes de las dos emisiones sintéticas persistidas (pendiente y revisión QA favorable); no se confunden con documentos o aprobaciones reales.

SHA-256 de la muestra final de tres páginas: `138b38d0aca1d4a37be92abd74237d44078c8a1caea7b28992dba514ba7a56d6`. Se revisaron visualmente todas sus páginas, tabla repetida/paginación, referencias, ausencia de aprobación técnica y fotografía sintética rotulada. No hubo fallos en las suites funcionales; se corrigieron preventivamente presentación/paginación, estado de dependencias y precisión SQLite, con verificaciones dirigidas posteriores. No se presentan las mejoras posteriores como si hubieran estado incluidas en la primera suite.

## Pendientes y riesgos

1. **Bloqueante comercial heredado:** procedimiento comercial aprobado y validado según Fase 4. No se modifica ni salta.
2. **Bloqueante técnico:** plantilla/protocolo real por aplicación, significado y orden de las 16 posiciones, unidades, fuentes, referencias, tolerancias y criterios; aprobación expresa y validación PLANTA independiente, aplicabilidad a cada equipo. No hay anclas reales configuradas.
3. Validar protocolos diferenciados de bombas/reductores y aplicabilidad de shims/backlash/presión; no asumir un ensayo universal.
4. Resolver instrumentos, certificados, vigencia, incertidumbre/resolución/escala por magnitud, requisitos de evidencia y criterios de aceptación. El registro de un nombre/archivo no prueba calibración metrológica del instrumento.
5. Decimal(30,12) es capacidad técnica. SQLite puede limitar cifras significativas por su afinidad numérica; el servicio verifica lectura exacta de vuelta y revierte/rechaza valores que el motor altere o no pueda representar. No se pierde precisión silenciosamente ni se promete admitir todos los valores PostgreSQL en SQLite. PostgreSQL sigue siendo el motor de referencia para precisión/concurrencia industrial.
6. La revisión es humana y documentada; el software no deduce conformidad de límites sin fórmula/procedimiento aprobado. No se autorizaron excepciones para aprobar puntos pendientes o no conformes.
7. DB/storage/cola no son transacción distribuida. Fallo/rollback puede dejar archivos huérfanos; hay ventana commit/encolado recuperable mediante reserva/reintento. No se elimina contenido automáticamente sin política de retención.
8. Respaldos/restauración, permisos de infraestructura, rendimiento, broker y Drive reales requieren revisión separada. SHA-256 no sustituye respaldo; un administrador de infraestructura puede intervenir fuera de los servicios/triggers.
9. Capturas y HTTP no sustituyen ensayo con tablet física, operadores y protocolo real. La política de revisión y publicación externa del PDF sigue pendiente; documentos nuevos permanecen privados.

## Estado

**Técnicamente completos y verificados para el alcance local:** estructura configurable de protocolo/aplicabilidad, borradores, 16 mediciones decimales, parámetros aplicables, evidencias/instrumentos, envío, revisión independiente, observación/rechazo/correcciones versionadas, guardas/permisos/auditoría, PDF privado persistente/versionado y recuperación/sincronización simulada. Migraciones aditivas ensayadas, regresión de fases anteriores y concurrencia PostgreSQL aprobadas, más verificaciones dirigidas finales de interfaz y precisión.

**Pendientes de aprobación operativa:** procedimiento comercial de Fase 4; protocolo real con las 16 posiciones, aplicación por equipo, unidades, tolerancias/criterios y requisitos metrológicos/instrumentales. Inicio, avance, aprobación y emisión operativos dependientes permanecen bloqueados. Plantillas configurables pueden prepararse sin aprobarlas, y los componentes se prueban con expedientes sintéticos aislados. No se declara Fase 5 industrialmente aprobada ni lista para activar producción.

Lista para revisión técnica previa a cualquier decisión de despliegue. Siguiente paso mínimo: aportar y aprobar el protocolo real y las políticas comerciales/metrológicas; contrastar sus reglas con estos controles, registrar aprobación de versión exacta y ejecutar aceptación local con esas fuentes, sin inventar posiciones o valores.

Se destruyeron las bases de prueba y se retiró exclusivamente el contenedor PostgreSQL QA tras comprobar nombre, imagen, etiqueta, ausencia de volúmenes/Compose y bases de prueba activas. `postgres-cleanup.json` registra la retirada. Solo se inspeccionaron metadatos de salud/montajes de los servicios existentes: no montan código del workspace y permanecieron saludables; no se accedió a sus bases, secretos o archivos.

No se implementa informe final o cierre global de Fase 6. Sin producción, Drive real, commits, push o despliegue.
