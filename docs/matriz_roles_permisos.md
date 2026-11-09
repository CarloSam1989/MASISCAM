# Matriz de roles y permisos

Fecha: 2026-11-05. Propuesta de diseño, no permisos ya implementados. PDF original no disponible. Ver [arquitectura](arquitectura_expedientes.md).

## Compatibilidad

Mantener ADMIN, TECNICO, CONSULTA y CLIENTE sin convertir usuarios automáticamente. Añadir OFICINA, PLANTA y GERENCIA (códigos propuestos, compatibles con max_length=12 actual) a RolMasiscam. TECNICO se reutiliza para el rol técnico requerido. No crear otro modelo de usuarios/roles.

ADMIN conserva gestión administrativa heredada, pero no obtiene aprobación comercial/técnica del expediente por equivalencia implícita. Un administrador podrá asignar un rol operativo mediante el mecanismo existente; toda reasignación se audita. El modelo actual admite un rol por perfil/empresa: la matriz propone esa misma cardinalidad. Si una persona debe acumular funciones, deberá aprobarse expresamente un diseño adicional; no usar cuentas compartidas ni superusuario como sustituto.

CONSULTA conserva lectura histórica y no vuelve a ofrecerse para altas. CLIENTE conserva acceso histórico autorizado; no recibe aprobaciones digitales nuevas. La autorización del cliente se incorpora como respaldo documental gestionado por OFICINA y registrado por GERENCIA. Un portal de firma/aprobación del cliente es alcance futuro pendiente.

## Matriz propuesta para expedientes

Leyenda: S = permitido en su empresa y con precondiciones; A = solo asignado al expediente; L = lectura autorizada; P = solo contenido publicado y propio; — = denegado. Una operación S/A nunca permite alterar revisiones aprobadas o cerradas.

| Operación | OFICINA | TECNICO | PLANTA | GERENCIA | ADMIN | CONSULTA | CLIENTE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Consultar expediente técnico | L | A | L | L | L | L | P |
| Consultar documentación comercial | L | — | — | L | L | — | P |
| Abrir recepción y registrar guía | S | — | — | — | — | — | — |
| Cargar/clasificar documentos de recepción/comerciales | S | — | — | S | — | — | — |
| Completar recepción y emitir guía | S | — | — | — | — | — | — |
| Asignar técnicos | — | — | S | — | — | — | — |
| Inspeccionar y diagnosticar | — | A | — | — | — | — | — |
| Solicitar piezas | — | A | — | — | — | — | — |
| Mantener biblioteca técnica | — | — | S | — | — | — | — |
| Registrar decisión comercial con respaldo del cliente | — | — | — | S | — | — | — |
| Registrar recepción de repuestos | S | A | — | — | — | — | — |
| Ejecutar reparación/montaje | — | A | — | — | — | — | — |
| Registrar calibración y enviar a revisión | — | A | — | — | — | — | — |
| Observar/aprobar/rechazar técnicamente | — | — | S | — | — | — | — |
| Generar borrador de informe inicial/final | S | A | S | — | — | — | — |
| Autorizar contenido técnico y emisión final | — | — | S | — | — | — | — |
| Publicar versión aprobada al cliente | S | — | — | S | — | — | — |
| Cerrar expediente completo | — | — | — | S | — | — | — |
| Solicitar corrección posterior al cierre | S | A | S | S | — | — | — |
| Autorizar reapertura/adenda | — | — | S* | S* | — | — | — |
| Gestionar usuarios/roles heredados | — | — | — | — | S | — | — |
| Eliminar expediente/evidencia aprobada | — | — | — | — | — | — | — |

S*: adenda técnica requiere PLANTA; cambios de alcance/autorización o reapertura requieren además GERENCIA. El solicitante no puede aprobar su propia solicitud. La política de doble aprobación se considera propuesta pendiente de aceptación operativa.

OFICINA administra metadatos y recepción; no redacta diagnóstico ni modifica mediciones. PLANTA revisa y solicita correcciones; la propuesta separa ejecución de aprobación. GERENCIA no sustituye al cliente: registra una decisión sustentada en su autorización y alcance vigente.

## Autorización en servidor

Permisos de negocio propuestos: `expediente_ver`, `recepcion_gestionar`, `tecnico_asignar`, `diagnostico_editar`, `biblioteca_gestionar`, `comercial_decidir`, `repuestos_recibir`, `reparacion_editar`, `calibracion_editar`, `tecnica_decidir`, `informe_emitir`, `informe_publicar`, `expediente_cerrar`, `adenda_autorizar`. Los nombres son contrato de diseño; no permisos creados.

La matriz se traduce a comprobaciones explícitas en servicios. No basarse en permisos genéricos actuales `editar`, `archivar` o `reemplazar`, ni en campos POST de empresa/autor/aprobador. Nuevas operaciones no heredan el bypass automático de `is_superuser` de `tiene_permiso`: superusuario técnico sin rol operativo no firma decisiones de negocio. Un eventual acceso de emergencia requiere procedimiento separado, nunca aprobación silenciosa.

En cada petición y descarga:

1. Usuario, perfil, rol y empresa deben estar activos.
2. Empresa activa = empresa fijada del expediente = empresa del equipo; rechazar referencias cruzadas.
3. Técnico debe estar asignado; el cambio de asignación no altera autores históricos.
4. CLIENTE debe corresponder al cliente contractual del expediente y al dueño actualmente autorizado del equipo. Si cambia la propiedad, denegar por defecto y resolver el acceso histórico explícitamente; no transferir los documentos al nuevo cliente.
5. Validar estado/revisión y permiso de operación. Autor de los trabajos revisados no puede aprobarlos; comprobar autorías de diagnóstico, reparación y calibración, no solo el usuario que envió la revisión.
6. Restringir documentos comerciales por operación y clasificación, incluso si se conoce su ID de Drive.

## Rutas heredadas

`access.py` contiene un allowlist de vistas de CLIENTE; futuras vistas requieren inclusión deliberada y solo GET/HEAD para consulta. `EmpresaActivaMiddleware` fija la empresa de clientes y bloquea administración. Reutilizar esta frontera sin confiar en ella como única comprobación.

Las rutas actuales de edición/eliminación de registros, reemplazo/archivado de documentos, cambio de cliente/equipo, fotografías, consulta pública y Django admin necesitan pruebas de bypass cuando el registro tenga expediente. Conservar comportamiento de registros sin expediente. No otorgar CLIENTE la lista recursiva completa de una carpeta con documentación comercial privada.
