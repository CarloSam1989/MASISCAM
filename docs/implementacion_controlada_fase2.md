# Fase 2: implementación controlada y estado de verificación

Actualización 2026-11-06: ver [activación operativa local](cierre_operativo_fase2.md) para la aprobación de políticas y resultados actuales. Este documento conserva la implementación y verificaciones históricas anteriores.

Fecha: 2026-11-05. Estado: finalización operativa bloqueada por decisión del propietario. Este informe conserva la validación de la línea base de 237 pruebas en SQLite/PostgreSQL 16; la entrega posterior y sus resultados actuales están en [finalización de Fase 2](finalizacion_fase2_resultados.md). Sin autorización de producción.

## Decisiones y alcance realmente autorizados

El encargo «FASE 2: IMPLEMENTACIÓN CONTROLADA» autoriza expediente OneToOne con RegistroEquipo, recepción, correlativo transaccional por empresa, permisos en servidor, ReportLab, almacenamiento persistente, Drive/Celery, protección y pruebas. Reemplaza la anterior restricción de solo análisis; no autoriza migraciones sobre datos reales, despliegues, commits ni trabajo técnico posterior.

El propietario respondió expresamente:

- «Mantener finalización bloqueada hasta definir la política» para recepciones sin guía/fotografías.
- «Autorizar instalación aislada y herramientas de verificación» para ReportLab y pypdf/PyMuPDF locales. La revisión automática inicialmente rechazó la instalación por citar instrucciones anteriores; solo se reintentó después de esa autorización. Las herramientas QA no se añaden a producción.

Las siete fuentes anteriores se mantienen como antecedentes de diseño. Se implementa la alternativa correlativa, no EXP-UUID. Convención técnica visible: `EXP-E<empresa_id>-<correlativo mínimo 8 dígitos>`, sin reinicio por fecha. No equivale a aprobación de un formato comercial/legal. El PDF original no se ha obtenido y su contenido no disponible no se presume implementado.

No se habilitan inspección, diagnóstico, reparación, calibración, aprobación comercial/técnica, cierre, publicación al cliente ni corrección libre. No se crean modelos vacíos de esas etapas; se reutiliza Auditoria para los eventos actuales, sin duplicar una tabla de transiciones técnicas todavía inexistentes.

## Funcionalidad preparada

- `ExpedienteMantenimiento` OneToOne a un RegistroEquipo nuevo; FK protegidas a empresa, cliente contractual y creador. No convierte registros históricos.
- `ContadorExpediente` por empresa; transacción/bloqueos y UNIQUE(empresa, correlativo), número único e idempotencia con huella del payload. Los números confirmados no se reciclan; rollback antes de confirmar no emite número. Se conserva el bloqueo global previo que usa el código heredado para respetar orden de locks; esto limita paralelismo entre empresas y debe medirse en PostgreSQL.
- `RecepcionEquipo` inmutable: fecha, responsable autenticado OFICINA, condición, observaciones, número de guía y snapshot. Cliente/equipo se seleccionan de catálogos existentes dentro de empresa.
- `EvidenciaRecepcion` referencia Documento: fotos y copia PDF de guía, persistidas con hash y archivos nuevos. No se cambian fotos del equipo ni archivos anteriores.
- `EmisionGuiaIngreso`: versión, clave idempotente, snapshot de recepción/manifest, hash, documento persistente y estado de generación. La guía se etiqueta expresamente como constancia provisional, sin autorización comercial/técnica.
- Generación ReportLab 5.1.0 con texto escapado, datos, fotografías de recepción verificadas, manifest, saltos de página y pie numerado. Nuevas versiones conservan las anteriores; no editan la recepción original.
- Permisos de escritura exclusivos de OFICINA, sin bypass por superusuario/ADMIN. PLANTA/GERENCIA/ADMIN/CONSULTA consultan dentro de empresa; TECNICO y CLIENTE no acceden a estos documentos nuevos hasta definir asignación/publicación. Roles históricos conservados; no conversión automática. Se bloquea autoasignación de rol operativo desde edición de usuario MASISCAM.
- ORM guards más triggers de PostgreSQL y equivalentes SQLite para identidad, no borrado, documento inmutable, auditoría y transferencia de empresa. La sincronización puede actualizar metadatos Drive sin sustituir contenido o publicar/archivar los documentos protegidos.

