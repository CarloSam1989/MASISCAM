# Implementación local de Fase 4

Fecha: 2026-11-06. **Implementación técnica local verificada y lista para revisión; cierre operativo pendiente de política comercial aprobada. No habilitada para producción.**

## Alcance autorizado y decisiones comerciales

El adjunto «MASISCAM — FASE 4: APROBACIÓN COMERCIAL, REPUESTOS Y REPARACIÓN» autoriza implementación local y pruebas. Se revisaron arquitectura, flujo, matriz de roles, validación e implementación de Fase 3, código real y migraciones hasta 0022. Los documentos iniciales son propuestas históricas; no constituyen aprobaciones comerciales.

No se encontró un procedimiento de aceptación comercial aprobado. Por instrucción expresa del adjunto se implementó la estructura y se mantuvo **bloqueada la aceptación efectiva y, en consecuencia, la ejecución de reparaciones**. Propuesta, presupuesto, informe inicial o entrega de un repuesto no sustituyen aceptación del cliente. No se inventaron precios, condiciones, firmas, tolerancias ni procedimientos industriales.

`MASISCAM_OPERACION_LOCAL` está deshabilitada por defecto y requiere DEBUG en la configuración habitual. El sandbox explícito `config.recepcion_local_settings` la habilita sobre su SQLite/media propios, con Drive deshabilitado y broker de memoria. No se reinició la aplicación activa ni se aplicaron migraciones a su base.

`MASISCAM_POLITICA_COMERCIAL_LOCAL = None` permanece en la configuración. No existe formulario, endpoint ni migración que la active. El contrato técnico reservado exige versión, referencia expresa de aprobación del propietario, procedimiento y fecha de vigencia con zona horaria. No basta un booleano ni pertenecer a GERENCIA. Antes de sustituir None se debe aprobar el procedimiento concreto y contrastarlo con las validaciones implementadas; este contrato no aprueba por sí mismo cualquier texto introducido en configuración.

Las pruebas de caminos válidos usan exclusivamente una política **FICTICIA QA**, mediante override temporal en bases desechables. Sus documentos y personas se identifican como ficticios. No es una aceptación real del cliente ni una autorización del propietario y no queda instalada en configuración o datos operativos.

## Arquitectura implementada

Se reutilizan Empresa/Perfil/RolMasiscam, Cliente, Equipo, RegistroEquipo, ExpedienteMantenimiento, AsignacionTecnica, Diagnostico, DecisionTecnica, PiezaRequerida, EmisionInformeTecnico, Documento y Auditoria. No se crea otro catálogo de clientes/equipos, contador, QR, repositorio binario, inventario o integración Drive.

Nueve modelos nuevos, registrados desde `models.py`:

| Modelo | Función |
| --- | --- |
| PropuestaComercial | Versión correlativa bajo bloqueo del expediente; alcance y observaciones; diagnóstico exacto, informe inicial emitido, snapshot y propuesta anterior |
| DecisionComercial | Decisión única e inmutable por propuesta; representante del cliente declarado, fecha efectiva, responsable de registro, motivo, respaldo y política congelada |
| RecepcionRepuesto | Procedencia, proveedor/entregador, número de parte, descripción, Decimal(20,6) positivo, unidad declarada, fecha, guía opcional y discrepancia documentada; pieza requerida opcional |
| ActividadReparacion | Actividad de reparación/montaje con autorización exacta, técnico autor, inicio, componentes, trabajo y observaciones |
| UsoRepuesto | Cantidad trazada a recepción y actividad; acumulación bajo bloqueo sin superar lo recibido; no crea movimientos de inventario |
| ResultadoReparacion | Finalización enviada por autor, trabajo realizado, resultado, fin y snapshot de usos/evidencias; versiones de corrección sin sobrescritura |
| RevisionReparacion | PLANTA independiente aprueba u observa una versión exacta con motivo y fecha de registro; una decisión por resultado |
| EvidenciaOperativa | Documento privado existente en el almacenamiento actual, clasificación y leyenda; destino exclusivo propuesta/decisión/recepción/actividad |
| EmisionOperativa | Comprobante de repuestos o reporte de reparación; reserva, versión, snapshot, huella, Documento, SHA-256, estado/error y autor |

Los registros operativos son append-only. Propuesta posterior enlaza la anterior; resultado observado permite otra versión. No se agregaron estados editables al Equipo ni se amplió ambiguamente EtapaExpediente, cuyo contrato de Fase 3 sigue limitado a inspección/diagnóstico.

## Estados y transiciones reales

