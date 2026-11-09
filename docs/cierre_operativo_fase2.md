# Fase 2: activación operativa local de recepción

Fecha: 2026-11-06. Estado de entrega: Fase 2 técnicamente completa en el alcance de recepción local y lista para revisión previa al despliegue. Sin autorización de producción.

## Aprobación y alcance

El propietario aprobó expresamente en el encargo «FASE 2: ACTIVACIÓN OPERATIVA LOCAL»: fotografía GENERAL obligatoria; fotografía PLACA cuando exista; excepción SIN_PLACA justificada y registrada; guía de remisión opcional con motivo documentado de ausencia. Esa aprobación reemplaza el bloqueo operativo anterior para esta implementación local. Política congelada en cada confirmación: `recepcion-2026-11-06`.

Se conserva OFICINA como único rol de escritura de recepción, sin bypass ADMIN/superusuario; PLANTA, GERENCIA, ADMIN y CONSULTA conservan su lectura autorizada. La persona OFICINA registra la declaración de placa y la justificación al confirmar. No se crea una aprobación adicional de excepciones, ni aprobación técnica/comercial, firma certificada, publicación al cliente, cierre del mantenimiento o reapertura. Correcciones posteriores y Fase 3 siguen fuera de alcance.

## Comportamiento implementado

1. Abrir recepción continúa siendo un borrador explícito, reutilizando Cliente, Equipo, RegistroEquipo, ExpedienteMantenimiento, RecepcionEquipo y Documento. Datos técnicos incompletos permanecen vacíos/pendientes: no se fabrican valores ni se relaja el alta maestra de equipos.
2. El formulario de confirmación selecciona fotos de esta recepción o permite cargar nuevas; clasifica GENERAL y PLACA. Valida contenido de imagen, límites existentes de carga, pertenencia, persistencia, tamaño y SHA-256. No se toma una foto genérica ni una fotografía maestra mutable como prueba automática.
3. CON_PLACA requiere fotografía de placa. SIN_PLACA requiere justificación; no admite simultáneamente una foto de placa. La declaración corresponde al estado del equipo en esta recepción; no reescribe su ficha histórica. La ausencia de número de guía en la recepción exige motivo. Se conserva el criterio existente de guía registrada por su número, con archivo PDF opcional: no se añade una obligación nueva de escanearlo.
4. La confirmación bloquea el expediente en una transacción PostgreSQL, comprueba rol/empresa en servidor y valida las evidencias. Reserva una emisión nueva y genera ReportLab desde snapshot congelado con manifest, clasificación, motivos, política, responsable y fecha.
5. Antes de guardar ConfirmacionRecepcion, verifica el archivo persistente de Documento mediante lectura de vuelta, tamaño y SHA-256. No usa la respuesta del storage ni un archivo temporal del render como prueba de persistencia. Si falla generación/almacenamiento/verificación, se revierte la confirmación, emisión y auditoría de esa transacción; expediente y correlativo anteriores se conservan.
6. Reintentos de igual contenido devuelven la confirmación original, incluso si cambia el nonce del navegador; contenido diferente produce conflicto. La misma clave no puede abrir otra confirmación. Concurrencia se serializa por expediente: una confirmación, una emisión final y un evento de confirmación.
7. La sincronización usa las tareas/servicios Drive existentes después de commit. Fallo de cola/Drive no borra el PDF ni devuelve falsamente la recepción a borrador. La guía puede reintentarse desde el expediente y mediante la reconciliación existente; no se reorganizan carpetas.
8. Una recepción confirmada conserva su PDF, versión y hash; no permite emitir después una nueva guía provisional. Las emisiones provisionales anteriores conservan bytes, snapshot y renderer. La confirmación de recepción no finaliza el mantenimiento.
9. Interfaz con estados separados: provisional/sin finalizar, errores de validación, recepción confirmada por servidor y estado documental/Drive. Formulario adaptable a escritorio, tablet y móvil; las excepciones se muestran en detalle y PDF.

## Modelo y migraciones aditivas