La recepción se registra, pero NO tiene acción de finalizar. Guía/fotos pueden faltar en el registro provisional; se informa esa situación. No se crean excepciones comerciales ni cuotas mínimas de fotos.

## Compatibilidad e integración

Rutas nuevas bajo `/app/expedientes/`; navegación «Expedientes» para roles de consulta autorizados. No se cambia ningún token, ruta QR o comportamiento de autenticación del QR.

Los registros de recepción no pasan por renumeración/reorganización heredada y se consultan desde Expedientes; las listas de registros genéricos excluyen esa extensión para conservar nombres y numeración anteriores. Se bloquean edición, reintento de adjuntos y eliminación heredados sobre registros con expediente, antes de llamadas remotas destructivas.

Documento sigue almacenándose en categorías del Equipo en Drive. La relación con expediente es lógica; no se reorganizan carpetas. Las guías se generan localmente desde datos congelados y se sincronizan mediante las tareas existentes. Lecturas nuevas no crean carpetas. El visor Drive heredado excluye los documentos de recepción para evitar acceso indirecto; las descargas nuevas verifican empresa/rol/hash.

El worker conserva la emisión ante error y permite reintento de la misma versión. Comando `reintentar_guias_ingreso --empresa ID` es solo listado por defecto; `--encolar` solicita recuperación explícita sin nuevo expediente/número/versión. No se ha ejecutado contra datos reales. No se afirma que exista scheduler periódico configurado.

## Archivos modificados y nuevos

### Modelos, integración y guardas heredadas

- `sistema/masiscam/models.py`: cinco modelos nuevos y FK opcional de Auditoria; choices de roles nuevos.
- `access.py`, `usuario_forms.py`, `usuario_views.py`: lectura de roles nuevos, limpieza de asociación cliente y control de autoasignación.
- `apps.py`, `signals.py`: registro de guardas y supresión de creación de carpeta para alta de recepción.
- `services.py`, `drive_documents.py`, `registro_orden.py`, `views.py`: protección/aislamiento documental y exclusión de expedientes de reorganización heredada.
- `urls.py`, `templates/masiscam/base.html`, `templatetags/masiscam_navigation.py`: rutas y acceso a expedientes.
- `sistema/requirements.txt`, `requirements.lock`: ReportLab 5.1.0; lock con hashes regenerado sin actualizar versiones previas. Pillow/charset-normalizer existentes satisfacen sus dependencias base.

Las rutas de la lista anterior son relativas a `sistema/masiscam/` salvo donde se indica otro prefijo.

### Archivos nuevos

- `recepcion_forms.py`, `recepcion_services.py`, `recepcion_views.py`, `recepcion_tasks.py`, `recepcion_guards.py`, `recepcion_pdf.py`.
- `templates/masiscam/recepcion_form.html`, `recepcion_listado.html`, `recepcion_detalle.html`.
- `management/commands/reintentar_guias_ingreso.py`.
- `test_recepcion.py`.
- `sistema/config/recepcion_test_settings.py`: SQLite aislada por defecto; PostgreSQL solo con `MASISCAM_TEST_POSTGRES` y nombre `masiscam_qa_*`. Nunca toma DATABASE_URL para la base QA.
- `sistema/tools/verificar_recepcion_local.py`: ensayo sintético de respaldo/migración y muestra PDF renderizada.
- Este informe de implementación.

### Ajustes de pruebas heredadas durante la verificación PostgreSQL

Solo se modificó código de pruebas de `accounts/test_importacion.py` y `accounts/test_standalone.py`, sin modificar autenticación ni ningún otro código funcional de accounts. En MASISCAM se ajustaron `test_drive_documents.py`, `test_registro_archivos.py`, `test_registros.py` y `test_security.py`; se añadió `test_utils.py` para consumir respuestas mediante el wrapper del cliente Django, evitando un segundo close que cerraba las transacciones de TestCase en PostgreSQL.

Las comprobaciones de importación/standalone/seguridad ahora verifican el contrato de QR ya vigente: sesión requerida y retorno al equipo autorizado, conservando tokens y rutas. No se abrió acceso público ni se cambió el significado del código funcional. La prueba concurrente heredada de carpetas habilita su proveedor simulado y suprime el callback automático antes de la barrera; anteriormente deshabilitaba Drive aun cuando intentaba probar su sincronización. Ninguna aserción de aislamiento o integridad se eliminó.