El estado se calcula desde fuentes inmutables y revisión vigente, sin reescribir decisiones anteriores:

- Sin propuesta/decisión: PENDIENTE_AUTORIZACION.
- Propuesta vigente rechazada con respaldo: RECHAZADO. Reconsideración exige nueva propuesta; conserva el rechazo.
- Decisión favorable, política aprobada coincidente y diagnóstico vigente: AUTORIZADO.
- Actividad abierta bajo esa autorización: EN_REPARACION.
- Resultado observado, propuesta/diagnóstico sustituido o política retirada/cambiada: OBSERVADO; no se reutiliza la aprobación vieja.
- Todas las actividades de la autorización vigente con resultado revisado favorablemente por PLANTA: REPARACION_FINALIZADA. No equivale a calibración, aprobación técnica final ni cierre general del expediente.

Cada escritura bloquea el expediente mediante transaction.atomic/select_for_update y registra auditoría con clave idempotente/huella/resultado en la misma transacción. Una solicitud repetida con los mismos bytes y datos devuelve el resultado previo; reutilizar clave con otro contenido se rechaza. El servicio, no la interfaz, fija autor, empresa, relaciones y estado.

Nueva propuesta o diagnóstico invalida el uso operativo de autorizaciones anteriores. No elimina documentos ni decisiones. Antes de iniciar, usar piezas, agregar evidencia, enviar resultado o revisar se vuelve a comprobar autorización/política/vigencia. El inicio también exige cantidades requeridas recibidas con número de parte/unidad coincidentes; no inventa excepciones para faltantes o conversiones.

## Recepción y uso de repuestos

OFICINA/PLANTA pueden documentar una entrega física mientras la autorización está pendiente; es una operación independiente y **no habilita reparación**. Esto aplica la instrucción actual de continuar las funciones seguras, sin interpretar como aprobada la propuesta de flujo antigua que situaba las entregas después de aprobación comercial.

Una pieza vinculada debe pertenecer al diagnóstico vigente aprobado del mismo expediente. Recepción manual/no vinculada, diferencias de número/unidad, entregas parciales o excedentes exigen discrepancia en texto. Se conserva en snapshot la cantidad requerida y lo recibido previamente, sin conversiones ni redondeos silenciosos. Campos de procedencia, entregador, descripción, cantidad, unidad y fecha son obligatorios. La guía y fotografías son capturables; no se trasladan automáticamente las políticas de fotografías obligatorias de recepción de EQUIPOS a recepción de repuestos.

Se admiten fotografías iniciales y fotografías adicionales clasificadas con leyenda, sin duplicar la entrega ni alterar comprobantes emitidos. Una nueva emisión congela las evidencias que existían en ese momento. Se reutiliza una fotografía idéntica ya protegida de la recepción de este mismo expediente mediante comparación de bytes/hash y validación de integridad; no se cambia su metadata ni ID Drive.

Uso de repuesto registra recepción, cantidad y actividad; no es compra, ingreso/egreso de stock ni factura. Una recepción de otra empresa/expediente o vinculada a diagnóstico distinto no puede utilizarse en la actividad. La relación de usos queda congelada al enviar resultado; una observación abre corrección adicional sin modificar lo anterior.

## Permisos y aislamiento

| Rol | Escritura nueva permitida | Límites |
| --- | --- | --- |
| GERENCIA | Propuestas, reconsideración, decisiones y respaldos comerciales | Aceptación efectiva bloqueada sin política; respaldo no sustituye decisión |
| OFICINA | Recepciones/evidencias de repuestos, respaldos de propuesta pendiente y emisiones internas | No decide aprobación comercial ni registra trabajo técnico |
| PLANTA | Recepciones de repuestos, evidencia, revisión independiente de resultados y emisiones | No decide comercial ni revisa su propio trabajo, incluso tras cambio de rol |
| TECNICO | Actividades autorizadas propias, usos, fotos antes/durante/después y resultados | Asignación activa, autoría y autorización vigentes; no recibe poderes de GERENCIA/OFICINA |
| ADMIN / CONSULTA | Lectura interna autorizada según ámbito | Sin escritura comercial o técnica por equivalencia; no bypass de superusuario |
| CLIENTE | Permisos históricos intactos | Ningún permiso nuevo de aprobación, lectura de internos o firma digital |

La lectura técnica exige asignación activa. Narrativas/documentos comerciales están restringidos a GERENCIA/OFICINA; PLANTA/TÉCNICO reciben estado y documentos operativos, no presupuestos por conocer el expediente. Todas las rutas validan empresa/usuario/rol en servidor; campos FK en formularios también están acotados. Conocer un ID, proyecto, QR o ruta heredada no autoriza descargar.

