# Finalización de Fase 2: entrega técnica y bloqueos

Vigencia 2026-11-06: este informe conserva la entrega anterior. La aprobación expresa y los resultados de la activación operativa local están en [cierre operativo de Fase 2](cierre_operativo_fase2.md); su dictamen reemplaza el bloqueo descrito aquí para el alcance local autorizado.

Fecha: 2026-11-05. Estado: Fase 2 no completada operativamente; políticas pendientes de aprobación.

Revisión posterior: [preparación del cierre operativo](cierre_operativo_fase2.md), con cambios necesarios, criterios de activación y nueva aceptación del bloqueo. Los resultados de 244 pruebas de este informe se conservan como evidencia de la entrega anterior.

## 1. Políticas revisadas

No existe aprobación expresa para convertir en obligatorias las reglas propuestas en el último encargo. Sigue vigente «Mantener finalización bloqueada hasta definir la política». Se consolidaron en [políticas pendientes](politicas_recepcion_pendientes_aprobacion.md), sin activarlas.

| Regla | Tratamiento en esta entrega |
| --- | --- |
| Fotografía general obligatoria | Propuesta pendiente; no se impone cantidad mínima |
| Fotografía de placa si existe | Propuesta pendiente; no se presume clasificación de fotos genéricas |
| Excepción de equipo sin placa | Propuesta pendiente de motivo, responsable y aprobación |
| Recepción sin guía, con motivo | Propuesta pendiente; el registro provisional no acredita excepción autorizada |
| Datos técnicos incompletos | Se muestran vacíos como pendientes en snapshot/PDF sin inventarlos; no se relaja el alta maestra de equipos |
| Usuario, empresa, cliente, equipo, fecha y condición | Registro y validación existentes conservados; responsable autenticado en servidor |

No se añade ruta ni botón de finalización. Una solicitud a `/app/expedientes/<id>/finalizar/` no ejecuta ninguna transición. No se implementan etapas técnicas, aprobaciones comerciales, firmas certificadas ni cierre del mantenimiento.

## 2. Cambios sobre la implementación existente

| Archivo | Cambio en esta entrega |
| --- | --- |
| `sistema/masiscam/recepcion_services.py` | Lectura de vuelta de archivos, comprobación de tamaño/SHA-256 antes de guardar metadatos y verificar reintentos; snapshot con datos técnicos disponibles y campos pendientes; reserva idempotente de la guía actual |
| `sistema/masiscam/recepcion_pdf.py` | Nueva plantilla `recepcion-v2` A4 con logo, cliente/equipo, datos disponibles, condición, observaciones, guía, fotos, manifest y espacios vacíos de firma; conserva renderer `recepcion-v1` para reservas anteriores |
| `sistema/masiscam/recepcion_views.py` | Una nueva versión requiere intención explícita; reintentar/recargar no duplica la emisión actual |
| `sistema/masiscam/templates/masiscam/recepcion_detalle.html` | Separa recuperar guía actual de solicitar una versión nueva; mantiene aviso de finalización bloqueada |
| `sistema/masiscam/test_recepcion.py` | Siete casos adicionales de políticas no activadas, almacenamiento, integridad, plantilla y concurrencia de emisión/generación; la prueba de nueva versión declara esa intención explícitamente |
| `sistema/tools/generar_muestra_guia_ingreso.py` | Genera muestra y PNG de revisión usando solo datos ficticios/ilustraciones; no inicia Django ni accede a DB/Drive |
| `docs/politicas_recepcion_pendientes_aprobacion.md` | Propuesta de políticas para el propietario, con estado y decisiones pendientes |
| `docs/implementacion_controlada_fase2.md` | Referencia a esta actualización, separando verificaciones históricas de resultados actuales |
| Este informe y `output/pdf/MASISCAM_Guia_Ingreso_DEMOSTRATIVA.pdf` | Entrega revisable y muestra documental |

No se cambian modelos ni se generan migraciones nuevas. `makemigrations --check --dry-run` confirma coherencia con 0015/0016 ya preparadas. No se reconstruyen clientes, equipos, RegistroEquipo, documentos o integración Drive.

## 3. Garantías técnicas verificadas

- Se conservan permisos de servidor y ámbito de empresa; no se habilita escritura por ADMIN/superusuario sin OFICINA.
- Correlativo, UUID de solicitud y snapshot conservan la numeración e identidad existentes. Misma solicitud devuelve el mismo expediente; payload incompatible produce conflicto.
- Generar/recuperar guía devuelve la emisión actual aunque el navegador haya cambiado el nonce. Una versión nueva requiere acción explícita y no crea otra mientras la actual esté pendiente.
- Reservas simultáneas se serializan con bloqueo de expediente; generación concurrente vuelve a comprobar la emisión bajo bloqueo antes de guardar. Verificado en PostgreSQL 16 con conexiones independientes: una sola emisión, documento y auditoría de generación, sin duplicar numeración.
- Un error de storage, una respuesta falsa de guardado o un hash/tamaño incorrecto impide vincular Documento y marcar GENERADA. La tarea conserva la recepción y el número y marca ERROR en emisiones sin documento; un reintento reutiliza esa misma emisión.
- Si una copia ya emitida se corrompe, el reintento falla y no se encola su sincronización. Se conserva la identidad/hash original, sin sobrescritura ni regeneración silenciosa; la recuperación del archivo exige respaldo. La descarga existente responde 503 ante integridad incorrecta.
- La nueva plantilla tiene identificación versionada. Reservas `recepcion-v1` usan su renderer anterior; una plantilla desconocida se rechaza, no se reemplaza silenciosamente.
- Los documentos generados siguen siendo privados, persistentes, versionados y protegidos contra edición/borrado. Se conserva Auditoria para reserva y generación.
- La sincronización posterior usa Documento, Celery y los servicios Drive existentes; no es condición inventada de autorización comercial ni de recepción finalizada.

