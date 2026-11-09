# Validación final del diseño de Fase 3

Fecha: 2026-11-06. **Propuesta consolidada para autorización expresa; no implementada ni aprobada por esta documentación.** Alcance: futura implementación local/QA de inspección, diagnóstico, biblioteca, piezas requeridas e informe inicial. No habilita producción ni reparación.

## 1. Fuentes y criterio de validación

Se revisaron [diseño Fase 3](diseno_fase3.md), [arquitectura](arquitectura_expedientes.md), [diccionario](diccionario_datos.md), [roles](matriz_roles_permisos.md), [flujo](flujo_estados_mantenimiento.md), [riesgos](riesgos_y_validaciones.md), [validación previa Fase 2](validacion_previa_fase2.md) y [cierre operativo Fase 2](cierre_operativo_fase2.md). Se contrastaron las relaciones, categorías y unicidad de Documento en `sistema/masiscam/models.py` y la autorización específica en `recepcion_services.py`.

Las propuestas antiguas no se tratan como estado implementado: el número de expediente ya es correlativo por empresa, ReportLab está incorporado y las políticas de recepción están aprobadas para el alcance local. Ninguna de esas aprobaciones cubre automáticamente Fase 3. Las 267 pruebas PostgreSQL de Fase 2 son un antecedente, no pruebas de este diseño. No se consultó producción ni se ejecutaron pruebas, Django, migraciones o llamadas Drive.

«Resuelto técnicamente» significa que existe una alternativa concreta, sujeta a aprobar la regla funcional. Las decisiones D01–D07 conservan sus identificadores del diseño. Las precisiones de este documento son la propuesta a revisar; no modifican silenciosamente los documentos previos.

## 2. Matriz de las siete decisiones

| ID / tema | Propuesta recomendada | Justificación técnica | Impacto sobre modelos y permisos | Riesgos | Aprobación del propietario |
| --- | --- | --- | --- | --- | --- |
| D01: roles, asignación y autoría | Un técnico principal activo por expediente; PLANTA asigna; técnico asignado y autor edita sus borradores; OFICINA gestiona recepción y solicita PDF; GERENCIA supervisa | Evita autorías ambiguas y permisos técnicos heredados de permisos genéricos | AsignacionTecnica con historial y unicidad activa; autoría protegida en revisiones; autorización técnica independiente de recepción | Reasignación que deje trabajo sin responsable; acumulación de roles; filtración documental | **Sí:** matriz de sección 3, autor único y alcance de biblioteca |
| D02: revisión y correcciones | PLANTA independiente revisa diagnóstico enviado; observar/rechazar exige motivo; cualquier corrección de enviado crea revisión nueva; PDF válido completa la etapa | Congela lo revisado y evita autoaprobación o sobrescritura de decisiones | DecisionTecnica append-only; EtapaExpediente y revisiones con procedencia; permisos de decisión separados de edición | Falta de segundo usuario habilitado; aprobar una revisión obsoleta; confundir aprobación con autorización comercial | **Sí:** circuito completo de sección 6, incluyendo revisión independiente |
| D03: obligatoriedad y evidencias | Mínimos documentales explícitos al enviar; borrador incompleto permitido; desconocidos declarados, no rellenados; fotografías con clase y leyenda | Separa integridad documental de conclusiones industriales y permite identificar pendientes reales | Formularios/servicios de Inspeccion, Diagnostico y EvidenciaTecnica; validación al envío y bloqueo posterior | Textos vacíos disfrazados de datos, evidencia insuficiente o conclusiones no sustentadas | **Sí:** tablas de sección 4; planta valida los mínimos sin deducir un protocolo |
| D04: fuentes y acceso a biblioteca | Biblioteca privada de empresa; PLANTA publica; técnico accede a referencias publicadas aplicables a sus asignaciones; reutilización documental explícita | Evita ampliar acceso a proyectos, documentos comerciales o archivos históricos por enlazar un plano | ReferenciaTecnica, aplicabilidad a Equipo, resolver de documentos autorizado y guardas extendidas; Documento conserva su proyecto real | Archivos públicos/compartidos incompatibles, referencia mutable, acceso por ID Drive o ruta antigua | **Sí:** alcance de acceso y proceso de incorporación de sección 5 |
| D05: selección desde plano y formatos | Primera entrega: visor autorizado y listado de partes por versión; selección por posición/parte; sin áreas gráficas, conversión CAD u OCR | Implementación trazable con herramientas existentes, sin atribuir compatibilidad mecánica al software | ParteReferenciaTecnica; referencias de página/posición opcionales; no coordenadas obligatorias ni librerías nuevas | Elegir otra versión, prometer render CAD o seleccionar por coincidencia de nombre | **Sí:** aceptar listado como selección inicial y diferir interacción gráfica |
| D06: piezas, cantidades, condición y acción | Biblioteca o entrada manual; descripción, cantidad positiva, condición y acción propuestas por técnico; unidad o pendiente explícito; sin catálogo industrial inventado | Permite trabajar sin número de parte conocido y conserva semántica sin crear inventario | PiezaRequerida con snapshots; Decimal propuesto y validación de sección 7; campos de texto para condición/acción inicialmente | Unidad ambigua, falsa exactitud, confundir cantidades solicitadas con disponibles o aprobadas | **Sí:** representación numérica, tratamiento de pendientes y campos de sección 7 |
| D07: PDF, emisión, retención y destinatarios | Informe inicial interno del diagnóstico aprobado; ReportLab, snapshot/versionado, copia persistente, SHA-256 y auditoría; Drive posterior; sin publicar al cliente ni anexar CAD/PDF arbitrarios | Reutiliza Fase 2 y distingue emisión válida de sincronización remota o permiso de reparación | EmisionInformeTecnico nuevo; Documento existente protegido; permisos de reserva/descarga/reconciliación; plantilla inicial propia | PDF de revisión antigua, fallo de storage, archivos huérfanos, exposición por carpeta o pérdida posterior | **Sí:** circuito/contenido interno de sección 8; plazos de retención y publicación externa requieren decisión separada |

