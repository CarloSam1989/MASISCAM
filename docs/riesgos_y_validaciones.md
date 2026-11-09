# Riesgos, decisiones pendientes y validaciones

Fecha: 2026-11-05. Fase 1 documental. Ninguna propuesta equivale a aprobación funcional o autorización de Fase 2.

## Evidencia y límites

Se revisaron modelos, formularios, vistas, rutas, plantillas, access/middleware, usuarios, servicios Drive, tareas/señales, orden y carga de archivos, migraciones y fuentes de pruebas. No se ejecutaron pruebas de aplicación, comandos Django, migraciones, acceso al despliegue ni llamadas Drive. La salida histórica `.test-registros-output.txt` no constituye evidencia de una ejecución actual.

El PDF original no está disponible en las rutas examinadas del proyecto/carpeta superior. El alcance proviene de los requerimientos transcritos por el usuario; portada exacta, tablas, firmas, obligatoriedad y anexos del original siguen pendientes. La documentación de septiembre `ESTADO_ACTUAL_MASISCAM.md` describe una entrega anterior: no certifica el nuevo flujo.

## Registro de riesgos

| Riesgo observado | Consecuencia | Control futuro y validación |
| --- | --- | --- |
| Secuencia renumerada en registro_orden.py y migración 0014 | Cambian referencias si se usa como expediente | Número inmutable independiente; editar fechas sin alterar número ni QR |
| registro_eliminar borra carpeta permanentemente | Pérdida de expediente/evidencia | PROTECT y comprobación previa a cualquier operación remota; pruebas en rutas legacy/admin |
| CASCADE en equipos/proyectos/archivos y auditoría | Borrado indirecto | Revisar cadena completa; proteger padre/hijos y auditoría; probar bulk/delete/SQL autorizado |
| ArchivoRegistro no conserva bytes | Celery no puede retransmitir contenido que nunca llegó a Drive | Persistencia duradera de emisiones mediante Documento; reselección explícita para carga heredada; reconciliación |
| Transacciones DB no abarcan Drive | Archivos sin registro o registros sin archivo | Reserva de IDs, idempotencia, inventario y reconciliación; fallos antes/después de cada frontera |
| Acceso por carpeta/listado recursivo | Documentos comerciales visibles a cliente | Clasificación y autorización por evidencia; pruebas por ID, token y rutas legacy |
| Algunas lecturas intentan crear carpetas faltantes | Inspección operativa con efectos laterales | Auditar rutas; futuras consultas de expediente sin crear/mover contenido |
| Permisos actuales editar/reemplazar amplios y superusuario con bypass | Aprobar o alterar fuera de función | Nuevas operaciones explícitas; sin aprobación implícita ADMIN/superuser; separación de autor/aprobador |
| Cambio de cliente/proyecto o ficha técnica | Documento histórico inconsistente o filtrado al nuevo dueño | Cliente contractual/empresa fijos, instantáneas y denegación de acceso histórico por defecto |
| Bloqueos amplios sobre primera Empresa | Contención entre empresas y workers | Medir en PostgreSQL; contrato de orden de locks; no sumar llamadas remotas bajo locks nuevos |
| Validación de serie por consulta, no UNIQUE equivalente | Duplicados concurrentes | Inventario y decisión de unicidad por empresa; prueba concurrente antes de restricción |
| Ausencia de motor PDF de producción | HTML no cumple informe versionado | Seleccionar motor mediante muestra de guía/informe y validación visual; no añadir dependencia en Fase 1 |
| Archivos CAD permitidos pero visor Drive solo sirve PDF/imágenes | Planos no utilizables directamente en informe | PDF/imagen derivado con fuente versionada; decidir conversión sin perder original |
| Formulario/tolerancias no validados | Falsa conformidad técnica | Bloquear envío/aprobación hasta protocolo aprobado; no sembrar valores de ejemplo |
| Estado real de migraciones/servicios desconocido | Plan incompatible con producción | Inventario de lectura y ensayo sobre copia aislada antes de migración |
| Entorno .venv312 con ruta antigua reportada en auditoría | Pruebas locales no reproducibles | Diagnosticar entorno antes de ejecutar Fase 2, sin instalar dependencias en esta fase |

La protección propuesta cubre acciones de aplicación y su credencial DB; no garantiza invulnerabilidad ante administrador de infraestructura o propietario externo de Drive. Definir acceso mínimo, respaldo verificable, retención y recuperación con el responsable del sistema.

## Decisiones pendientes

