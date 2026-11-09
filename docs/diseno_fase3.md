# Fase 3: inspección, diagnóstico y biblioteca técnica

Fecha: 2026-11-06. **Diseño para revisión y autorización expresa. No implementado.** La aprobación de recepción de Fase 2 no aprueba este diseño, protocolos industriales ni decisiones comerciales. Fase 2 permanece limitada a QA/local, sin autorización de producción.

## 1. Base real revisada y límites

Fuentes: `arquitectura_expedientes.md`, `diccionario_datos.md`, `flujo_estados_mantenimiento.md`, `matriz_roles_permisos.md`, `cierre_operativo_fase2.md`; código en `sistema/masiscam/models.py`, `access.py`, `recepcion_services.py`, `recepcion_confirmacion.py`, `recepcion_guards.py`, `recepcion_tasks.py`, `recepcion_pdf.py`, `drive_documents.py`, servicios/rutas documentales y migraciones hasta 0018. Se contrastaron las dependencias de `sistema/requirements.txt` y el resumen QA `sistema/artifacts/recepcion-qa/activacion-local/resumen.json`.

| Confirmado en el proyecto | Consecuencia para el diseño |
| --- | --- |
| Equipo conserva identidad/token y tiene múltiples RegistroEquipo; ExpedienteMantenimiento es OneToOne con un registro | Todo trabajo nuevo pertenece al expediente de esa intervención; no crear otro equipo, QR o número de expediente |
| ConfirmacionRecepcion enlaza la recepción con su guía persistente, política y responsable | Exigir recepción confirmada para iniciar inspección; no basta que exista RegistroEquipo o un PDF provisional |
| 0018 impide insertar EvidenciaRecepcion o EmisionGuiaIngreso después de confirmar | No usar esas tablas para fotografías técnicas ni informe inicial; mantener las guardas y documentos de recepción |
| No existen modelos de inspección, diagnóstico, asignación técnica, biblioteca estructurada o piezas requeridas | Son propuestas nuevas; campos de documentos/observaciones existentes no constituyen ese flujo |
| Documento ya tiene PLANOS, MANUALES, FOTOGRAFIAS e INFORMES, archivo, tamaño/hash, privacidad y metadatos Drive | Reutilizar archivo y categorías; añadir relaciones de negocio, sin otro FileField ni copia automática de catálogos |
| Documento exige Proyecto; la unicidad activa es proyecto/hash | No crear un proyecto ficticio de biblioteca ni quitar la FK por conveniencia; resolver el acceso de biblioteca entre proyectos mediante relaciones explícitas |
| `_guardar_documento` de recepción rechaza un hash existente y siempre crea un documento del equipo | No reutilizarlo sin adaptación como cargador de biblioteca; necesita resolución de documentos existentes con autorización y sin cambiar visibilidad |
| `recepcion_services.autorizar` no admite TECNICO y limita escritura a OFICINA | Crear autorización técnica específica; no ampliar esa función y dar por accidente acceso a documentación de recepción/comercial |
| Guardas y exclusiones del visor Drive conocen únicamente relaciones de Fase 2 | Las futuras relaciones técnicas requieren protección explícita en ORM, SQL, descarga y visor/listados Drive; las actuales no las cubren automáticamente |
| ReportLab 5.1.0, Pillow, Django, PostgreSQL/Celery y servicios Drive ya declarados | Reutilizar; no proponer otro motor PDF ni instalar dependencias en esta fase |

La evidencia anterior de 267 pruebas PostgreSQL y 262 aprobadas/5 omitidas en SQLite pertenece a Fase 2. No se ejecutaron pruebas nuevas ni se verificó una base real en esta revisión. El PDF original de requerimientos continúa sin contenido disponible para contrastar; no se presume leído ni aprobado.

## 2. Decisiones estructurales propuestas