Los listados heredados de proyecto y Drive excluyen documentos operativos protegidos. La descarga privada heredada y la autorización Drive usan la protección central existente. No se expone MEDIA_ROOT como ruta pública. Documentos emitidos y respaldos no se pueden archivar, publicar, sobrescribir o eliminar mediante ORM/actualización masiva/SQL; el estado de sincronización sigue pudiendo actualizarse por la tarea existente.

## PDF, almacenamiento y Celery

ReportLab ya estaba incorporado y se reutiliza; no se instalaron dependencias nuevas. `operacion_pdf.py` produce A4 con identidad MASISCAM, cliente/equipo congelados, registros, responsables, fechas, cantidades/unidades, resultados, manifiesto hash de fuentes, fotografías y paginación. No incluye firmas certificadas o una supuesta aceptación firmada del cliente. No es informe técnico final.

Reserva versionada bajo bloqueo; render fuera de transacción; nuevo bloqueo para comprobar fuente y asociar Documento. El comprobante de repuestos describe las recepciones congeladas al reservar. El reporte de reparación exige autorización actual y todas las actividades revisadas; si cambia el diagnóstico/autorización o se agrega otra actividad durante render, no se publica como válido.

Se reutiliza `tecnica_services.guardar_archivo`, DocumentoForm, FileField y la verificación de copia persistente por lectura de vuelta, tamaño y SHA-256. Archivo temporal, storage caído o falso éxito no registran una emisión GENERADA. Reintento recupera la reserva original. Otra versión exige intención explícita y motivo; nunca sustituye bytes emitidos. Corrupción bloquea uso/descarga y exige restaurar los bytes originales.

Una solicitud nueva no presenta como reporte actual una emisión de otra autorización o de un conjunto anterior de actividades. Debe completarse la fuente vigente y solicitar nueva versión con motivo. La descarga del documento histórico y el resultado de una solicitud idempotente anterior se conservan sin sobrescritura.

Se reutilizan `encolar_drive` y `sincronizar_documento` después del commit; sin carpetas, IDs ni documentos históricos reorganizados. Drive/cola caídos no eliminan la copia persistente. La reserva permanece recuperable si falla el broker. `reintentar_documentos_operativos` lista por defecto y solo encola con `--encolar` y la bandera local habilitada. No se conectó a Drive real.

## Archivos de esta fase

| Área | Archivos |
| --- | --- |
| Nuevos modelos/negocio | `sistema/masiscam/operacion_models.py`, `operacion_services.py`, `operacion_guards.py` |
| Interfaz | `operacion_forms.py`, `operacion_views.py`; plantillas `operacion_inicio.html`, `operacion_detalle.html`, `operacion_form.html` |
| Documentos y recuperación | `operacion_pdf.py`, `operacion_tasks.py`, `management/commands/reintentar_documentos_operativos.py` |
| Registro, permisos documentales y navegación existentes | `models.py`, `apps.py`, `recepcion_guards.py`, `tecnica_services.py`, `drive_documents.py`, `views.py`, `urls.py`, `templatetags/masiscam_navigation.py`, `templates/masiscam/base.html` |
| Activación local | `sistema/config/settings.py`, `recepcion_local_settings.py` |
| Migraciones | `0023_operacion_fase4.py`, `0024_guardas_operacion.py` |
| Pruebas/herramientas | `test_operacion.py`, `sistema/tools/verificar_fase4_local.py`, `verificar_migracion_fase4.py` |
| Entrega | Este documento y dos muestras PDF bajo `output/pdf/` |

El árbol de trabajo ya contenía cambios no confirmados de fases anteriores; no se presentan como cambios nuevos de esta fase. No se modificó ninguna migración 0015–0022, requirements ni dependencias.

## Migraciones y conservación

0023 añade solamente nueve tablas y sus FK PROTECT, unicidades, checks de cantidad/destino/emisión. 0024 añade triggers PostgreSQL/SQLite para inmutabilidad, pertenencia de relaciones, documentos privados, aprobación/revisión coherentes, cantidades, no auto-revisión y bloqueo de hijos tras envío. No hay backfill, modificación de históricos, aprobación ficticia o llamada a Drive durante migrate.