Todas las opciones son propuestas funcionales para aprobar. Las restricciones de aislamiento, validación en servidor, conservación histórica y ausencia de autoaprobación son requisitos ya pedidos por el usuario; su concreción se somete a revisión sin rebajarlos.

## 3. D01: matriz específica de permisos

Leyenda: E = expedientes de la empresa; A = expediente asignado; R = solo revisión del propio autor. Todo acceso exige usuario/perfil/rol/empresa activos y autorización sobre el objeto. No se confía en empresa, autor, estado o aprobación enviados por el navegador.

| Operación propuesta | Técnico TECNICO | Administrador de planta PLANTA | Secretaría OFICINA | Gerencia GERENCIA |
| --- | --- | --- | --- | --- |
| Consultar inspección/diagnóstico y sus evidencias | A | E | E, lectura | E, lectura |
| Consultar informe inicial interno | A | E | E | E |
| Asignar/reasignar técnico | No | Sí, E | No | No |
| Crear/editar/enviar inspección o diagnóstico | A + R | No | No | No |
| Seleccionar piezas y proponer condición/acción | A + R, borrador | No; puede observar | No | No |
| Consultar biblioteca publicada | Aplicable a A | E | E | E |
| Crear/editar borrador de referencia o componente | No | Sí, E | No | No |
| Publicar/retirar referencia, registrar aplicabilidad | No | Sí, E | No | No |
| Aprobar/observar/rechazar diagnóstico | No | Sí, E y sin autoría en fuentes | No | No |
| Solicitar corrección | A | E | E, solicitud documentada | E, solicitud documentada |
| Autorizar nueva revisión de enviado | No | Sí, E, con motivo | No | No |
| Reservar/reintentar informe de diagnóstico aprobado | A | E | E | No |
| Cambiar datos enviados, PDF emitido o borrar historial | No | No | No | No |
| Autorizar/ejecutar reparación mediante Fase 3 | No | No | No | No |

GERENCIA conserva su responsabilidad comercial prevista en la arquitectura, pero esta fase no implementa esa operación. Su lectura de un informe no constituye aprobación del cliente. OFICINA no redacta diagnóstico ni modifica mediciones/causas; pedir generación de PDF no cambia contenido técnico.

ADMIN y CONSULTA: mantener lectura técnica de empresa propuesta en el diseño, sin nuevas operaciones de escritura/decisión; no cambiar sus funciones históricas. CLIENTE: sin nuevos accesos técnicos o de biblioteca; conservar únicamente sus accesos históricos. Superusuario no sustituye asignación ni rol operativo. No convertir ADMIN en PLANTA/GERENCIA ni crear multirrol implícito.