| ID | Decisión / evidencia requerida | Responsable propuesto | Bloquea |
| --- | --- | --- | --- |
| P01 | Obtener PDF original o aprobar por escrito la transcripción como línea base y registrar diferencias | Dueño del producto | Alcance definitivo |
| P02 | Aprobar OneToOne, tipos admitidos, número EXP-UUID o formato correlativo y política de recepción sin guía | Operación / producto | Fase 2 |
| P03 | Validar matriz, cuentas operativas, segregación y ausencia de aprobaciones automáticas para ADMIN/superuser | Gerencia / planta | Fase 2 |
| P04 | Definir documentos mínimos, datos obligatorios y muestra de guía de ingreso | Oficina / producto | Guía y recepción completas |
| P05 | Inventariar esquema productivo, backups, permisos Drive y disponibilidad de servicios | Responsable técnico | Aplicación de cambios futuros |
| P06 | Validar checklist de inspección, catálogo de acciones/condiciones y versionado de planos | Planta | Diagnóstico/biblioteca |
| P07 | Confirmar protocolo, unidades, precisión, shims, backlash, presión, significado de las 16 posiciones y tolerancias con fuente | Responsable técnico de planta | Calibración y aprobación técnica |
| P08 | Definir autorización comercial válida, alcance/cambios, rechazados/devolución y documentos respaldo | Gerencia / oficina | Flujo comercial |
| P09 | Definir firmas, retención, publicación al cliente, cierre, adendas y reapertura | Gerencia / planta | Emisión final/cierre |
| P10 | Seleccionar motor PDF y política de copia local/Drive mediante prueba de maquetación y recuperación | Responsable técnico / producto | Emisión de guía/final según fase |
| P11 | Política de transferencia de propietario/empresa y acceso a expedientes históricos | Gerencia / seguridad | Transferencias, no recepción ordinaria |
| P12 | Validar si se requieren varios roles por persona y si protocolos de bombas difieren | Gerencia / planta | Ampliación de permisos/calibración de bombas |

P07 no bloquea diseñar recepción; sí impide implementar o declarar completa calibración. P01 puede resolverse aprobando una línea base alternativa explícita; la ausencia del PDF no se interpreta como aceptación. No solicitar permisos para esta documentación: ya está autorizada por el encargo.

## Plan de pruebas para implementación futura

| Área | Casos esenciales / resultado esperado |
| --- | --- |
| Regresión existente | Ejecutar suites accounts y masiscam en entorno aislado; clientes, productos, usuarios, sesiones, QR, informes HTML, registros y Drive mantienen comportamiento esperado para registros sin expediente |
| Recepción e identidad | Múltiples expedientes por equipo, un expediente por registro, doble envío idempotente, número concurrente único e inmutable, QR/token/serie/IDs antiguos preservados |
| Aislamiento | Referencias cruzadas por empresa/cliente rechazadas; perfil/rol/empresa inactivos sin acceso; cambio de cliente en sesión no filtra históricos; probar ID, token, rutas públicas/legacy y descargas |
| Permisos | Cada celda denegada de matriz devuelve rechazo y no escribe; técnico no asignado, CLIENTE, CONSULTA, ADMIN y superusuario sin rol operativo no aprueban |
| Flujo | Saltos prohibidos, prerequisitos incompletos, observación/rechazo, corrección y nuevo envío conservan decisiones previas; invalidación bloquea etapas posteriores |
| Concurrencia | Aprobación vs edición, cierre vs cambio, carga vs borrado, solicitudes duplicadas; probar PostgreSQL real, no inferir locks desde SQLite |
| Calibración | Borrador incompleto permitido; envío exige 16 posiciones únicas, unidades/protocolo, precisión/límites validados; no inventar conformidad cuando falte regla |
| Documentos | Exclusión de fuentes, hash/deduplicación, MIME/firma de archivo, vínculo correcto de etapa; documentos comerciales no aparecen en PDF/descarga pública sin publicación explícita |
| Conservación | Borrado directo/por cascada, reemplazo, archivado y actualización masiva de datos aprobados/cerrados bloqueados antes de efectos Drive; adenda conserva bytes/hash anterior |
| Drive/Celery | Respuesta remota perdida, broker caído, tarea repetida, archivo ausente/movido, subida parcial, caída tras commit; recuperar sin duplicar ni recrear borrados |
| PDF | Muestras extensas con fotos/tablas/planos; inspección visual y extracción; portada, páginas, anexos, firmas y unidades legibles; hash, snapshot, revisión y autorización coherentes |
| Migraciones | Copia anonimizada con registros sin cliente/archivos incompletos; migración aditiva; conteos/PK/tokens/IDs/hashes iguales; backfill selectivo repetible y restauración ensayada |

No se agregan tests de código para estos documentos. La validación de Fase 1 consiste en comprobar los seis entregables, enlaces internos, consistencia de decisiones y diff limitado a Markdown nuevo.

## Viabilidad del informe final

Hoy se reutilizan identificación del cliente/equipo, fotografías, observaciones e historial. El HTML actual no contiene recepción formal, diagnóstico, piezas, repuestos aplicados, calibración ni aprobaciones estructuradas. El soporte reutilizable estimado en la auditoría es 25–30 % de secciones/datos; no representa porcentaje de implementación PDF ni una estimación de esfuerzo.

Emisión propuesta: snapshot aprobado → reserva idempotente → generación fuera de locks → Documento persistente con hash → sincronización Drive → revisión del PDF → emisión inmutable → cierre. Si cambia la revisión antes de emitir, descartar como vigente el resultado y conservar trazabilidad. Determinar cuáles anexos se incluyen y cuáles se referencian; nunca mezclar todos los archivos de la carpeta automáticamente.

## Dictamen de preparación

Arquitectura documentada y apta para revisión técnica/funcional. Compatibilidad prevista por extensión opcional, reutilización de identidades/archivos y migraciones aditivas; no está demostrada mediante ejecución productiva. Lista para preparar el alcance de Fase 2, condicionada a P02–P05 y P10 para la guía, más P01 o línea base alternativa aprobada. Los puntos posteriores pueden resolverse en su fase antes de implementarlos.

No iniciar Fase 2 sin autorización expresa. Esta entrega no genera modelos, migraciones, dependencias ni cambios de infraestructura.