- Conservar intactos ExpedienteMantenimiento, recepción, correlativo, token, RegistroEquipo y sus snapshots. Añadir relaciones protegidas para trabajo técnico, sin campos operativos nuevos en la raíz inmutable.
- Una inspección y un diagnóstico pueden tener varias revisiones por expediente. Borradores editables con control de versión; una revisión enviada no cambia. Corregir crea otra revisión y conserva fuente, evidencias, decisiones y PDF anteriores.
- Biblioteca de empresa, con referencias PLANOS/MANUALES versionadas y componentes reutilizables. La selección parte del listado asociado al plano; interacción gráfica sobre CAD no se presume existente.
- Repuestos son requerimientos de diagnóstico, sin precios, órdenes, compras, reservas, saldos o movimientos de inventario.
- Reutilizar Auditoria para transiciones y asignaciones. No crear EventoTransicion duplicando los mismos eventos en esta fase; incorporar una tabla de decisiones solo para decisiones técnicas con identidad/revisión propias.
- Informe inicial con una entidad de emisión técnica propia, reutilizando Documento y primitivas de integridad/almacenamiento. EmisionGuiaIngreso sigue siendo exclusiva de recepción; no eliminar su bloqueo para alojar informes nuevos.

## 3. Modelos y relaciones propuestos

Nombres orientativos para el modelo físico posterior. FK de negocio y autoría con PROTECT; fechas de servidor con zona horaria. Empresa se deriva del expediente cuando exista ese vínculo; la biblioteca sí requiere empresa explícita. Ningún campo industrial recibe un valor numérico por defecto.

### 3.1 Asignación y etapas

| Modelo | Campos y relaciones | Reglas |
| --- | --- | --- |
| AsignacionTecnica | FK expediente, técnico y asignado_por; inicio, fin opcional, motivo; clave idempotente | PLANTA asigna usuarios TECNICO activos de la misma empresa. Una asignación principal activa por expediente como propuesta D01. Conservar asignaciones anteriores al cerrar su vigencia; no editar su autor/inicio ni borrarlas |
| EtapaExpediente | FK expediente, código INSPECCION/DIAGNOSTICO, estado, versión de concurrencia, revisión vigente, inicio/fin opcionales | UNIQUE(expediente, código). Solo crear estas dos etapas bajo demanda, sin etapas futuras vacías ni backfill histórico. El indicador de revisión debe usar FK explícitas a Inspeccion/Diagnostico con exclusión por código, no GenericForeignKey ni un ID JSON sin integridad |

Recepción se consulta desde ConfirmacionRecepcion, sin duplicar su estado. Ausencia de etapa técnica se muestra NO_INICIADO; estado documental/Drive y Equipo.estado no representan avance técnico.

### 3.2 Trabajo técnico y evidencias

| Modelo | Datos | Relaciones y restricciones |
| --- | --- | --- |
| Inspeccion | revisión interna, estado físico, condiciones generales, hallazgos/daños, observaciones, fecha efectiva, técnico/creado_por, creado_en, enviado_en opcional, versión de edición | FK expediente; sustituye FK opcional al mismo modelo; UNIQUE(expediente, revisión). Técnico autor único de la revisión, con asignación vigente; un cambio de autor requiere una revisión nueva |
| Diagnostico | revisión interna, fallas identificadas, posibles causas, componentes afectados, recomendaciones, conclusiones, observaciones, fecha efectiva, autor/fechas, versión de edición, declaración sobre repuestos | FK expediente y FK a inspección enviada concreta, sustituye FK opcional. UNIQUE(expediente, revisión). La inspección y los datos copiados pertenecen al mismo expediente |
| EvidenciaTecnica | FK Documento, clasificación, descripción, autor/fecha, orden | Exactamente una FK destino: Inspeccion o Diagnostico. Clasificaciones propuestas ESTADO_FISICO/HALLAZGO/DANO/DETALLE/OTRA por validar en D03; descripción para OTRA. Múltiples fotos, sin inventar un mínimo por daño o pieza. Insertar/editar/retirar vínculos solo en borrador; enviados inmutables |
| DecisionTecnica | FK Diagnostico exacto, solicitud UUID, decisión APROBADO/OBSERVADO/RECHAZADO, motivo, responsable, fecha | Append-only y UNIQUE(solicitud). Modelo condicionado a aprobar el circuito de revisión D02; no representa autorización comercial ni aprobación final de calibración |

Una evidencia de inspección puede citarse en diagnóstico enlazando el mismo Documento y conservando su procedencia; no copiar bytes para cada revisión. Reutilizar fotografías de recepción mediante referencia protegida solo con autorización de lectura para ese expediente; no insertar en EvidenciaRecepcion confirmada ni dar acceso al conjunto documental de recepción al técnico.