Una sola asignación principal vigente es la propuesta inicial; no impide conservar asignaciones anteriores. Reasignar cierra vigencia con motivo/auditoría; el nuevo técnico no edita borradores ajenos: crea revisión vinculada, manteniendo al autor original y la procedencia. El técnico anterior deja de tener acceso por asignación salvo otro permiso expreso. Las autorías deben seguir protegidas aunque cambien el rol o la asignación.

## 4. D03: campos obligatorios y opcionales

Reglas al **enviar**, no al guardar un borrador. Aun en borrador son obligatorios el expediente autorizado, autor/fecha de servidor, revisión interna y control de concurrencia. Estos datos se fijan en servidor, no como campos libres. No se adopta un checklist mecánico, un resultado de conformidad ni una tolerancia.

### Inspección

| Campo | Obligatorio al enviar | Tratamiento propuesto |
| --- | --- | --- |
| Expediente y recepción confirmada | Sí, servidor | Misma empresa, guía persistente íntegra y técnico asignado |
| Fecha efectiva de inspección | Sí | Capturada por usuario; distinguir de creado_en/enviado_en; correcciones auditadas |
| Estado físico | Sí | Descripción del técnico, sin valores predeterminados |
| Condiciones generales | Sí | Texto registrado por el técnico, sin inferir conformidad |
| Hallazgos y daños | Sí, declaración | Descripción o declaración explícita de que no se identificaron; nunca insertar automáticamente «sin daños» |
| Fotografías y otras evidencias | Declaración obligatoria; sin mínimo numérico impuesto | Seleccionar evidencias o documentar motivo de ausencia; PLANTA puede observar insuficiencia en la revisión. La foto de recepción no sustituye automáticamente evidencia de inspección |
| Clasificación de cada fotografía | Sí, cuando se adjunta | Propuesta ESTADO_FISICO/HALLAZGO/DANO/DETALLE/OTRA; OTRA con descripción. Son categorías documentales, no tipos industriales de falla |
| Leyenda/descripcion de evidencia | Sí, cuando se adjunta | Explicar qué documenta; orden opcional de presentación |
| Observaciones adicionales | No | No duplicar ni usar como sustituto de campos obligatorios |
| Número de parte, mediciones o tolerancias | No como campos obligatorios de inspección | No inventar valores ni exigirlos sin protocolo aprobado |

La alternativa «sin evidencia, con motivo» es una **propuesta que requiere aprobación D03**; no se presenta como política industrial vigente. Si el propietario exige fotografías mínimas de inspección, deberá indicar la regla antes de activarla. No extrapolar las reglas GENERAL/PLACA de recepción a todas las fotos técnicas.

### Diagnóstico

| Campo | Obligatorio al enviar | Tratamiento propuesto |
| --- | --- | --- |
| Inspección fuente enviada y vigente | Sí, servidor | FK exacta del mismo expediente; preservar revisión y evidencias fuente |
| Fecha efectiva y responsable | Sí | Autor técnico asignado y de la revisión; fechas de registro por servidor |
| Fallas identificadas | Sí, declaración | Describir lo identificado o declarar explícitamente que no se ha determinado una falla; no autocompletar |
| Posibles causas | Sí, declaración | Texto con distinción expresa entre hipótesis/causa sustentada/no determinada; no inferir causas desde marca/modelo |
| Componentes afectados | Sí, declaración | Descripción o pendientes/no identificados explícitos; catálogo opcional |
| Recomendaciones | Sí | Propuesta escrita por técnico; puede indicar necesidad de evaluación adicional sin inventar una reparación |
| Conclusiones técnicas | Sí | Alcance y limitaciones; desconocidos identificados. No equivalen a autorización comercial ni a garantía de conformidad |
| Evidencias | Sí, referencias o motivo | Pueden citar inspección/reutilizar Documento autorizado; nuevas fotos opcionales; manifest íntegro y procedencia conservada |
| Declaración sobre repuestos | Sí | Lista completa de requerimientos o declaración explícita de que no se requieren; no deducir «ninguno» de una lista vacía |
| Observaciones adicionales | No | Texto complementario |