Ensayos sobre SQLite temporal y PostgreSQL 16 desechable en tmpfs/puerto 127.0.0.1: se pobló 0022 con datos de recepción, QR/IDs Drive ficticios, asignación, inspección enviada, diagnóstico aprobado, pieza, plano publicado, cita e informe inicial con archivos persistentes. Respaldo previo: backup nativo SQLite / pg_dump PostgreSQL. Comparación de todas las columnas de 28 tablas y hashes de todos los archivos tras 0024: **intactos en ambos motores**, nuevas tablas vacías, sin backfill.

Artefactos de QA en `sistema/artifacts/fase4-qa/`, excluidos del versionado. No son respaldos de producción.

`resumen-final.json` consolida resultados y verifica automáticamente los logs finales, hashes de las muestras, 21 capturas sin desbordamiento, ambos ensayos de migración y retirada del contenedor QA.

## Verificaciones y evidencias

| Verificación | Resultado |
| --- | --- |
| Suite inicial SQLite Fase 4 | 26 casos: se detectaron y corrigieron reutilización de fotos y errores de fixtures/expectativa de rechazo |
| Suite corregida SQLite Fase 4 | 24 aprobadas, dos omitidas por requerir PostgreSQL |
| Suite inicial PostgreSQL Fase 4 | 26 aprobadas, sin omisiones |
| Regresión ampliada SQLite inicial | 332 casos: fallo de fixture de reintento que regeneraba bytes PDF diferentes; corregido para reenviar exactamente el mismo respaldo |
| Regresión completa SQLite | 332 casos: 320 aprobados, 12 omisiones PostgreSQL, cero fallos/errores; `sqlite-suite-final.txt` |
| Regresión completa SQLite final ampliada | 334 casos: 322 aprobados, 12 omisiones PostgreSQL, cero fallos/errores; `sqlite-suite-334-final.txt` |
| Regresión completa PostgreSQL 16 inicial | 332 casos: un error de la fixture PDF anterior, cargada antes de su corrección; no se considera aceptación |
| Regresión completa PostgreSQL 16 final | 334 aprobadas, cero fallos/errores/omisiones; `postgres-suite-final.txt` |
| Verificación Fase 4 SQLite | 38 casos: 34 aprobados, cuatro omisiones de concurrencia; `sqlite-operacion-final.txt` |
| Comprobación final de reporte vigente/versiones | Dos pruebas adicionales aprobadas en cada motor; `sqlite-reporte-vigente.txt`, `postgres-reporte-vigente.txt`; posteriores al inicio de las suites de 334 |
| Migración 0022 → 0024 | Pasó en ambos motores, 28 tablas y archivos intactos; JSON y respaldos en QA |
| HTTP/worker/descarga | Recorrido real de vistas con Test Client, worker local y datos ficticios; autorización bloqueada por defecto, escenario válido solo con override QA |
| Interfaz | 21 capturas Edge headless, siete estados en 1400/768/390 px; sin desbordamiento horizontal; fuentes estáticas locales y red bloqueada |
| PDF | Dos muestras A4, dos/tres páginas; extracción pypdf, ausencia de formularios/firmas, hash, render de todas las páginas y revisión visual |
| Check/coherencia | Django check sin incidencias; makemigrations --check --dry-run sin cambios; diff sin errores de espacios, con avisos LF/CRLF anteriores |

Cobertura: bloqueo por política faltante, aceptaciones/rechazos respaldados, revisión fuente sustituida, permisos y empresa, técnico no asignado, rol cambiado, entrada manipulada, cantidades Decimal/discrepancias/parciales, usos acumulados, evidencias/clasificación, historia ORM/SQL, almacenamiento caído/falso éxito/corrupción, callback posterior al commit, reserva/versionado, cambio de fuente durante render y QR/históricos. La concurrencia usa conexiones independientes en PostgreSQL para recepción idempotente, propuesta, emisión y usos simultáneos; SQLite no certifica locks.

Las omisiones SQLite se informan como omisiones, no aprobaciones. Los avisos de errores Drive de la regresión heredada corresponden a escenarios de fallo simulados, sin acceso a Drive real. Los fallos iniciales y sus correcciones se conservan en los logs.

Las suites finales incluyen íntegramente las 296 pruebas anteriores de Fases 2/3 y 38 pruebas de Fase 4. Después se añadió el caso de reporte histórico ante otra actividad y se ejecutaron dos comprobaciones dirigidas por motor, una de ellas de regresión de versionado; ambas aprobadas. No se suman como si fueran dos casos únicos adicionales a la suite completa.