PostgreSQL detectó un ajuste funcional necesario en `registro_orden.py`: la exclusión de la relación opcional Expediente añade LEFT JOIN, por lo que se usa `select_for_update(of=("self",))` para bloquear solo RegistroEquipo. No se bloquea la parte nullable ni se alteran registros históricos.

## Migraciones preparadas y ensayo con respaldo

1. `0015_recepcion_expedientes`: tablas, relaciones opcionales, roles y restricciones aditivas. No backfill ni llamadas remotas.
2. `0016_recepcion_guardas`: triggers de conservación/inmutabilidad; reversión de triggers disponible para administración de esquema, sin borrar datos por sí misma.

Se aplicaron al crear las bases desechables de tests. Además, el script QA creó una SQLite completamente sintética hasta 0014, insertó cliente/equipo/registro/documento de prueba, tomó backup con sqlite.backup y aplicó 0015/0016. Comparó todas las columnas de esas cuatro tablas: inventario idéntico antes/después y cero expedientes creados por migración. Backup y base de ensayo permanecen en un directorio temporal generado; ruta registrada en `sistema/artifacts/recepcion-qa/validacion-local.json`.

Tras habilitar Docker se creó otro PostgreSQL 16-alpine exclusivamente de QA, con datos en tmpfs, sin volúmenes del proyecto, sin unirse a la red de MASISCAM y puerto publicado solo en 127.0.0.1. Se respaldó la base vacía inicial (`postgres-backup-inicial.sql`) y Django creó/destruyó `test_masiscam_qa_recepcion` en cada ejecución. Las migraciones 0015/0016 y sus funciones/triggers se aplicaron satisfactoriamente en PostgreSQL. El nombre y etiqueta `com.masiscam.scope=recepcion-qa` permiten identificar únicamente el recurso desechable de este trabajo.

Al finalizar se retiró solo ese contenedor, después de verificar su ID, etiqueta, imagen, ausencia de proyecto Compose y ausencia de volúmenes persistentes. Evidencia en `sistema/artifacts/recepcion-qa/postgres-limpieza.json`. Los cuatro contenedores activos de MASISCAM (web, worker, DB y Redis) continuaron healthy; no se detuvieron, reconstruyeron ni migraron. Se conserva `resumen-verificacion.json` con los conteos finales y la comparación del baseline.

No se leyó ni migró la DB de producción; no se ejecutó el Compose del proyecto, cuya inicialización aplicaría migraciones. No se usaron credenciales ni contenido real de Drive.

## Pruebas y evidencias

- Pruebas específicas actuales: **21 aprobadas en PostgreSQL, sin omisiones**. Incluyen los dos casos de concurrencia real (misma empresa/doble envío y empresas distintas). Cubren permisos, aislamiento, CSRF, idempotencia/conflicto, rollback, contador independiente, recepción HTTP, fotos/MIME, PDF/hash/versiones/descargas, bloqueo ORM/SQL, cascadas, preservación QR/historia, renumeración heredada y recuperación del worker. En SQLite: 19 aprobadas y esos dos casos correctamente omitidos.
- Regresión focalizada con generación PDF instalada: 37 pruebas de recepción/Drive heredado, sin fallos; una omitida por PostgreSQL (antes de ampliar los casos específicos).
- **Suite completa PostgreSQL: 237 aprobadas, cero fallos, cero errores y cero omisiones**, 96,580 segundos. Log: `sistema/artifacts/recepcion-qa/postgres-suite-actualizada.txt`.
- **Suite completa SQLite: 237 ejecutadas, cero fallos/errores y 3 omisiones correspondientes a concurrencia PostgreSQL**, 47,184 segundos. Log: `sistema/artifacts/recepcion-qa/sqlite-suite-actualizada.txt`.
- Se conservaron las ejecuciones anteriores como evidencia. Antes de corregir el arnés, SQLite tenía 3 expectativas antiguas de consulta anónima; PostgreSQL produjo 1 fallo y 47 errores. La misma lista de 48 casos se reprodujo en HEAD anterior (216 pruebas) frente a la implementación (237 pruebas), sin casos nuevos de recepción en esa lista. Los errores provenían principalmente del cierre manual de respuestas dentro de transacciones de TestCase y de una prueba antigua de Drive con la integración deshabilitada. Se repararon las pruebas como se describe arriba; no se cambiaron las rutas ni el acceso real del QR para conseguir los resultados.
- `manage.py check`: sin incidencias. `makemigrations --check --dry-run`: sin cambios pendientes de modelo.
- PDF sintético de 3 páginas con textos largos, tildes y fotografía: contenido extraído con pypdf; páginas renderizadas con PyMuPDF y revisadas visualmente. Sin solapamientos/cortes de contenido ni pie. Muestra no aprobada por oficina todavía.
- Artefactos locales ignorados por Git: muestra PDF, PNG de las tres páginas, JSON de inventario y logs. No contienen datos reales ni se instalan como documentos del sistema.