No diseñar checklists, criterios de daño, causas automáticas, fórmulas, unidades o tolerancias sin fuente de planta. «Causa no determinada» debe ser una declaración explícita del técnico, no una conclusión automática. Los campos de borrador pueden estar incompletos; campos exigibles al envío se validarán en D03. Componentes afectados pueden describirse manualmente, sin obligar a que todo exista en catálogo.

### 3.3 Biblioteca técnica

| Modelo | Datos y vínculos | Reglas |
| --- | --- | --- |
| ReferenciaTecnica | FK empresa y Documento; tipo PLANO/MANUAL; código interno, revisión interna, título, marca/modelo/ratio disponibles, versión declarada por fabricante, procedencia, autor/fecha, estado BORRADOR/PUBLICADA/RETIRADA; sustituye opcional | UNIQUE(empresa, código, revisión interna). La revisión interna no simula una versión del fabricante. Ratio/versión desconocidos permanecen desconocidos, no comodines compatibles. Contenido publicado inmutable; retirar no elimina contenido ni referencias usadas |
| ComponenteTecnico | FK empresa, código interno, fabricante si consta, número de parte si consta, descripción y unidad si consta, autor/fecha | Identidad de catálogo reutilizable, código interno único por empresa. No asumir número de parte globalmente único ni completar fabricante/unidad desconocidos. Cambios de catálogo no cambian snapshots de partes o diagnósticos enviados |
| ParteReferenciaTecnica | FK referencia PLANO, FK componente opcional; posición/identificador del listado, número de parte/descripcion/unidad congelados desde fuente, cantidad de referencia opcional, página/etiqueta de referencia opcionales | UNIQUE(referencia, posición) para posiciones explícitas del plano. Datos de fuente, no cantidades inventadas. Se congela junto con la referencia publicada; modificación requiere otra versión |
| AplicabilidadReferenciaEquipo | FK referencia y Equipo, motivo/fuente, autor/fecha | UNIQUE(referencia, equipo), misma empresa. Vínculo explícito, no coincidencia automática por nombre. La nueva asociación o su retiro se audita; un diagnóstico emitido conserva la referencia/versionado usados |

Se unifican PlanoTecnico y manuales en ReferenciaTecnica para compartir búsqueda, versiones y protección; ParteReferenciaTecnica concreta la anterior propuesta PartePlano. No introducir simultáneamente otro catálogo de planos paralelo.

Documento sigue perteneciendo a su proyecto real. La referencia de biblioteca puede ser usada por equipos de otros proyectos de la misma empresa mediante una ruta técnica autorizada para esa referencia; esto no concede acceso al proyecto de origen ni a sus otros documentos. No trasladar el archivo, cambiar IDs Drive, crear carpetas comunes ni hacer opcional Documento.proyecto en esta entrega propuesta. Compartir entre empresas queda denegado.

Documentos existentes PLANOS/MANUALES son candidatos, no incorporaciones automáticas. Antes de publicar una referencia: verificar empresa, origen, permisos, copia persistente y hash, y que la protección documental no altere inesperadamente usos heredados. Reutilizar un documento privado y compatible con su propietario autorizado; no convertir silenciosamente uno público/compartido en privado o inmutable. Si ese uso no es compatible, dejar la referencia en borrador y resolver D04; no duplicar bytes para saltar la restricción proyecto/hash. Un manual disponible solo en Drive no se considera copia persistente local; incorporación controlada requiere autorización y validación, sin sincronización/importación real en esta fase.

Primera interfaz propuesta: filtros marca/modelo/ratio/versión, vista del documento autorizado y listado de partes con selección. El usuario elige la versión concreta y el servidor valida aplicabilidad. No inferir compatibilidad mecánica ni ratios equivalentes por normalizar cadenas. PDF/imágenes admiten visualización con capacidades existentes. DWG/DXF conservan descarga autorizada; no se promete render CAD, conversión, OCR, extracción automática o áreas clicables. D05 decide si se incluye anotación gráfica posterior, siempre ligada a página y versión congelada.

### 3.4 Repuestos requeridos

PiezaRequerida: FK Diagnostico y FK ParteReferenciaTecnica opcional; origen BIBLIOTECA/MANUAL; número de parte, descripción, unidad y referencia/versionado en snapshot; cantidad Decimal positiva; condición, acción recomendada y observaciones. Lista de piezas forma parte de la revisión enviada, no del equipo maestro.

