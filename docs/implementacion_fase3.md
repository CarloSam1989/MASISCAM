# Implementación local de Fase 3

Fecha: 2026-11-06. **Estado: Fase 3 técnicamente completa en local/QA y lista para revisión previa al despliegue.** No autorizada para producción.

## Autorización y alcance

El adjunto del propietario «MASISCAM — FASE 3: IMPLEMENTACIÓN COMPLETA» aprueba expresamente D01–D07 de [validación Fase 3](validacion_fase3.md) y autoriza implementación y pruebas exclusivamente locales/QA. Reemplaza el anterior bloqueo documentado en este archivo. Se aplican [diseño Fase 3](diseno_fase3.md) y las protecciones de [Fase 2](cierre_operativo_fase2.md).

No se implementan compras, inventario, autorizaciones comerciales, reparación o calibración; el informe declara expresamente que no autoriza reparaciones. No hubo acceso a bases reales, Drive real, despliegue, commits ni push.

## Modelos implementados y reutilización

Los doce modelos están en `sistema/masiscam/tecnica_models.py`, registrados mediante importación desde `models.py`:

| Modelo | Función real |
| --- | --- |
| AsignacionTecnica | Un técnico principal activo por expediente; asignación/reasignación PLANTA con motivo, autor, fechas, clave e historial |
| EtapaExpediente | Solo INSPECCION/DIAGNOSTICO, revisión vigente mediante FK, estado y control de avance |
| Inspeccion | Borrador narrativo, fecha, autor, revisión, procedencia, hallazgos y envío congelado |
| Diagnostico | Fuente exacta de inspección, fallas/causas/limitaciones/componentes/recomendaciones/conclusiones y declaración de piezas |
| EvidenciaTecnica | Documento y destino exclusivo inspección/diagnóstico; clasificación, leyenda, autor y cita opcional a versión exacta de biblioteca |
| DecisionTecnica | Una decisión por revisión enviada: aprobación/observación/rechazo, solicitud idempotente, responsable y motivo |
| ReferenciaTecnica | Plano/manual privado por empresa; revisión interna y versión del fabricante separadas; publicación/retiro PLANTA |
| ComponenteTecnico | Catálogo por empresa con código interno; no presupone unicidad global del número de parte |
| ParteReferenciaTecnica | Despiece por posición y versión; datos de fuente congelados al publicar |
| AplicabilidadReferenciaEquipo | Vínculo explícito referencia/equipo de la misma empresa, fundamento y vigencia auditados |
| PiezaRequerida | Origen manual/biblioteca, cantidad Decimal(20,6) positiva sin redondeo silencioso, unidad o pendiente justificado, condición/acción y snapshot |
| EmisionInformeTecnico | Informe INICIAL versionado; reserva, revisión exacta, snapshot, Documento, hash, estado/error y responsable |

Se reutilizan Empresa, Perfil, RolMasiscam, Cliente, Equipo, RegistroEquipo, ExpedienteMantenimiento, Documento y Auditoria. No se duplica el repositorio binario ni el QR. EmisionGuiaIngreso y EvidenciaRecepcion permanecen exclusivas de recepción: no se relajan las guardas de 0018 para almacenar trabajo técnico.

## Vistas, formularios y servicios

- Entrada `/app/tecnica/`, detalle `/app/expedientes/<id>/tecnico/`, revisiones de inspección/diagnóstico, evidencias, piezas, decisión y solicitud de informe.
- Biblioteca `/app/biblioteca-tecnica/`: filtros, planos/manuales privados, registro/nuevas versiones, edición de borrador, catálogo de componentes, partes, publicación/retiro, aplicabilidad, visor PDF/imágenes y descarga de originales. CAD se conserva descargable; sin OCR/conversión o interacción gráfica.
- Formularios adaptables a escritorio/tablet/móvil, con errores de servidor, borradores, estado técnico/documental separado y aviso de reparación bloqueada. Cambio de fuente se identifica como revisión pendiente; el estado histórico no se presenta como avance válido de la fuente nueva.
- `tecnica_services.py` autoriza cada operación, bloquea expediente o empresa según ámbito, comprueba revisión esperada en edición técnica y registra auditoría/idempotencia. No amplía la autorización de recepción para otorgar escritura a TECNICO.
- Un técnico asignado edita solamente su revisión borrador. PLANTA autoriza una revisión nueva con motivo, preservando autoría previa, evidencias y piezas fuente. No se sobrescriben revisiones enviadas.
- PLANTA independiente decide el diagnóstico. El veto de autoaprobación examina autores de diagnóstico, inspección y cadenas de procedencia, incluso tras cambio de rol o reasignación.
- Envío exige los mínimos aprobados o declaraciones explícitas, evidencia o ausencia documentada, y lista de piezas coherente con REQUERIDOS/SIN_REPUESTOS. Información desconocida no se convierte en mediciones, causas o conformidad.
- Repuestos se seleccionan por referencia publicada/aplicable y posición, o manualmente con contexto; cantidad > 0, máximo 14 enteros/seis decimales, unidad real o justificación de pendiente. No genera órdenes, compras o stock.
- Planos/manuales pueden citarse explícitamente como evidencia REFERENCIA de una revisión, incluso sin piezas, conservando versión/Documento. Los PDF de referencia se citan; no se envían al renderizador de fotografías ni se anexan automáticamente.
- Archivos existentes solo se admiten con autorización de uso/congelación documentada, privacidad, categoría, empresa y copia íntegra compatibles; nunca se cambia silenciosamente visibilidad o identidad Drive. Documento conserva su proyecto real; biblioteca habilita acceso acotado entre proyectos de la empresa sin abrir el proyecto fuente.