Datos técnicos maestros desconocidos, causa no determinada y número de parte pendiente no se convierten en ceros o valores ficticios. Un diagnóstico con limitaciones puede enviarse a revisión; solo PLANTA decide si lo aprueba con esas limitaciones, lo observa o rechaza. Aprobar el contenido registrado no certifica una causa que siga desconocida.

## 5. D04 y D05: acceso, versiones y selección desde planos

1. Biblioteca privada por empresa. PLANTA administra borradores y publica referencias; otros roles leen únicamente lo permitido por D01. Técnico filtra tanto búsquedas/metadatos como descarga por aplicabilidad a equipos de sus expedientes asignados. Referencia BORRADOR no se ofrece para seleccionar piezas; RETIRADA permanece legible cuando se cita históricamente, sin ofrecerla para nuevas selecciones.
2. ReferenciaTecnica mantiene tipo PLANO/MANUAL, código y revisión interna, marca/modelo/ratio disponibles, versión declarada de fabricante, fuente y Documento. La revisión interna es del sistema; no inventa una versión técnica del fabricante. Desconocidos no funcionan como comodines de compatibilidad.
3. AplicabilidadReferenciaEquipo es explícita, autorizada por PLANTA, con fundamento/documento fuente. Buscar por marca/modelo/ratio ayuda a localizar; no valida compatibilidad mecánica ni equivalencia de piezas. Cambio de aplicabilidad no modifica snapshots anteriores.
4. Documento conserva Proyecto obligatorio, archivo y metadatos Drive. Una referencia compartida entre proyectos de **la misma empresa** permite descargar solo su Documento mediante la ruta de biblioteca autorizada; no abre el proyecto fuente ni documentos hermanos. No crear proyecto ficticio ni una carpeta Drive de biblioteca nueva para resolverlo.
5. Antes de registrar/publicar un Documento existente: verificar empresa, titularidad/acceso, categoría, contenido/hash, copia persistente y compatibilidad con sus usos heredados. No endurecer silenciosamente un documento público/compartido ni cambiar sus IDs. Si el propietario de ese uso no autoriza congelarlo, queda excluido de publicación hasta resolver el caso. Esto bloquea ese documento, no toda la biblioteca.
6. Reutilizar el mismo Documento si es autorizado y compatible. Respetar unicidad proyecto/hash; no copiar bytes para vencerla. Un nuevo documento de biblioteca se guarda en su proyecto real autorizado. Archivo existente solo en Drive: candidato pendiente, no fuente persistente confirmada; una importación real requiere autorización específica posterior.
7. Primera selección: abrir versión concreta en visor PDF/imagen y elegir una fila del listado ParteReferenciaTecnica por posición/número/descripción. El servidor revalida empresa, versión publicada, aplicabilidad y permiso antes de guardar PiezaRequerida. Manuales sirven de consulta y fuente; no crear automáticamente un despiece de cualquier manual.
8. DWG/DXF: descarga autorizada del original; sin conversión, extracción, OCR, hotspots ni selección geométrica en esta entrega inicial. Una versión PDF/imagen derivada solo podrá incorporarse con origen y validación explícitos, sin reemplazar el CAD. Página/posición como referencia textual es opcional.

Mantener revisiones publicadas inmutables y partes congeladas con su referencia. Corregir contenido crea otra revisión; retirar cambia disponibilidad de nuevas selecciones, no el contenido técnico. Extender protección a rutas heredadas, Django admin, visor/listados Drive y descargas por ID. El control por carpeta no sustituye la autorización del documento. No ampliar el acceso comercial o público al registrar una referencia.

## 6. D02: estados, revisión y corrección