## 4. Muestra PDF

Ruta: `output/pdf/MASISCAM_Guia_Ingreso_DEMOSTRATIVA.pdf`.

- Tres páginas A4 con logo MASISCAM, número `EXP-DEMO-00000001`, versión, fecha y etiquetas demostrativas.
- Cliente, RUC y equipo expresamente ficticios. Las imágenes son ilustraciones sintéticas identificadas como no reales; no se utiliza ninguna fotografía de cliente.
- Los datos técnicos sin información aparecen pendientes, sin valores de potencia, ratio, RPM o tolerancias inventados.
- Incluye sección de remisión y espacios sin firmar para entrega/recepción. No hay certificados, firma digital, aceptación simulada ni aprobación técnica/comercial.
- Datos extraídos con pypdf, imágenes y dimensiones A4 comprobadas, tres páginas renderizadas con PyMuPDF y revisadas visualmente: sin cortes ni superposición.
- SHA-256 de la muestra: `8362b6926467869704c4398851242aaba42e58212a2ffd6e037d46f5aa31df3f`.

La muestra no está incorporada como recepción real ni representa aprobación del propietario. La revisión visual técnica tampoco equivale a aprobación de su presentación definitiva.

## 5. Pruebas actuales

| Verificación | Resultado |
| --- | --- |
| Casos específicos de recepción antes de añadir el nuevo caso PostgreSQL | 27 ejecutados: 25 aprobados, 2 omitidos por PostgreSQL |
| Suite completa actual SQLite | 244 ejecutados: 240 aprobados, cero fallos/errores, 4 omitidos por concurrencia PostgreSQL |
| Django check | Sin incidencias |
| Coherencia de modelos/migraciones | Sin cambios pendientes; no se generaron migraciones |
| Muestra PDF | A4, 3 páginas, texto/recursos/SHA-256 verificados y revisión visual completada |
| Suite completa actual PostgreSQL 16 | 244 ejecutados y aprobados en 130,918 s; cero fallos, errores u omisiones; incluye concurrencia real |

Logs actuales: `sistema/artifacts/recepcion-qa/finalizacion/sqlite-suite.txt` y `sistema/artifacts/recepcion-qa/finalizacion/postgres-suite.txt`. Evidencia de muestra: `sistema/artifacts/recepcion-qa/finalizacion/muestra-verificacion.json` y PNG de sus tres páginas.

La validación anterior de 237 pruebas pertenece a la implementación previa. Tras habilitar el propietario Docker, se ejecutaron las 244 pruebas actuales en un PostgreSQL 16 desechable, enlazado exclusivamente a loopback, con almacenamiento tmpfs y respaldo inicial de su base vacía. Las migraciones se aplicaron solamente a la base de pruebas creada por Django. La suite destruyó esa base y se retiró el contenedor después de comprobar su identidad y etiqueta; evidencia en `sistema/artifacts/recepcion-qa/finalizacion/postgres-cleanup.json`. No se arrancó el Compose ni se utilizaron bases reales; los cuatro contenedores existentes de MASISCAM permanecieron activos y saludables. Las pruebas de Drive usaron simulación, sin llamadas reales.

## 6. Riesgos y aprobación pendiente

1. El propietario debe aprobar las políticas y quién acepta las excepciones de placa/remisión. Hasta entonces, la finalización continúa bloqueada para todas las recepciones.
2. Confirmar guía definitiva, retención/restauración, correcciones/publicación y disponibilidad requerida de Drive. No se infieren plazos ni decisiones comerciales.
3. La verificación local PostgreSQL actual quedó superada. Esto no sustituye una aprobación de producción ni una prueba operativa de Drive con credenciales reales; cualquier verificación externa requiere alcance autorizado.
4. La comprobación de copia es puntual; un administrador de almacenamiento puede modificar/eliminar archivos después. Se requieren respaldos y controles operativos, no una promesa de invulnerabilidad.
5. Un rollback de DB puede dejar un archivo nuevo huérfano. No se purga automáticamente ni se sobrescriben históricos; la reconciliación y retención requieren política aprobada.
6. La recuperación de una copia emitida corrupta depende del respaldo. Cambiar su hash para aceptar bytes alterados no está permitido.

## 7. Estado final

Se entrega la mejora técnica autorizada, la muestra y las políticas propuestas. **Fase 2 no completada operativamente:** no existe aprobación expresa de las políticas y sigue bloqueada su finalización; la suite actual está superada en PostgreSQL y SQLite (con las omisiones de concurrencia propias de SQLite). Sin migraciones en producción, despliegue, commits, push, cambios de QR o reorganización de Drive.