Origen BIBLIOTECA exige una parte de referencia publicada, misma empresa y aplicabilidad validada; origen MANUAL permite identificación incompleta explícita y motivo/contexto, sin inventar número de parte. Catálogos de condición/acción y precisión/unidades/cantidades fraccionarias quedan en D06. La cantidad elegida por el técnico no se deduce de la cantidad de referencia del fabricante.

Al enviar: piezas completas o declaración explícita de que no se requieren, según aprobación de D03. No se crea una compra, asignación de stock o autorización comercial al seleccionar una pieza. El cambio de una pieza después del envío crea una revisión nueva, sin modificar el PDF anterior.

### 3.5 Informe inicial

EmisionInformeTecnico: FK expediente y Diagnostico enviado exacto; tipo INICIAL; versión positiva por expediente/tipo; UUID idempotente y huella; estado RESERVADA/GENERANDO/GENERADA/ERROR; snapshot JSON versionado; FK Documento opcional protegido; SHA-256, versión de plantilla, responsable/fechas, error seguro y emisión anterior opcional. UNIQUE(expediente, tipo, versión), UNIQUE(clave). Documento obligatorio al GENERADA, con hash/tamaño/copia verificados. No otro FileField.

La relación inspección se obtiene del diagnóstico. Snapshot contiene: identificación congelada del expediente y recepción confirmada, revisiones fuente, estado físico, hallazgos, daños, diagnóstico, conclusiones, fotografías/leyendas, repuestos, referencias técnicas con su versión, autores/decisiones aplicables y manifest de IDs/hash. Fuente principal para cliente/equipo: snapshot de recepción; información adicional identificada con procedencia y fecha, sin sobrescribirlo con la ficha actual.

Guía de ingreso y biblioteca no se reescriben para construirlo. Propuesta: incorporar fotografía y datos/leyendas de plano; anexar páginas de plano solo si se aprueban D05/D07 y los formatos lo permiten. ReportLab se reutiliza para composición; no asumir que incrusta cualquier PDF o CAD existente ni añadir una librería de unión de PDFs sin justificarla. Firmas: identificación y espacios vacíos; ningún certificado o aprobación simulada.

## 4. Formularios, rutas y servicios

| Propuesta | Función y validación en servidor |
| --- | --- |
| AsignacionTecnicaForm | PLANTA selecciona técnico de la empresa; motivo de reasignación, sin cambiar autores de revisiones existentes |
| InspeccionForm + evidencias | Narrativa, fecha efectiva, fotografías clasificadas/leyendas. Autor/empresa/estado fijados por servidor, no campos editables |
| DiagnosticoForm + formset PiezaRequerida | Inspección fuente exacta, fallas, causas, componentes, recomendaciones y conclusiones; elección de parte o entrada manual |
| ReferenciaTecnicaForm + listado de partes | Candidato documental autorizado, metadatos de fuente, versión y aplicabilidad; no aceptar URLs remotas arbitrarias para render |
| DecisionTecnicaForm | Revisión/solicitud, decisión y motivo; actor/fecha de servidor, veto de autoaprobación |
| SolicitudInformeInicialForm | Revisión esperada y clave idempotente; muestra estado de generación, versión, integridad y sincronización por separado |

Rutas orientativas bajo `/app/expedientes/<id>/tecnico/` y `/app/biblioteca-tecnica/`: detalle, inspecciones, diagnósticos, envío, decisión, piezas e informe inicial. Mutaciones únicamente POST con CSRF; GET/listados/descargas sin crear carpetas ni emitir documentos. Los endpoints de biblioteca y descarga vuelven a autorizar la referencia/documento concreto.

Servicios propuestos: `autorizar_tecnica`, `asignar_tecnico`, `guardar_inspeccion`, `enviar_inspeccion`, `guardar_diagnostico`, `enviar_diagnostico`, `decidir_diagnostico`, `registrar_referencia`, `publicar_referencia`, `seleccionar_pieza`, `reservar_informe_inicial`, `generar_informe_inicial`. No exponer ModelForms genéricos que permitan cambiar estado, FK ajenas, autoría o contenido enviado.