Las muestras son `output/pdf/MASISCAM_Repuestos_DEMONSTRATIVO.pdf` (dos páginas) y `output/pdf/MASISCAM_Reparacion_DEMONSTRATIVO.pdf` (tres páginas). Se identifican como demostrativas, con ilustración sintética y datos ficticios, sin uso industrial. SHA-256 finales: `d5424eed089585eefc5d056e3b6f93f4aa4a6a6402cbb283c4aa1928452a8849` y `10b5b41e07542550f25cc1d8fe18c90c3ab16eca2b143e5c4311ffd4de3e694a`, respectivamente. El JSON `aceptacion-visual.json` distingue estos hashes de los documentos persistidos de QA.

## Riesgos y pendientes de aprobación

1. **Bloqueante operativo:** aprobar procedimiento comercial concreto: canales/respaldo aceptables, representación del cliente, alcance autorizado, fecha efectiva y cómo documentar cambios/reconsideración. Después revisar si exige validaciones adicionales; no activar solo colocando un texto arbitrario en configuración. Hasta entonces no hay aceptación efectiva ni reparación operativa.
2. No existe política aprobada para excepciones de repuestos faltantes, sustituciones, equivalencias o conversiones de unidad. No se inventan. Discrepancia documentada no es aceptación comercial de una sustitución. Las recepciones manuales quedan trazadas y no habilitan por sí mismas reparación; revisar el tratamiento de uso de partes no previstas dentro del procedimiento comercial antes de activar operación.
3. Correcciones cuantitativas de entregas/usos, devoluciones y anulación comercial con reparación ya ejecutada requieren política explícita; no se borran registros ni se generan ajustes ficticios. Una modificación de alcance exige nueva propuesta/diagnóstico según corresponda. No hay flujo inventado de devolución/facturación.
4. La reasignación conserva autoría: un nuevo técnico no firma ni finaliza retroactivamente la actividad del anterior. Resolver actividades abiertas bajo reasignación mediante revisión operativa antes de activar; no se habilita suplantación.
5. DB y storage/cola no son una transacción distribuida. Rollback puede dejar archivos huérfanos; commit/encolado tiene ventana de fallo recuperable por reservas y reconciliación. No se promete entrega exactamente una vez a Drive. No borrar archivos huérfanos automáticamente sin política de retención.
6. SHA-256 detecta alteración pero no reemplaza respaldo/restauración ni permisos de infraestructura. Un administrador DB/storage puede intervenir fuera de la aplicación. Servicios de negocio son obligatorios para autorizar operaciones; los triggers no pueden verificar por sí solos la autenticidad jurídica de un documento o una aprobación externa.
7. Drive real, cuotas, credenciales, worker/broker de despliegue y capacidad bajo carga no se probaron. Revisar con autorización separada. Los servicios actuales no se reiniciaron y no se ejecutó Compose.
8. Las capturas validan disposición y errores HTTP, no sustituyen pruebas en tablet física, carga productiva ni revisión de usabilidad por operadores. No se probaron firma digital, CAD/OCR, compras, inventario, calibración o informe final porque están fuera del alcance.

## Estado y siguiente paso

La parte técnica del alcance local está implementada y verificada: migraciones aditivas ensayadas, regresión completa aprobada en PostgreSQL 16 y SQLite con omisiones explícitas, concurrencia real, documentos persistentes/versionados, aceptación HTTP, revisión visual y comprobaciones finales de vigencia.

**No se declara cerrada operativamente toda la Fase 4.** La aceptación comercial efectiva, ejecución/revisión de reparaciones y emisión de reportes operativos de reparación permanecen bloqueadas con la configuración real del sandbox, hasta aprobación del procedimiento y revisión de sus consecuencias. Propuestas, rechazos respaldados, documentación comercial, recepción/evidencias de repuestos y comprobantes internos se pueden revisar localmente sin esa aprobación. Los caminos de reparación válidos solo se habilitaron temporalmente dentro de QA ficticio.

Lista para revisión técnica previa al despliegue, **no para activación o despliegue**. Siguiente paso mínimo del propietario: aprobar el procedimiento comercial concreto y resolver el tratamiento de discrepancias/excepciones y alcance; contrastarlo con estos controles y ejecutar la aceptación local bajo la política aprobada. La aprobación de código/QA no constituye autorización comercial.

Se destruyeron las bases de prueba y se retiró exclusivamente el contenedor PostgreSQL QA, verificando identidad/etiqueta, ausencia de volúmenes persistentes/Compose y de bases de pruebas activas. `postgres-cleanup.json` registra la retirada. Los cuatro servicios existentes de MASISCAM permanecieron saludables. Sin producción, Drive real, commits, push, despliegues, calibración o informe técnico final.