## Permisos y protección documental

OFICINA conserva recepción y solicita informes de contenido aprobado; GERENCIA supervisa con lectura. PLANTA asigna, administra biblioteca y revisa de forma independiente; TECNICO trabaja solo en expedientes asignados. ADMIN/CONSULTA mantienen lectura autorizada, sin escritura técnica/decisión; CLIENTE no recibe nuevos documentos internos. No hay bypass por superusuario ni conversión automática de roles.

Guardas ORM y SQL protegen enviados, decisiones, emisiones, contenido documental y procedencia. Nuevos vínculos de evidencia/piezas/partes no se insertan ni alteran después de congelar su padre. Traslados de empresa de proyectos con documentos técnicos quedan bloqueados. Protección cubre actualización masiva/SQL, borrado indirecto y documentos compartidos por referencia.

Descarga técnica y de biblioteca comprueban objeto/empresa/asignación e integridad. Visor/listados Drive excluyen los documentos nuevos protegidos; la descarga privada heredada reutiliza la protección central. El listado heredado de documentos de proyecto oculta metadatos técnicos nuevos: conocer el proyecto o ID no abre el documento ni revela un informe de un expediente no asignado. Se conserva comportamiento de documentos ordinarios y funcionalidades de Fase 2.

## Informe inicial, persistencia y recuperación

`tecnica_pdf.py` implementa plantilla `inicial-v1` A4 ReportLab: logo, expediente/versión, cliente/equipo y recepción congelados, inspección, diagnóstico, limitaciones, fotos/leyendas, piezas, referencias y responsables/fechas. Texto escapado, tablas con paginación y encabezado/pie repetidos. No se agregan firmas certificadas ni aprobaciones comerciales.

Solo se reserva desde diagnóstico vigente aprobado por decisión independiente. La reserva congela snapshot/manifest; el worker verifica archivos, genera PDF y vuelve a comprobar vigencia antes de registrar Documento privado, tamaño y SHA-256 mediante lectura de vuelta. Una revisión sustituida durante render no publica ni finaliza la nueva revisión. Ningún falso éxito del storage produce emisión válida sin archivo.

Reintento recupera la misma emisión/Documento; otra versión requiere intención explícita y motivo. Archivos emitidos y sus hashes son inmutables. Si se pierde/corrompe una copia, se bloquea descarga/uso y se requiere restaurar bytes originales, sin cambiar hash para aceptar contenido alterado.

`tecnica_tasks.generar_informe_inicial` conserva ERROR y reserva recuperable ante fallo; después de persistir, utiliza `encolar_drive`/`sincronizar_documento` existentes para informe, evidencias y planos de piezas. La biblioteca también encola su Documento después del commit al publicarse y permite reintento autorizado PLANTA. No reorganiza carpetas.

Comando `reintentar_informes_tecnicos`: lista por defecto, sin escrituras ni tareas; `--encolar` explícito y local permite recuperación de emisiones vigentes. Broker/Drive caídos no borran copia persistente ni convierten generación en aprobación. No hay publicación automática al cliente.

## Archivos modificados o creados en esta fase