| Operación | Estado resultante | Requisitos y responsable |
| --- | --- | --- |
| Crear inspección | EN_PROCESO | Recepción confirmada, autor TECNICO asignado; ausencia de fila antes de iniciar = NO_INICIADO |
| Enviar inspección | FINALIZADO | D03 cumplido, revisión/evidencias congeladas. Es fin de captura, no aprobación independiente |
| Crear diagnóstico | EN_PROCESO | Inspección fuente enviada y vigente |
| Enviar diagnóstico | PENDIENTE_APROBACION | D03/D06 completos; congelar texto, hijos y manifest |
| Revisar diagnóstico | APROBADO, OBSERVADO o RECHAZADO | PLANTA independiente decide una solicitud/revisión concreta; motivo obligatorio al observar o rechazar |
| Generar y registrar informe válido de revisión aprobada | FINALIZADO | Copia persistente/hash verificados y revisión aún vigente; ninguna autorización comercial implícita |
| Corregir observado | Nueva revisión EN_PROCESO | Motivo, vínculo a revisión y observación originales; nuevo envío y decisión independiente |
| Reconsiderar rechazado | Nueva revisión EN_PROCESO | PLANTA autoriza reconsideración con motivo; el rechazo original no cambia |
| Corregir enviado/aprobado/finalizado | Nueva revisión EN_PROCESO | Solicitud y autorización PLANTA con motivo; bloquear uso del anterior como vigente, sin alterar su contenido/PDF/decisiones |

Autoaprobación prohibida por identidad de usuario, no solo por rol actual: revisar autores del diagnóstico, inspección y fuentes copiadas dentro de la procedencia. Asignarse otro rol o copiar una revisión no elimina autoría. Si no hay revisor independiente habilitado, permanecer pendiente; no aprobar con cuenta compartida o superusuario. No se introduce edición técnica por PLANTA como atajo.

Cada modificación requiere versión esperada y transacción; los envíos/decisiones/reservas bloquean el expediente con orden de locks consistente. Cambio de inspección obliga a nueva revisión diagnóstica si se quiere usar la nueva fuente. El PDF anterior sigue siendo un documento histórico de su fuente exacta y se señala como sustituido/no vigente mediante estado de etapa y auditoría, sin reescribirlo. No invalidar retrospectivamente una firma/decisión alterando su registro.

Fallo de PDF deja diagnóstico APROBADO con generación pendiente/error, no RECHAZADO ni FINALIZADO. Fallo de Drive con copia local íntegra no revierte la decisión o la emisión local. Ninguna de estas transiciones habilita reparación: debe seguir bloqueada hasta implementar y autorizar el circuito comercial en otra fase.

## 7. D06: registro de repuestos

| Dato / regla | Recomendación concreta |
| --- | --- |
| Origen | BIBLIOTECA exige ParteReferenciaTecnica; MANUAL no exige catálogo y registra motivo/contexto |
| Descripción | Obligatoria al enviar; capturada desde fuente o escrita por técnico |
| Número de parte | Se conserva si está en la fuente; para manual se admite «pendiente de identificación» como estado explícito, sin usar esa frase como número ficticio |
| Cantidad | Decimal positiva, sin default 1. Propuesta de representación informática: max_digits=20 y decimal_places=6; no constituye precisión física ni tolerancia. Rechazar exceso de escala/rango, sin redondeo silencioso; requiere aprobación antes de migrar |
| Unidad | Registrar unidad real de fuente/técnico, sin conversiones automáticas. Si se desconoce, estado PENDIENTE con motivo y revisión PLANTA; no suponer unidad «pieza». PLANTA decide suficiencia del requerimiento para el informe interno, sin autorizar una compra |
| Condición | Texto obligatorio del técnico; puede declarar que no se determinó y explicar la limitación. No sembrar un catálogo industrial no validado |
| Acción recomendada | Texto obligatorio propuesto por técnico; no activar una orden ni asumir reparación/sustitución predeterminada |
| Cantidad de referencia del plano | Opcional y solo desde fabricante; no copiarla automáticamente como cantidad requerida |
| Snapshot | Fuente, referencia/revisión/posición, número/descripcion/unidad conocidos, cantidad, condición/acción y pendientes. La edición futura del catálogo no modifica el diagnóstico enviado |

ComponenteTecnico usa código interno por empresa; no imponer unicidad global del número de parte. ParteReferenciaTecnica conserva la posición y datos de fuente congelados. Seleccionar requiere referencia publicada/aplicable; si no hay parte compatible, usar entrada manual documentada, sin falsificar vínculo al plano. No inferir compatibilidad por número parecido.

La aprobación D06 incluye permitir cantidades fraccionarias representables y pendientes de unidad/número en un **informe interno**. Si planta requiere cantidades enteras o catálogos cerrados por unidad, debe aportar esa regla antes de implementarla; no se puede deducir técnicamente. Una cantidad solicitada no representa existencia, compra, recepción o consumo.

## 8. D07: informe técnico inicial