Cada mutación usa transacción, bloqueo del expediente y revisión esperada; contenido incompatible con una clave repetida o versión obsoleta se rechaza como conflicto. Un técnico asignado edita únicamente revisiones de su autoría; la colaboración multiautor no se introduce implícitamente. Reasignación exige nueva revisión si cambia el autor, conservando enlaces de procedencia.

Definir y probar orden único de locks para expediente, revisión, etapa/emisión y documentos; biblioteca se publica bajo su propio bloqueo de versión. No añadir llamadas Drive bajo locks. La apertura heredada de expediente conserva sus locks; no modificarla como parte de Fase 3.

Primitivas reutilizables: validación de fotografías/archivos, SHA-256, lectura de vuelta y `evento`/Auditoria. Separar en el futuro esas primitivas de la autorización específica de recepción con una refactorización acotada y regresión Fase 2. La resolución de archivo existente debe comprobar propietario/proyecto/empresa, uso autorizado y protección; no alterar su privacidad para el nuevo vínculo.

## 5. Estados, revisiones y transiciones propuestas

| Etapa / operación | Transición | Precondiciones y resultado |
| --- | --- | --- |
| Iniciar inspección | NO_INICIADO → EN_PROCESO | Recepción confirmada con guía íntegra; técnico asignado y autorizado |
| Enviar inspección | EN_PROCESO → FINALIZADO | Validaciones D03, evidencias persistentes y revisión congelada. Finalizado no significa aprobado técnicamente |
| Iniciar diagnóstico | NO_INICIADO → EN_PROCESO | Inspección enviada concreta y vigente |
| Enviar diagnóstico | EN_PROCESO → PENDIENTE_APROBACION | Revisión y piezas/evidencias congeladas; circuito de revisión propuesto D02 |
| Decisión PLANTA | PENDIENTE_APROBACION → APROBADO / OBSERVADO / RECHAZADO | Decisión sobre revisión concreta, motivo al observar/rechazar, actor independiente |
| Emitir informe inicial | APROBADO → FINALIZADO | Propuesta D02: PDF persistido/verificado del diagnóstico aprobado; un error de PDF no equivale a rechazo técnico |
| Corregir observado/rechazado | Nueva revisión EN_PROCESO → nuevo envío | Conservar decisión y revisión anterior; no editar ni borrar lo enviado. Reconsideración de rechazo con motivo por PLANTA como propuesta D02 |

La revisión de inspección finalizada también crea otra revisión y requiere motivo/permiso PLANTA propuestos. Diagnósticos dependientes quedan no vigentes mediante eventos y selección de nueva revisión vigente, sin cambiar sus textos/PDF. Si una fuente cambia mientras se genera el informe, conservar o descartar su publicación como vigente según revisión esperada; nunca emitirlo como si correspondiera al nuevo diagnóstico.

D02 debe aprobar si el informe inicial requiere revisión PLANTA. Se recomienda esa separación; no está autorizada todavía. Sin esa decisión no implementar silenciosamente una aprobación obligatoria ni permitir al técnico aprobarse. Si se autoriza emisión solo del autor, deberá marcarse como diagnóstico enviado, sin atribuir aprobación independiente y con flujo documentado distinto.

Prohibición de autoaprobación: comparar identidad de usuario con autores de diagnóstico, inspección y revisiones fuente copiadas, incluso después de reasignación/cambio de rol. Conservar autores con PROTECT y procedencia sin ciclos. PLANTA no modifica el trabajo que revisa; si participa como autor en una revisión fuente, deberá revisar otra persona habilitada. Si no existe, el envío queda pendiente, sin bypass por ADMIN/superusuario.

Todo avance registra Auditoria con expediente, revisión, actor, instante, estado anterior/nuevo, motivo y clave/huella idempotentes; la modificación y el evento se confirman juntos. Guardas evitan cambios/borrados o inserciones de hijos después del envío, también por queryset/bulk/SQL y Django admin.

## 6. Permisos propuestos

Todos requieren usuario/perfil/rol/empresa activos y objeto dentro del ámbito. La matriz es para aprobar, no permisos activados. L: lectura técnica de empresa; A: solo asignado; —: denegado. No autoriza lectura comercial.