- `FotoRecepcionClasificada`: OneToOne con EvidenciaRecepcion existente; clase GENERAL/PLACA. Mantiene el tipo FOTO original intacto. No duplica bytes ni crea otro catálogo de equipos.
- `ConfirmacionRecepcion`: OneToOne con RecepcionEquipo y EmisionGuiaIngreso; clave/huella idempotentes, placa, motivos, política, responsable y fecha. La recepción y su snapshot original permanecen inmutables.
- `0017_confirmacion_recepcion_local`: crea solamente esos dos modelos y sus restricciones. No contiene backfill ni clasifica o confirma automáticamente filas antiguas.
- `0018_confirmacion_guardas`: triggers PostgreSQL/SQLite de inmutabilidad y no borrado, coherencia de confirmación/documento/plantilla/hash y existencia de fotos clasificadas; bloquea agregar evidencias o emisiones después de confirmar. Conserva 0015/0016 sin reescribirlas.
- Guardas ORM complementarias y FK PROTECT; las comprobaciones de lectura física del archivo y permisos se realizan en servidor, no mediante un CHECK que pretenda acceder a storage.

Estas migraciones se prepararon y aplicaron exclusivamente en bases locales desechables de QA. No se aplicaron sobre la base activa de MASISCAM ni producción.

## Archivos de esta activación

| Área | Archivos |
| --- | --- |
| Configuración local explícita | `sistema/config/settings.py`, `sistema/config/recepcion_local_settings.py` |
| Modelos y guardas | `sistema/masiscam/models.py`, `recepcion_guards.py` |
| Confirmación y reutilización documental | `recepcion_confirmacion.py`, `recepcion_services.py` |
| Formulario, rutas y vistas | `recepcion_forms.py`, `recepcion_views.py`, `urls.py` |
| Interfaz | `templates/masiscam/recepcion_form.html`, `recepcion_detalle.html`, `recepcion_listado.html` |
| PDF | `recepcion_pdf.py`, plantilla recepcion-v3, con v1/v2 conservadas |
| Migraciones nuevas | `migrations/0017_confirmacion_recepcion_local.py`, `0018_confirmacion_guardas.py` |
| Pruebas | `test_recepcion_confirmacion.py`; nombres de dos pruebas anteriores en `test_recepcion.py` actualizados para distinguir bloqueo de entorno de aprobación operativa |
| Herramientas QA | `sistema/tools/verificar_confirmacion_local.py`, `verificar_migracion_confirmacion.py`, actualización de `verificar_recepcion_local.py` y opción `--confirmada` en `generar_muestra_guia_ingreso.py` |
| Documentación | Este documento y notas de vigencia en informes anteriores |

No se añadieron dependencias. ReportLab y herramientas QA usan el entorno aislado previamente autorizado.

## Activación local segura

La configuración habitual mantiene `MASISCAM_RECEPCION_LOCAL=False` por defecto; una variable de entorno solo se acepta con DEBUG. El módulo explícito `config.recepcion_local_settings` habilita el cierre y sustituye la base por `sistema/.local-recepcion/sandbox.sqlite3`, los archivos por `sistema/.local-recepcion/media`, broker en memoria y Drive deshabilitado/sin credenciales. No usa volúmenes ni base del Compose existente.

Para una demostración interactiva posterior, crear primero el directorio `sistema/.local-recepcion` y respaldar cualquier sandbox previo. Con el Python y dependencias locales, utilizar explícitamente:

```text
manage.py migrate --settings=config.recepcion_local_settings
manage.py runserver 127.0.0.1:8012 --settings=config.recepcion_local_settings
```

Crear únicamente usuarios/clientes/equipos ficticios en ese sandbox mediante las herramientas existentes. No importar datos reales ni copiar credenciales Drive. Estos comandos son instrucciones de uso local; esta entrega verificó el flujo con Django Test Client y bases QA, sin reiniciar ni desplegar sobre la aplicación activa. El correlativo concurrente se certificó en PostgreSQL, no en SQLite.

## Evidencias y pruebas actuales

Directorio: `sistema/artifacts/recepcion-qa/activacion-local/`.