| Área | Archivos |
| --- | --- |
| Registro de modelos y aplicación | `sistema/masiscam/models.py`, `apps.py`, nuevo `tecnica_models.py` |
| Negocio y permisos | Nuevos `tecnica_services.py`, `tecnica_guards.py` |
| Interfaz y rutas | Nuevos `tecnica_forms.py`, `tecnica_views.py`; cambios en `urls.py`, `templates/masiscam/base.html` y `templatetags/masiscam_navigation.py` |
| Plantillas nuevas | `_tecnica_campos.html`, `tecnica_inicio.html`, `tecnica_expediente.html`, `tecnica_revision.html`, `tecnica_form.html`, `tecnica_biblioteca.html`, `tecnica_referencia.html` bajo `sistema/masiscam/templates/masiscam/` |
| Protección de rutas existentes | `recepcion_guards.py` extiende detección de documentos protegidos; `drive_documents.py` excluye relaciones técnicas; `views.py` filtra metadatos técnicos en el listado heredado de proyecto |
| PDF/tareas/recuperación | Nuevos `tecnica_pdf.py`, `tecnica_tasks.py`, `management/commands/reintentar_informes_tecnicos.py` |
| Configuración local | `sistema/config/settings.py`, `recepcion_local_settings.py` |
| Pruebas y evidencia | Nuevo `sistema/masiscam/test_tecnica.py`, `sistema/tools/verificar_fase3_local.py`, `verificar_migracion_fase3.py` |
| Entrega | Este documento y `output/pdf/MASISCAM_Informe_Inicial_DEMOSTRATIVO.pdf` |

Sin dependencias nuevas. Se utilizan ReportLab y herramientas locales QA previamente instaladas/autorizadas. El árbol de trabajo contiene también los cambios anteriores de Fase 1/2; esta tabla identifica las intervenciones de Fase 3, no los presenta todos como nuevos.

## Migraciones preparadas y verificadas solo en QA

- `0019_trabajo_tecnico_fase3`: doce modelos, FK, índices/unicidades y checks de destinos/cantidad/asignación/emisión.
- `0020_guardas_tecnicas`: protección PostgreSQL/SQLite de filas enviadas/publicadas, autoría, decisiones independientes, documentos, empresa y auditoría.
- `0021_referencias_citadas`: vínculo explícito de evidencia a versión de plano/manual y clasificación REFERENCIA.
- `0022_guardas_citas`: coherencia clase/referencia/Documento, manteniendo las guardas de envío.

No se modificaron migraciones 0015–0018. Los ensayos parten de 0018 poblada con filas, guía PDF y foto sintéticas y respaldo previo. Se comparan todas las columnas/filas de dieciséis tablas de Fase 2 y hashes/bytes de archivos. Resultado PostgreSQL y SQLite: intactos, sin backfill ni inspecciones/diagnósticos/referencias creados automáticamente. No se aplicaron migraciones a la base activa ni producción.

## Pruebas ejecutadas y evidencias

Directorio `sistema/artifacts/fase3-qa/`.

| Verificación | Resultado |
| --- | --- |
| Primera pasada técnica | 21 casos: dos errores detectados y corregidos, dos omitidos por PostgreSQL; no se presenta como aceptación |
| Suite completa inicial PostgreSQL | 294 aprobadas, sin fallos/omisiones; anterior a ampliar citas versionadas |
| Suite completa final PostgreSQL 16 | 296 aprobadas, cero fallos/errores/omisiones; `postgres-suite-final.txt` |
| Suite completa final SQLite | 296 casos, 288 aprobados, ocho omisiones PostgreSQL, cero fallos/errores; `sqlite-suite-final.txt` |
| Verificación complementaria final PostgreSQL | 29 pruebas técnicas aprobadas, cero fallos/errores/omisiones, después de los ajustes finales; `postgres-tecnica-verificacion-final.txt` |
| Verificación complementaria final SQLite | Tres pruebas dirigidas aprobadas sobre listados heredados, callbacks/almacenamiento y presentación; `sqlite-verificaciones-finales.txt` |
| Migración PostgreSQL/SQLite | 0018 → 0022 conservó históricos y archivos; `migracion-postgres-resultados.json`, `migracion-sqlite-resultados.json` y respaldos QA |
| Django check / coherencia de migraciones | Sin incidencias / sin cambios pendientes |
| Espacios en diff | Sin errores; avisos LF/CRLF existentes |
| Aceptación funcional por HTTP | Técnico guarda diagnóstico y piezas, envía; PLANTA revisa; OFICINA reserva; worker local genera y descarga; sin Drive real |
| Interfaz | Doce capturas en Edge headless: borrador, revisión PLANTA, biblioteca y expediente con informe en 1400/768/390 px; sin desbordamiento horizontal |
| PDF | Muestra A4 de tres páginas, extracción de texto, fotografías, hash y revisión visual; no simula una intervención ni aprobación industrial real |

Las ocho omisiones SQLite corresponden a cinco casos anteriores y tres casos nuevos de concurrencia PostgreSQL. La suite incluye íntegramente las 267 pruebas anteriores de Fase 2; no se atribuyen sus resultados previos a una ejecución nueva.