| Operación | OFICINA | TECNICO | PLANTA | GERENCIA | ADMIN | CONSULTA | CLIENTE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Leer trabajo técnico / informe interno | L | A | L | L | L | L | — |
| Asignar/reasignar técnicos | — | — | Sí | — | — | — | — |
| Crear/editar/enviar inspección y diagnóstico | — | A + autor | — | — | — | — | — |
| Seleccionar piezas o registrar piezas manualmente | — | A + autor | — | — | — | — | — |
| Mantener/publicar biblioteca | — | — | Sí | — | — | — | — |
| Consultar biblioteca autorizada | L | A | L | L | L | L | — |
| Observar/rechazar/aprobar diagnóstico, si D02 aprobado | — | — | Sí, independiente | — | — | — | — |
| Reservar/reintentar informe de revisión habilitada | Sí | A | Sí | — | — | — | — |
| Eliminar emitidos o alterar originales enviados | — | — | — | — | — | — | — |
| Autorizar reparación / compras | — | — | — | — | — | — | — |

OFICINA puede solicitar generación desde datos congelados, no editar diagnóstico ni aprobarlo. Lectura TECNICO de biblioteca limitada a referencias aplicables de expedientes asignados; ampliar a navegación de toda la biblioteca requiere D01. Mantener roles históricos sin conversiones ni cuentas compartidas. CLIENTE conserva sus funcionalidades históricas, pero no recibe nuevos documentos internos por ampliar el allowlist o conocer un ID.

Extender el concepto de documento protegido con relaciones EvidenciaTecnica/ReferenciaTecnica/EmisionInformeTecnico: bloquear reemplazo, archivado, publicación, borrado y mutación de bytes/metadatos de contenido cuando corresponda. Abarcar descargas directas, rutas heredadas, administración, visor/listado Drive y búsquedas, no solo el botón de interfaz. Metadatos de sincronización pueden actualizarse sin modificar contenido congelado.

## 7. Informe, almacenamiento y Drive/Celery

1. Reservar bajo bloqueo una emisión para diagnóstico y revisión exactos con clave/huella; congelar datos y manifest antes de encolar. Una recarga con otro nonce recupera la emisión actual; versión nueva requiere intención explícita o revisión nueva.
2. ReportLab renderiza solo contenido autorizado y escapado, sin URLs arbitrarias ni lectura libre de rutas. Comprobar hashes de evidencias y referencias antes de incluirlas.
3. Persistir mediante Documento privado, leer de vuelta y comprobar tamaño/SHA-256; solo entonces marcar GENERADA y permitir descarga/emisión válida. Error conserva la reserva para reintentar; no duplicar versión/documento/auditoría.
4. Revalidar vigencia de revisión bajo lock al publicar el resultado. Una decisión antigua o un PDF de revisión anterior nunca habilita avance de la revisión nueva.
5. Encolar sincronización con `sincronizar_documento`/`encolar_drive` después de commit; reutilizar IDs/reintentos y agregar selección de emisiones técnicas a una reconciliación persistente. El comando de recepción actual selecciona guías y no recupera informes técnicos automáticamente.
6. Drive pendiente o cola caída no implica rechazo ni PDF inexistente si hay copia persistente íntegra. Archivo corrupto/no disponible bloquea descarga/uso, exige recuperación de respaldo y nunca se legitima cambiando el hash.

La copia local y DB no constituyen una transacción distribuida. Prever archivos huérfanos tras rollback y ventana commit/encolado; no purgar históricos ni prometer entrega exactamente una vez. No reorganizar carpetas ni crear una estructura remota paralela. Verificar que el sincronizador existente no exponga referencias de biblioteca o informes a lectores de carpetas heredadas.

## 8. Límite comercial y reparación

Fase 3 no implementa autorización comercial, reparación, compras ni inventario. En el estado actual esos modelos/servicios no existen; por tanto, la salida de diagnóstico/informe NO habilita ejecutar reparación ni marcar autorización comercial favorable. Mostrar «Autorización comercial pendiente; reparación no habilitada».

Para la fase futura, la autorización deberá referenciar el diagnóstico/piezas/informe y alcance exactos, respaldo del cliente, responsable GERENCIA, fecha y decisión vigente. Una corrección que cambie alcance requerirá nueva autorización. En esta fase solo se diseña esa dependencia: no crear aprobaciones ficticias, un booleano de cliente en POST o etapas comerciales vacías. Pruebas negativas deben demostrar que el nuevo flujo no abre accesos a reparación por rutas o permisos heredados del expediente.