- Suite SQLite: 267 pruebas, 262 aprobadas, 5 omisiones que requieren PostgreSQL; cero fallos/errores. Log `sqlite-suite.txt`.
- Suite PostgreSQL 16: 267 pruebas aprobadas, cero fallos/errores/omisiones. Log `postgres-suite.txt`.
- Casos de confirmación: completa, sin GENERAL, con placa sin foto, SIN_PLACA con/sin motivo, sin guía con/sin motivo, guía existente, MIME/contenido falso, referencia ajena, solicitudes manipuladas y CSRF, roles/superusuario, empresa ajena, corrupción, storage caído o falso éxito, rollback/reintento, versionado, inmutabilidad SQL y cola caída.
- Concurrencia real con conexiones independientes: misma y distinta clave de confirmación para un expediente; también concurrencia previa de apertura/correlativo y generación documental.
- Ensayo de migración SQLite desde 0014 hasta 0018: inventario histórico sintético intacto y respaldo previo; `sistema/artifacts/recepcion-qa/validacion-local.json`.
- Ensayo PostgreSQL desde 0016 con filas y archivo sintéticos, respaldo previo a 0017/0018: todas las columnas/filas históricas y el archivo intactos; cero clasificaciones/confirmaciones automáticas. Evidencias `migracion-postgres-antes.json`, `postgres-backup-0016-sintetico.sql` y `migracion-postgres-resultados.json`.
- Django check: sin incidencias. `makemigrations --check --dry-run`: sin cambios pendientes. `git diff --check`: sin errores; avisos existentes LF/CRLF.
- Django Test Client confirmó una recepción sintética y su auditoría, PDF/hash, QR e historial. `aceptacion-visual.json` conserva el resultado y seis capturas: provisional/confirmada en 1400, 768 y 390 px. Edge headless, sin acceso a red, con CSS local; sin desbordamiento horizontal. No equivale a una prueba física en tablet/teléfono.
- Muestra PDF A4 de tres páginas con datos e ilustraciones ficticios: `output/pdf/MASISCAM_Guia_Confirmacion_DEMOSTRATIVA.pdf`. Revisión visual de todas las páginas, texto, imágenes, paginación y campos de firma vacíos. SHA-256: `d58cfc011c4fce11c79786aefea7a8e924eebbb04abc4311a2af44b536af5473`. No registra ni simula una aprobación industrial real.

Los logs anteriores de 237/244/245 pruebas corresponden a entregas previas, no se sustituyen por resultados de esta activación.

Se destruyó la base de pruebas de Django y se retiró exclusivamente el contenedor QA después de verificar identidad, imagen, etiquetas y ausencia de volúmenes persistentes. `postgres-cleanup.json` conserva la evidencia. Los contenedores existentes de MASISCAM continuaron saludables. Las pruebas de Drive se simularon; no hubo llamadas a Drive real.

## Riesgos y límites antes del despliegue

- Un filesystem y PostgreSQL no comparten transacción: un rollback puede dejar archivos nuevos huérfanos. No se eliminan automáticamente ni se reutilizan para sobrescribir históricos; la reconciliación requiere procedimiento de retención.
- La lectura de vuelta acredita integridad en ese momento, no inmunidad frente a pérdida posterior. Respaldos/restauración conjunta de DB/media y permisos de almacenamiento deben revisarse antes de producción. Una copia corrupta no se acepta cambiando su hash.
- Existe una ventana entre commit y encolado; las emisiones persistidas y la reconciliación existente permiten recuperar el envío. No se promete entrega remota exactamente una vez; se conserva la idempotencia documental y los servicios actuales.
- Drive real, credenciales, cuotas, volúmenes y workers productivos no se probaron ni modificaron. Toda prueba externa necesita alcance autorizado.
- Se conserva el bloqueo amplio heredado durante apertura de expedientes. No se asegura rendimiento productivo a partir de los tiempos locales.
- No se implementa edición/reapertura de una recepción confirmada ni corrección retrospectiva del PDF. Retención, publicación al cliente y despliegue no quedan aprobados por este encargo.

## Dictamen técnico

Fase 2 técnicamente completa para recepción y confirmación local; lista para revisión previa al despliegue. Políticas expresamente aprobadas, pruebas PostgreSQL/SQLite, conservación histórica, interfaz y guía verificadas. La aplicación activa no se reinició ni recibió migraciones; el cierre está disponible únicamente al optar por el entorno local autorizado. No se ha habilitado producción ni iniciado Fase 3.