Recomendación: emitir únicamente desde diagnóstico APROBADO por PLANTA y vigente. El PDF identifica expresamente el alcance de esa aprobación y sus pendientes; incluye «No autoriza reparación ni constituye autorización comercial del cliente». No agregar una segunda aprobación obligatoria del PDF: PLANTA revisa el contenido técnico congelado y la aplicación compone ese contenido con una plantilla aprobada; defectos de presentación se resuelven con nueva emisión y motivo. Esta elección requiere autorización D07 junto con D02.

**Contenido propuesto:** portada MASISCAM, expediente y versión; cliente/equipo desde snapshot confirmado; recepción referenciada; inspección; fallas/causas/componentes; recomendaciones/conclusiones y limitaciones; fotografías clasificadas; listado de piezas; referencias de planos/manuales con versión y posición; autor/revisor/fechas y manifest. Firmas: nombres de responsables y espacios vacíos si se aprueban, sin firma certificada ni imagen de firma simulada. Los detalles técnicos añadidos después de recepción declaran fuente/fecha, sin alterar el snapshot de ingreso.

No anexar automáticamente archivos completos de plano/manual ni PDF/CAD al informe. Incluir referencia y listado, con fotografías autorizadas. Esto resuelve la primera versión sin instalar un motor CAD o herramienta de unión PDF. Anexos gráficos y destinatarios externos son ampliaciones separadas, no dependencias de este informe interno.

**Secuencia de emisión propuesta:**

1. Autorizar operación y lectura, comprobar diagnóstico aprobado/vigente y reservar bajo bloqueo una EmisionInformeTecnico con clave/huella, versión y snapshot/manifest inmutables. Versión documental correlativa por expediente/tipo, distinta de revisión técnica, versión de fabricante y plantilla.
2. Misma intención/revisión devuelve la reserva actual aun con un nonce nuevo; misma clave con contenido distinto produce conflicto. Pedir otra emisión exige intención explícita y motivo; un reintento no incrementa versión ni genera otro Documento.
3. Generar ReportLab desde datos congelados, fuera de llamadas remotas y sin URLs arbitrarias. Validar integridad de fotos y referencias; escapar texto y controlar recursos con límites existentes. Definir presupuestos adicionales de tiempo/tamaño solo tras medición, sin inventar umbrales operativos.
4. Guardar en Documento privado, verificar lectura de vuelta, tamaño y SHA-256; unir Documento a emisión y registrar evento de generación en transacción. No marcar GENERADA ni completar etapa con un archivo temporal o solo con la respuesta del storage.
5. Revalidar la revisión vigente antes de completar etapa. Si fue sustituida durante el render, mantener el resultado como emisión histórica no vigente, sin hacerlo pasar por informe del diagnóstico nuevo ni finalizar esa nueva revisión. No sobrescribir bytes para corregirlo.
6. Sincronizar mediante Celery/servicios existentes después de commit. Reconciliación de emisiones técnicas separada de las guías, con IDs y reintentos existentes; fallo de cola/Drive conserva copia local y estado pendiente recuperable.
7. Nueva revisión o corrección documental genera otra versión; preservar PDF/hash anteriores. La descarga comprueba autorización e integridad, identifica si es histórico/no vigente y no publica el documento por defecto.

Retención recomendada durante QA: no eliminación automática de revisiones, evidencias o PDF. No es un plazo legal de conservación. Antes de producción, propietario debe definir retención, restauración y destinatarios; hasta entonces no habilitar purgas ni publicación externa. En implementación local se verifica respaldo/restauración sintéticos y conservación por PROTECT/guardas.

## 9. Otras precisiones necesarias, sin omitir dependencias