## 9. Migraciones previstas, no generadas

Partir de 0018. Numeración futura orientativa, sujeta a otras ramas; no editar migraciones 0015–0018 ni asumir que estén aplicadas en producción.

| Lote previsto | Contenido | Protección de datos existentes |
| --- | --- | --- |
| A: biblioteca | ReferenciaTecnica, ComponenteTecnico, ParteReferenciaTecnica, AplicabilidadReferenciaEquipo; índices empresa/filtros y unicidades | Tablas nuevas vacías; sin explorar Drive, mover archivos o importar catálogos automáticamente |
| B: trabajo técnico | AsignacionTecnica, EtapaExpediente, Inspeccion, Diagnostico, EvidenciaTecnica, PiezaRequerida y DecisionTecnica si D02 aprobado | Relaciones opcionales hacia Fase 2; sin crear revisiones para históricos ni cambiar recepción/QR |
| C: emisión y guardas | EmisionInformeTecnico; restricciones de versiones, autoría, vínculos de empresa/revisión y nuevas protecciones documentales | Ampliar guardas mediante migraciones posteriores y reversibles de DDL; conservar las de recepción, no reutilizar su tabla de emisiones |

Checks de cantidad > 0, revisión > 0, destino único de evidencia y coherencia temporal; UNIQUE de revisión por expediente, etapa, parte por referencia y emisión por tipo/versión. Índices en expediente/revisión, empresa/código/estado de biblioteca y asignación vigente. La selección de FK a una revisión de otra empresa, la prohibición de alterar hijos enviados y la no autoaprobación requieren servicios más guardas adecuadas, no un CHECK inválido entre tablas.

Ensayar PostgreSQL 16 y equivalentes SQLite sobre esquema 0018 poblado con datos sintéticos y respaldados; comparar inventario, tokens, IDs Drive, bytes/hashes de PDF, recepciones confirmadas y permisos Fase 2. No hacer backfill de supuestos técnicos ni invalidar decisiones antiguas durante migración. Reversión operativa: deshabilitar Fase 3 conservando datos/guardas; no recomendar bajar migraciones destruyendo revisiones emitidas.

## 10. Pruebas necesarias y aceptación futura

| Grupo | Casos mínimos |
| --- | --- |
| Precondiciones | Impedir inspección sin recepción confirmada, con guía corrupta o sin asignación; diagnóstico solo con inspección enviada válida |
| Permisos | Matriz completa, desactivados, cambio de rol/asignación, ausencia de bypass superusuario; autor anterior no aprueba tras reasignación; no lectura comercial por técnico |
| Aislamiento | IDs ajenos en expediente, inspección, diagnóstico, parte, referencia, evidencia y descarga; biblioteca entre proyectos sin abrir el proyecto fuente; sin acceso entre empresas |
| Narrativa/evidencias | Borrador incompleto, envío validado según D03, imágenes falsas/corruptas, MIME/tamaño, leyendas y escape HTML, fotografías múltiples, reutilización autorizada sin duplicar bytes |
| Biblioteca | Marca/modelo/ratio/versión desconocidos, versiones publicadas inmutables, parte/posición, referencia retirada aún legible históricamente; no inferir compatibilidad; public/shared legacy no cambia silenciosamente |
| Repuestos | Origen manual/biblioteca, número desconocido explícito, cantidades positivas y unidades/escala D06, conflicto de versión o empresa, ausencia de piezas declarada; cero órdenes/movimientos de inventario |
| Estados | Observación, rechazo, corrección/nueva revisión, decisión independiente, vigencia de fuentes, cambio de inspección invalidando avance sin borrar resultados |
| Concurrencia PostgreSQL | Dos guardados con misma revisión esperada, envío frente a edición, decisiones simultáneas, cambios de catálogo durante selección, doble reserva/generación; misma clave con contenido diferente rechazada |
| PDF y almacenamiento | A4, saltos/paginación/tablas largas, fotos/orientación, texto congelado, hash, versión, error de render/storage/falso éxito, retry, archivo corrupto, generación que termina con revisión ya sustituida |
| Integración | Commit sin cola, worker repetido, Drive caído con copia persistente, reconciliación; Drive simulado, sin operaciones reales |
| Protección y regresión | Guardas ORM/queryset/bulk/SQL/admin; no hijos agregados a enviados; rutas antiguas/visor Drive sin filtración; suite completa Fase 2, QR, historial y documentos previos intactos |
| Interfaz | Escritorio/tablet/móvil, mensajes de bloqueo, revisión/estado técnico y documental separados; nunca mostrar aprobación o informe válido antes de respuesta servidor |