## Ejecución reproducible en entorno QA

Usar Python 3.12 con requirements del proyecto y, para la verificación visual, pypdf/PyMuPDF en entorno QA. En esta máquina se usó el intérprete de `.python-runtime` con PYTHONPATH a los paquetes existentes de `.venv312` y los paquetes aislados `.venv-phase2-render`: no se reparó el venv antiguo ni se instaló nada en producción.

```powershell
python manage.py test masiscam.test_recepcion --settings=config.recepcion_test_settings --noinput
python manage.py test accounts masiscam --settings=config.recepcion_test_settings --noinput
python tools/verificar_recepcion_local.py
python manage.py makemigrations --check --dry-run --settings=config.recepcion_test_settings
```

Las pruebas heredadas de Drive usan dobles de API. Las credenciales de prueba están vacías; no existe conectividad real de Drive verificada. Para repetir PostgreSQL, proporcionar un clúster local desechable y una URL `MASISCAM_TEST_POSTGRES` con base `masiscam_qa_*`; jamás usar una base real. En esta validación se utilizó un contenedor independiente, no `masiscam-db-1`. No ejecutar estos comandos con settings productivos.

## Gates verificados, límites y riesgos pendientes

1. Gate técnico PostgreSQL resuelto: concurrencia, guardas, migraciones y suite completa verificados en servidor 16 aislado. No equivale a validación o autorización de migración del entorno productivo.
2. Regresión heredada resuelta en QA: arnés de streaming y expectativas alineados con el acceso autenticado existente. Se conserva la evidencia del baseline anterior y el resultado completo aprobado.
3. Finalización bloqueada por decisión expresa: faltan política de guía/fotos, recepción sin placa/datos heredados, retención/restauración, publicación, correcciones y disponibilidad exigida en caída de Drive. No hay cierre ni aprobaciones implementados.
4. Falta aprobación de la muestra visual de guía y de su presentación definitiva; no contiene firma digital ni autorización comercial.
5. Los triggers no defienden frente a un propietario DB que los deshabilite o un administrador que modifique archivos directamente. Auditar permisos de credencial DB, almacenamiento y Drive; respaldos reales no ensayados aquí.
6. Copia de archivos y transacción DB no son atómicas entre sí. Una caída al persistir puede dejar un archivo nuevo sin referencia. No se purga automáticamente para evitar pérdida; requerirá reconciliación/retención aprobada. No se modifica ni sobrescribe contenido anterior.
7. Para evitar reutilizar o alterar un documento heredado/público, cargar un hash ya activo en el mismo proyecto se rechaza explícitamente. La selección de un documento existente con control de acceso es mejora posterior; no se duplican bytes para eludir la restricción.
8. No existe tarea periódica instalada para reconciliar on_commit/broker: recuperación manual explícita disponible. Configurar operación periódica requiere decisión de despliegue separada.

## Dictamen

Actualización posterior: la guía A4 v2, la verificación de persistencia y la idempotencia reforzada se describen en [finalizacion_fase2_resultados.md](finalizacion_fase2_resultados.md). Las políticas nuevas siguen como [propuesta pendiente de aprobación](politicas_recepcion_pendientes_aprobacion.md). No interpretar los conteos históricos de este informe como pruebas de los cambios posteriores.

Implementación del alcance controlado verificada, sin commits, push o deploy: recepción, expediente, numeración, evidencias, guía provisional y protecciones superan las pruebas PostgreSQL/SQLite y regresión. La Fase 2 no se declara operativamente completa: finalización de recepción y presentación definitiva de la guía dependen de las políticas/aprobaciones pendientes indicadas. No se habilitan aprobaciones comerciales/técnicas ni etapas posteriores. No se solicita autorización nuevamente para lo ya permitido; únicamente deben resolverse las dependencias pendientes antes de habilitar las funciones afectadas o producción.