Cobertura nueva: autoría/asignación/reasignación; permisos y empresa ajena; cambios de rol y fuentes copiadas; borradores/mínimos y ausencia de evidencia; inmutabilidad ORM/SQL/FK; biblioteca y documentos públicos; acceso entre proyectos; fuentes retiradas; cantidades/unidad pendiente y snapshots; decisiones/observación/rechazo/correcciones; citas manuales sin repuestos; reserva/versionado/idempotencia; edición simultánea, decisiones y generación concurrentes con conexiones independientes; render con fuente sustituida; corrupción, storage caído/falso éxito y sincronización simulada.

Problemas corregidos durante QA: snapshot manual vacío rechazado por validación; expectativa de excepción de borrado cubierta por PROTECT; formato de fecha para input date; petición con versión ausente/manipulada; visualización de fuentes sustituidas; ausencia de cita explícita de manual/plano; metadatos expuestos en listado de proyecto; ámbito de mocks de callbacks on_commit en una prueba complementaria. Los fallos iniciales no se ocultan ni se confunden con riesgos operativos pendientes.

La muestra es independiente del informe persistido de QA: ilustra la plantilla con datos ficticios, imagen sintética y parámetros técnicos desconocidos vacíos. No se sobrescriben muestras ni guías de recepción anteriores.

Evidencia funcional/visual: `aceptacion-visual.json`, doce capturas y las tres páginas renderizadas del PDF. SHA-256 de la muestra: `3a1beb70cb0517d61d98dcd32be9b41b0500ff0d089fb543de3adfe8ce0666d6`. El informe persistido de QA tiene su propio hash; no se confunde con la muestra demostrativa.

Se retiró exclusivamente el contenedor PostgreSQL QA después de comprobar nombre, imagen, etiqueta y ausencia de volúmenes persistentes. Django destruyó sus bases de prueba; `postgres-cleanup.json` registra la retirada del contenedor que alojaba también el ensayo sintético de migración. Los servicios existentes de MASISCAM permanecieron saludables. Ningún ensayo utilizó la base activa ni Drive real.

## Activación y operación local

`MASISCAM_TECNICA_LOCAL` queda deshabilitada por defecto en configuración habitual y solo se admite por variable con DEBUG. `config.recepcion_local_settings` habilita recepción y técnica sobre SQLite/media propios del sandbox, sin Drive/credenciales reales, con broker de memoria. No cambia el contenedor activo. No iniciar el Compose real para demostrar esta fase.

Para demostración interactiva, crear/respaldar el sandbox local y usar explícitamente `--settings=config.recepcion_local_settings` en migrate/runserver, con usuarios ficticios y roles existentes. El broker de memoria es para sandbox; generación puede verificarse mediante tarea local o un broker QA independiente. Los ensayos entregados usaron Django Test Client y worker simulado, no reiniciaron el despliegue activo. PostgreSQL desechable verifica locks; SQLite no los certifica.

## Riesgos y revisión previa al despliegue

- DB/storage/cola no son una transacción distribuida: rollback puede dejar archivos huérfanos y existe ventana commit/encolado. Reservas y reconciliación permiten recuperar; no se borran históricos ni se promete entrega remota exactamente una vez.
- La verificación puntual de hash no sustituye respaldos/restauración ni controles del administrador de infraestructura. Un archivo corrupto emitido necesita recuperación de bytes originales.
- Drive real, credenciales, cuotas y permisos remotos no se probaron ni cambiaron. La política de retención, restauración conjunta, publicación externa y operaciones productivas requiere revisión separada.
- La publicación de un documento privado existente exige uso/congelación compatibles; fuentes públicas, de otra empresa, corruptas o solo remotas no se admiten silenciosamente.
- No hay CAD interactivo, OCR o conversión; no se agregan tolerancias o fórmulas. El técnico y el revisor son responsables del contenido y de declarar sus limitaciones.
- Se conserva contención heredada en apertura de expedientes; las pruebas locales no garantizan rendimiento bajo carga productiva. Revisar volumen, almacenamiento y configuración de worker antes de desplegar.
- Revisión recibida/enviada no se edita retrospectivamente: corrección por nueva revisión autorizada. La reparación y autorización comercial permanecen bloqueadas/fuera de alcance.

## Dictamen técnico

Fase 3 técnicamente completa para D01–D07 en local/QA: implementación, migraciones aditivas, regresión, concurrencia, protección documental, aceptación funcional y revisión visual superadas. Lista para revisión previa al despliegue, con los riesgos operativos indicados. No se declara desplegada ni habilitada en producción, no se reinició la aplicación activa y no se iniciaron fases comerciales, reparación o calibración. Sin commits ni push.