| Asunto | Resolución propuesta / límite | Efecto y aprobación |
| --- | --- | --- |
| Línea base sin PDF original disponible | Aprobar expresamente esta especificación y los requisitos transcritos como alcance local, o aportar el original antes de cerrar diferencias | Propietario; no afirmar cumplimiento de contenido no revisado |
| Reductores y bombas | Narrativa documental común; sin checklist mecánico ni protocolo específico de calibración | Propietario/planta aprueban campos comunes D03. Protocolos particulares quedan fuera; no inventarlos |
| Colaboración/multirrol | Autor único por revisión y técnico principal por expediente; sin acumulación implícita | D01; ampliar requiere otro diseño |
| Catálogos de condición/acción | Texto técnico trazable en la primera versión; catálogos cerrados posteriores solo con fuente aprobada | D06; no bloquea adoptar explícitamente esta alternativa |
| Evidencia de recepción | Referencia de lectura acotada al expediente y permisos técnicos; sin insertar en recepción ya confirmada | Diseño técnico coherente con 0018; alcance de lectura incluido en D01/D04 |
| ArchivoRegistro sin copia local | No usarlo como copia persistente para el nuevo informe; candidato solo con incorporación autorizada a Documento y validación | Bloquea esa fuente, no toda la fase; importación real posterior requiere permiso |
| Datos heredados incompletos | Sin backfill técnico o aprobaciones automáticas; crear etapas/revisiones bajo demanda para recepciones confirmadas | Preservar Fase 2; aceptación de implementación local no autoriza migrar producción |
| Duplicación de eventos | Reutilizar Auditoria para transiciones y claves de idempotencia; DecisionTecnica conserva decisiones por su semántica | No crear EventoTransicion paralelo. Identidades de autores/revisores protegidas y procedencia verificable |
| PDF o evidencia corruptos después de emisión | Bloquear uso/descarga e informar; restaurar bytes originales desde respaldo, conservando hash | No legitimar bytes distintos cambiando hash. Pérdida posterior no se resuelve con un estado GENERADA |
| DB, archivos y cola | Aceptar sus fronteras reales: reserva persistida, reconciliación, detección de huérfanos y recuperación; sin borrado automático | No prometer transacción distribuida ni entrega exactamente una vez; controles técnicos a implementar y probar |
| Revisión de alcance comercial | Diagnóstico/piezas nuevos nunca autorizan reparación. Futura autorización debe referenciar revisión, alcance y respaldo del cliente | Sin modelo/booleano comercial ficticio. Aprobación comercial, compras, reparación y calibración fuera de Fase 3 |
| Publicación/retención/correcciones tras cierre global | Solo correcciones técnicas previas al cierre global según D02; no diseñar ahora reapertura de mantenimiento cerrado | Requieren autorización separada; no bloquean informe interno local bajo estas restricciones |

No se elimina ninguna D01–D07 calificándola como «detalle técnico». Acceso entre proyectos, mínimos de captura, cantidades y revisión independiente deben aprobarse aunque el código para aplicarlos sea sencillo.

## 10. Impacto consolidado y migraciones previstas

Conservar sin cambios de identidad Cliente, Equipo, token/QR, RegistroEquipo, ExpedienteMantenimiento, recepción/confirmación y EmisionGuiaIngreso. Nuevas entidades propuestas: AsignacionTecnica, EtapaExpediente (solo INSPECCION/DIAGNOSTICO), Inspeccion, Diagnostico, EvidenciaTecnica, DecisionTecnica, ReferenciaTecnica, ComponenteTecnico, ParteReferenciaTecnica, AplicabilidadReferenciaEquipo, PiezaRequerida y EmisionInformeTecnico. No crear otro repositorio binario o catálogo de usuarios/equipos.

EtapaExpediente conserva FK explícitas y coherentes a revisión vigente; prohibir GenericForeignKey o IDs JSON sin integridad para ese vínculo. EvidenciaTecnica tiene exactamente un destino inspección/diagnóstico y FK Documento. FK de autoría/negocio con PROTECT; versiones y claves únicas; cantidad > 0; asignación vigente única; guardas de hijos tras envío, documentos, decisiones y emisiones inmutables. Empresa se deriva del expediente salvo biblioteca, con comprobación de coherencia entre tablas.

Futuras migraciones **aditivas posteriores a 0018**, en lotes: biblioteca; trabajo técnico/relaciones; emisiones y guardas ampliadas PostgreSQL/SQLite. Resolver referencias circulares con creación de tablas y FK en operaciones ordenadas, no debilitando validaciones. No modificar 0015–0018 ni el esquema real, crear etapas para todos los históricos o ejecutar señales Drive en migraciones. Reversión operativa futura deshabilita la función conservando datos y protecciones.