Criterio de salida: todos los escenarios aprobados por el propietario cubiertos, suite completa PostgreSQL 16 sin omisiones de concurrencia, SQLite compatible con omisiones explicadas, migración desde 0018 verificada, PDFs revisados visualmente y ninguna alteración de datos/documentos previos. Esta tabla es un plan, no resultados ejecutados.

## 11. Decisiones pendientes y riesgos

| ID | Decisión para autorización del propietario/planta | Trabajo que bloquea |
| --- | --- | --- |
| D01 | Matriz técnica, asignación principal, lectura de biblioteca, reasignación y autoría única | Escritura/acceso técnico y aceptación de permisos; no bloquea revisar este diseño |
| D02 | Revisión PLANTA del diagnóstico/informe inicial, reconsideración y correcciones | Activar envío/decisión/emisión y transiciones definitivas |
| D03 | Campos exigibles al enviar, clasificación de fotos y declaración de piezas/no piezas; formularios para reductores y bombas | Validaciones operativas finales; no inventar checklist, cantidades mínimas o tolerancias |
| D04 | Fuente y uso autorizado de biblioteca, documentos existentes compartidos/públicos, acceso entre proyectos y publicación de versiones | Incorporación/publicación de esos documentos; no copiar ni endurecer permisos heredados automáticamente |
| D05 | Listado primero o selección gráfica, formatos y tratamiento de páginas PDF/CAD | Selección gráfica/anexos; selección textual por parte es la propuesta inicial |
| D06 | Unidades, precisión de cantidad, fracciones y catálogos de condición/acción | Esquema numérico y validación de piezas antes de generar migración física |
| D07 | Contenido/presentación y circuito de emisión del informe inicial, inclusión de planos, retención y destinatarios | Emisión/publicación definitivas; CLIENTE sigue denegado hasta autorización separada |

Riesgos principales: fuga documental por visores heredados o permisos del proyecto de origen; mutación de documentos fuente; asignaciones o autorías que permitan autoaprobación; informe de revisión obsoleta; selección de una parte incompatible; pretender render CAD con ReportLab; pérdida posterior de archivos y colas; contención/deadlocks; bloqueo accidental de otros módulos al ampliar guardas. Mitigación: aislamiento explícito, snapshots y protección por relación, trazabilidad de fuentes, revisión esperada/locks, validación humana de aplicabilidad, pruebas de regresión y recuperación. Retención/restauración, límites de tamaño y volumen esperado deben revisarse antes del despliegue, sin prometer rendimiento o disponibilidad no medidos.

## 12. Plan por etapas y autorización

1. **Revisión del diseño:** resolver D01–D07 y obtener autorización expresa de implementación local/QA. Registrar las decisiones; no deducirlas de la aprobación de Fase 2.
2. **Biblioteca y fronteras documentales:** esquema aprobado, registro manual de referencias/componentes, búsqueda/listado, acceso y protección. Sin importación Drive automática ni selección CAD.
3. **Asignación e inspección:** precondición de recepción confirmada, borrador/versiones/evidencias, envío congelado y regresión de Fase 2.
4. **Diagnóstico y piezas:** revisiones ligadas a inspección, selección desde listado/manual, cantidad/condición/acción autorizadas y circuito D02. Sin compras ni reparación.
5. **Informe inicial:** reservas idempotentes, plantilla ReportLab, persistencia/SHA-256, snapshot, permisos, auditoría y recuperación Celery/Drive simulada.
6. **Aceptación integrada local:** PostgreSQL 16/SQLite, ensayos de migración respaldados, fallos/concurrencia, revisión visual y matriz de aceptación firmada por responsables. Presentar resultados para otra autorización antes de desplegar.

**Resultado de esta entrega:** se crea únicamente este documento de diseño. No se modifican código funcional, dependencias, modelos, migraciones, bases, PDFs ni Google Drive; no se ejecutan pruebas, commits, push o despliegues. Fase 3 no iniciada: pendiente de revisión y autorización expresa.