La autorización técnica será un servicio propio; no ampliar indiscriminadamente `recepcion_services.autorizar`. Extraer únicamente primitivas documentales con regresión Fase 2. Extender guardas, descargas, visor/listados Drive y rutas antiguas para documentos nuevos antes de permitir carga/publicación; una relación nueva por sí sola no protege el archivo.

## 11. Plan concreto de implementación local, pendiente de autorización

| Etapa | Trabajo previsto | Criterio de salida |
| --- | --- | --- |
| 0. Autorización | Aprobar D01–D07 y sus tablas, identificar responsable funcional y alcance local; registrar versión de política | Autorización expresa de implementación; ninguna propuesta se toma por aprobada por silencio |
| 1. Esquema y fronteras | Preparar migraciones aditivas; permisos técnicos, asignación, guardas y resolución documental | Ensayo desde 0018 con datos sintéticos respaldados; tokens, IDs, hashes, recepción y permisos anteriores intactos |
| 2. Biblioteca | Referencias/partes/aplicabilidad, registro privado autorizado, filtros, visor y selección por listado | Sin acceso cruzado ni cambios silenciosos a documentos existentes; versiones publicadas inmutables |
| 3. Inspección | Formularios responsive, borrador, evidencias, control de edición y envío | D03 y asignación exigidos en servidor; congelación comprobada |
| 4. Diagnóstico y piezas | Causas/limitaciones, piezas manuales o de plano, revisión PLANTA y correcciones | D02/D06, no autoaprobación, procedencia y revisiones previas conservadas; reparación bloqueada |
| 5. Informe inicial | ReportLab, reserva/versionado, copia persistente/hash, auditoría y tareas/reconciliación simuladas | Ningún estado válido sin PDF íntegro; reintentos idempotentes, revisión obsoleta no habilita avance |
| 6. Aceptación integrada | PostgreSQL 16 aislado, SQLite, regresión y revisión visual | Evidencias y resultados verificables, riesgos remanentes identificados; revisión posterior antes de cualquier despliegue |

Pruebas de aceptación mínimas: cada celda permitida/denegada; empresa y cliente ajenos; referencias entre proyectos sin fuga; roles/usuarios desactivados; reasignación/cambio de rol y autorías copiadas; borrador incompleto y cada obligatorio faltante; texto desconocido declarado sin valores falsos; foto inválida/corrupta; piezas manuales/biblioteca y exceso de escala; estado obsoleto; observación/rechazo/corrección; guardas ORM/bulk/SQL/admin/legacy; doble envío/decisión/reserva/generación con conexiones PostgreSQL independientes; storage caído o falso éxito; cambio de revisión durante render; cola/Drive simulados caídos; recuperación sin duplicaciones; PDF largo con fotos/tablas y caracteres españoles; interfaz móvil/tablet/escritorio; QR, históricos y PDF de Fase 2 intactos.

Concurrencia se valida con PostgreSQL 16, no se deduce de SQLite. Ejecución en bases desechables respaldadas y Drive simulado; ningún Compose productivo. La suite histórica de Fase 2 debe repetirse como regresión junto con pruebas nuevas; su resultado previo no sustituye aceptación Fase 3. No fijar de antemano número de pruebas, rendimiento o resultado favorable.

## 12. Decisiones presentadas para autorización

Se recomienda aprobar como conjunto D01–D07 con las opciones descritas: autor único/asignación principal; revisión independiente PLANTA; mínimos narrativos y pendientes explícitos; biblioteca privada con acceso controlado entre proyectos; selección por listado sin CAD interactivo; piezas con cantidad Decimal y condición/acción en texto; informe inicial interno de revisión aprobada, sin publicación externa ni anexos automáticos.

El propietario debe aceptar o ajustar expresamente cada fila, especialmente la ausencia justificada de evidencia técnica, la unidad pendiente, las fracciones de cantidad y el circuito de corrección/revisión. La conformidad técnica de planta con estos campos debe constar antes de programar validaciones industriales. Retención productiva, exposición a clientes, importación Drive real y autorización comercial permanecen fuera de ese conjunto local.

**Estado final:** D01–D07 cuentan con resolución técnica recomendada y trazabilidad de impacto/riesgo; su aprobación funcional y el inicio de implementación siguen pendientes. En esta entrega solo se crea `docs/validacion_fase3.md`. Sin cambios de código, migraciones, bases, dependencias o Drive; sin pruebas ejecutadas, commits, push o despliegue.
