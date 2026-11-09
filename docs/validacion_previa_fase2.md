# Validación previa a Fase 2 de MASISCAM

Fecha: 2026-11-05. Estado: recomendación técnica para aprobación, sin implementación.

## 1. Alcance, evidencia y diferencias respecto a Fase 1

Se revisaron [arquitectura](arquitectura_expedientes.md), [roles](matriz_roles_permisos.md), [flujo](flujo_estados_mantenimiento.md), [diccionario](diccionario_datos.md), [migraciones](plan_migraciones.md) y [riesgos](riesgos_y_validaciones.md). Se contrastaron modelos, permisos, formularios, servicios documentales, manifiestos de dependencias y configuración de construcción/despliegue. Los seis documentos permanecen sin modificaciones.

**Confirmado** significa observado en los archivos del proyecto; no certifica producción. **Propuesto** significa diseño recomendado. **Pendiente** significa decisión aún no aprobada. Una matriz «definitiva propuesta» es una especificación consolidada para aprobación, no autorización para habilitar permisos.

No está disponible el PDF original de requerimientos en las rutas examinadas durante Fase 1. No se ha validado su contenido completo ni el esquema aplicado en producción. Esta revisión no consulta ni modifica bases de datos ni Drive, no ejecuta Django ni instala paquetes. Tampoco arranca Docker: `sistema/deploy/init.sh` ejecuta migraciones al iniciar el servicio de inicialización.

Se conserva la decisión estructural: `ExpedienteMantenimiento` OneToOne con `RegistroEquipo`, cuyo Equipo mantiene QR e historial. Se concreta una alternativa correlativa por empresa a la propuesta inicial `EXP-UUID`; su adopción requiere B02. Se precisa la obligatoriedad de recepción y el enrutamiento documental: el servicio actual de Documento no guarda automáticamente dentro de la carpeta de RegistroEquipo.

## 2. Base confirmada y dependencias reales

| Evidencia local | Confirmado | Implicación |
| --- | --- | --- |
| `sistema/requirements.txt` | Django 5.2.17, Celery 5.5.3, redis 6.4.0, psycopg2-binary 2.9.10, Pillow 12.3.0, qrcode 8.2, google-api-python-client 2.179.0 y google-auth 2.40.3 | Reutilizar ORM, tareas, imágenes, QR y cliente Drive |
| `requirements.txt` y `requirements.lock` | Sin ReportLab ni WeasyPrint declarados; tampoco Pango/pydyf/CFFI/fontTools como dependencias declaradas del proyecto | Ningún motor PDF existente que conservar; instalar uno será un cambio futuro explícito |
| `sistema/requirements-dev.txt` | Playwright es dependencia de desarrollo | No convertir QA en motor PDF de producción por asumir que ya existe |
| `sistema/Dockerfile` | `python:3.12-slim`; instalación desde lock con `--require-hashes`; usuario sin privilegios; sin instalación explícita de paquetes Pango/fuentes del sistema | WeasyPrint necesita ampliar/verificar entorno nativo; cualquier motor requiere lock actualizado y build reproducible |
| `sistema/docker-compose.yml` | PostgreSQL 16, Redis 7; web y worker comparten volúmenes media/staticfiles y montaje de secretos de solo lectura | Hay base para contenido persistente y procesamiento asíncrono; no demuestra backups ni servicios activos |
| `sistema/static/web/fonts/` | Archivos TTF existentes | Verificar licencia, familia y glifos; no asumir que todo archivo sirve para PDF o está registrado en el motor |
| `masiscam/models.py` | Cliente por empresa; Equipo y token; registros múltiples; Documento con FileField; ArchivoRegistro con metadatos Drive; Auditoria | No duplicar catálogos ni archivos |
| `services.py`, `tasks.py`, `registro_archivos.py` | IDs reservados, reintentos; Documento sube desde archivo local; ArchivoRegistro usa bytes de petición | Reutilizar idempotencia; una carga fallida sin bytes no se recupera solo con Celery |
| `registro_orden.py` y migración 0014 | Secuencia depende de fecha y tipo y se renumera | No sirve como número oficial inmutable |
| `access.py`, `usuario_forms.py` | Rol por perfil; ADMIN/TECNICO/CONSULTA/CLIENTE; bypass actual de superusuario; CONSULTA retirado del selector | Nuevos permisos no deben heredar facultades genéricas de aprobar |

La verificación es de dependencias declaradas y receta de build, no de paquetes efectivamente instalados en un contenedor o servidor. No se recomienda una versión PDF exacta hasta resolver compatibilidad, hashes y prueba de maquetación.

## 3. Matriz definitiva propuesta de permisos

OF=OFICINA, TP=TECNICO, AP=PLANTA, GE=GERENCIA, AD=ADMIN, CO=CONSULTA, CL=CLIENTE. Se reutiliza TECNICO y se añaden tres códigos al mismo RolMasiscam, sin migración automática de usuarios. Un rol por perfil/empresa se conserva; acumulación de funciones es decisión B03.

Leyenda: E=ejecutar en empresa autorizada; A=ejecutar solo asignado; V=lectura técnica autorizada; C=decisión comercial; T=decisión técnica; P=solo contenido propio publicado; —=denegado. Todo acceso está condicionado por estado, revisión y pertenencia. La lectura de documentos comerciales es exclusiva de OF/GE/AD, y de CL solo si están expresamente publicados a su cliente; TP/AP/CO no la reciben por lectura técnica.

| Etapa / operación | OF | TP | AP | GE | AD | CO | CL | Aprobación pendiente |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Consulta técnica de expediente | V | A | V | V | V | V | P | B03, B06 |
| Recepción: abrir, completar, adjuntar y emitir guía | E | — | V | V | V | V | P | B03, B04 |
| Asignación/cambio de técnico | — | — | E | — | — | — | — | B03, B04 |
| Inspección: capturar y finalizar | — | A | V | V | V | V | P | B03; protocolo posterior |
| Diagnóstico/piezas: capturar y enviar informe inicial | — | A | V | V | V | V | P | B03; revisión inicial pendiente |
| Biblioteca: publicar versión de plano/partes | — | V | E | V | V | V | — | B03; alcance posterior |
| Comercial: cargar respaldo documental | E | — | — | E | V | — | P | B03; política posterior |
| Comercial: aprobar/observar/rechazar alcance | — | — | — | C | — | — | — | B03; política posterior |
| Repuestos: registrar recepción y evidencias | E | A | V | V | V | V | P | B03; política posterior |
| Reparación/montaje: registrar ejecución | — | A | V | V | V | V | P | B03; alcance posterior |
| Calibración: registrar y enviar revisión | — | A | V | V | V | V | P | B03; protocolo posterior |
| Aprobación técnica: aprobar/observar/rechazar | — | — | T | — | — | — | — | B03; protocolo posterior |
| Informe final: preparar borrador | E | A | E | V | V | V | — | B03; formato posterior |
| Informe final: autorizar contenido/emisión | — | — | T | — | — | — | — | B03; firma posterior |
| Publicar documento emitido al cliente | E | — | — | E | — | — | — | B06 |
| Cierre: verificar requisitos y cerrar | — | — | V | E | V | V | — | B03; cierre posterior |
| Solicitar corrección/adenda posterior | E | A | E | E | — | — | — | B03, B06 |
| Autorizar adenda técnica | — | — | T | — | — | — | — | B06 |
| Autorizar reapertura/cambio comercial | — | — | T* | C* | — | — | — | B06 |
| Eliminar expediente o evidencia protegida | — | — | — | — | — | — | — | Invariante de conservación |
| Administrar usuarios/roles heredados | — | — | — | — | E | — | — | B03 |

T*/C*: reapertura exige ambas autorizaciones independientes. El flujo de recepción de Fase 2 no habilita estas operaciones posteriores. Rechazo comercial no cierra como mantenimiento completado: la devolución/anulación requiere política futura. «Enviar informe inicial» no concede aprobación técnica: si planta exige revisión inicial, debe añadirse expresamente antes de implementar diagnóstico.

Reglas obligatorias del diseño:

1. En servidor: usuario, perfil, rol y empresa activos; expediente/equipo/cliente y cada documento pertenecen al ámbito autorizado. TP asignado. IDs enviados por formulario no otorgan acceso.
2. CLIENTE debe ser cliente contractual y dueño actualmente autorizado del equipo. Ante transferencia, denegar el historial por defecto hasta decisión explícita; no divulgarlo al nuevo dueño automáticamente.
3. AD conserva permisos actuales sobre módulos heredados, pero no recibe C/T en expedientes. Tampoco superusuario firma decisiones por su bypass técnico. No modificar la semántica general de permisos heredados para conseguir esta separación: nueva comprobación específica.
4. Autor de diagnóstico/reparación/calibración no aprueba sus propios trabajos; revisar todas las autorías relevantes. Registrar aprobación sobre revisión y respaldo concretos. Cliente no aprueba digitalmente en esta fase; GE registra su autorización documental, sin sustituirla.
5. Solo cambios POST protegidos por CSRF; sin mutaciones por GET. Administrador de usuarios podría reasignarse un rol: auditar y definir control de autoasignación de PLANTA/GERENCIA antes de habilitar decisiones (B03). No afirmar separación efectiva si ese camino queda libre.
6. Los roles nuevos deben seguir funcionando en páginas reutilizadas. Ampliar permisos de lectura y uso de catálogos de forma explícita, sin otorgar `archivar`, `reemplazar`, `usuarios` o `visibilidad` por conveniencia. Revisar selector de rol y limpieza de cliente para los nuevos códigos.

## 4. Numeración por empresa: propuesta concurrente e inmutable

**Recomendación propuesta:** correlativo continuo por empresa sin reinicio anual. Número visible `EXP-E<empresa_id>-<correlativo con mínimo 8 dígitos>`. Ejemplo ilustrativo: `EXP-E12-00000001`. No es un identificador comercial aprobado. El prefijo usa PK estable, no razón social ni RUC editables. Empresa y número se congelan; ancho mínimo no es límite ni causa truncamiento.

Datos nuevos mínimos:

- `ContadorExpediente`: OneToOne Empresa PROTECT, `ultimo_numero` BigInteger no negativo. Tabla de infraestructura, no catálogo duplicado.
- Expediente: `empresa` FK PROTECT, `correlativo` BigInteger positivo, `numero` único global, `registro` OneToOne PROTECT. UNIQUE(empresa, correlativo), UNIQUE(numero). No FK Equipo redundante. Empresa se obtiene y fija del registro al abrir.
- Guardas PostgreSQL futuras: rechazar cambio de número/correlativo/empresa/registro/cliente contractual y borrar expediente; validar consistencia del número. No confiar exclusivamente en editable=False o Model.save frente a bulk/SQL.

Algoritmo futuro dentro de una transacción corta:

1. Validar permiso y empresa. Bloquear fila Empresa correspondiente y RegistroEquipo en orden estable. Si se usa una ruta heredada con bloqueo de la primera Empresa, respetar ese bloqueo global antes de los nuevos locks; ensayar orden de todos los caminos para evitar deadlocks.
2. Resolver idempotencia por `(equipo, clave_creacion)` actual. Si el registro ya tiene expediente, devolverlo sin consumir número; misma clave con payload incompatible devuelve conflicto. No convertir arbitrariamente registros ajenos o de tipo no autorizado.
3. Obtener/crear contador bajo el bloqueo de Empresa, protegido por OneToOne; bloquearlo. Incrementar y construir número en servidor. Crear expediente, recepción inicial, evento y auditoría en la misma transacción. Rollback revierte también el contador; no usar MAX+1 ni contador en memoria/Redis.
4. Confirmar transacción; solo entonces generar documentos/encolar. Error PDF/Drive conserva expediente y número; reintento no asigna otro número. Restricciones son última barrera; colisiones inesperadas se investigan, no se corrigen renumerando históricos.

No prometer numeración legal sin huecos: anulación conserva número, backups inconsistentes pueden producir conflictos y no se reciclan números. No bajar contador tras borrados o restauraciones parciales. No se usa como mecanismo de autorización aunque sea predecible. Fecha retroactiva y cambios en RegistroEquipo.secuencia no alteran el número.

La propuesta UUID de arquitectura/diccionario anteriores permanece como antecedente. Si se aprueba esta variante, esos textos deberán alinearse en una tarea documental posterior; **todavía no se han modificado ni existe una numeración implementada**. Un formato anual/código comercial exigiría nueva decisión sobre reinicio, zona horaria y administración de prefijos, fuera de la recomendación mínima.

## 5. Recepción: campos y validaciones propuestos

Se distingue abrir un borrador de finalizar recepción/emitir guía. O=obligatorio; C=condicional; N=opcional; S=asignado por servidor. La obligatoriedad nueva requiere B04 y no se presenta como requisito industrial confirmado.

| Campo / origen reutilizado | Borrador | Finalizar / guía | Regla propuesta |
| --- | --- | --- | --- |
| Empresa, autor, timestamp creación, estado/revisión | S | S | No elegir empresa arbitraria en POST |
| Registro y equipo | O | O | Registro nuevo MANTENIMIENTO/REPARACION o vínculo expresamente validado; mismo equipo permanente |
| Cliente contractual y RUC | O | O | Seleccionar Cliente existente por RUC dentro de empresa; coincidir con Equipo.cliente; sin copiar catálogo |
| Número/correlativo | S | S | Asignar una vez al crear expediente, no al generar PDF |
| Tipo de intervención y fecha RegistroEquipo | O | O | Tipos autorizados; fecha coherente con recepción; no modificar registros antiguos para completar datos |
| Fecha/hora real de recepción | N | O | Zona configurada; distinta de creado_en; oficina valida fechas aportadas |
| Responsable oficina / recibido_por | S | O | Usuario activo OFICINA; conservar autor histórico al desactivarlo |
| Motivo de ingreso/servicio solicitado | N | O | Texto del solicitante, sin diagnóstico ni autorización comercial implícita |
| Condición visible de ingreso | N | O | Descripción observada, sin criterios técnicos inventados |
| Accesorios recibidos | N | O | Permitir declaración explícita «sin accesorios»; no confundir vacío con ninguno |
| Entregado_por_nombre | N | N | Recomendado para trazabilidad, obligatoriedad pendiente de oficina |
| Identificación personal de quien entrega | N | N | No recopilar por defecto sin necesidad aprobada |
| Guía remisión número/fecha | N | C | Si se declara que existe guía, ambos requeridos; contenido real, no generar guía de remisión ficticia |
| Documento guía remisión | N | C | Propuesta exigir copia cuando se declara guía; reutilizar Documento/Evidencia, fuente disponible y validada |
| Motivo sin guía | N | C | Requerido si se aprueba permitir recepción sin guía; aceptación de esta vía pendiente B04 |
| Técnico responsable | N | N* | PLANTA asigna antes de inspección; *si operación lo exige al ingreso, elevar a O mediante B04 |
| Administrador planta / responsable comercial | N | N | Requeridos antes de sus decisiones posteriores, no para simular recepción completa |
| Fotografías equipo/placa y otras evidencias | N | N | Reutilizar archivos; obligatoriedad y cantidad pendientes; no inventar cuota mínima |
| Observaciones adicionales | N | N | Texto escapado; no HTML libre |
| Snapshot identificación, documentos usados y versión | S | S | Congelar cliente/equipo/referencias al finalizar para que ediciones futuras no reescriban guía |
| Emisión guía ingreso y su hash | — | S/O | EmisionInforme + Documento persistido; finalizar al cumplir disponibilidad acordada B06 |

**Datos técnicos confirmados en la ficha actual:** FichaEquipoForm exige cliente para altas nuevas, camaronera, estación, sector, número de equipo, marca, modelo, serie y potencia. RUC/razón social se consultan desde Cliente; ratio, SF, RPM, rotación, aceite y fotos son opcionales del formulario. Reutilizar esa validación al crear equipo; no hacer esos campos duplicados editables en RecepcionEquipo.

Para un equipo existente con datos heredados incompletos, mostrar faltantes y bloquear finalización hasta regularización autorizada o excepción de recepción aprobada; no aplicar validaciones nuevas masivas ni inventar valores. Definir B04 para equipos que llegan a taller sin camaronera/estación/sector o sin placa: la ficha actual puede no adaptarse a ellos. No crear «camaronera desconocida» ni cambiar modelos para ocultar esa incompatibilidad.

Recepción finalizada queda congelada. Corrección mediante nueva revisión/adenda y nueva guía, conservando anterior. En Fase 2 no habilitar reapertura compleja: si no se implementa su autorización, denegar edición y registrar solicitud pendiente.

## 6. Conservación histórica y archivos

### 6.1 Protección estructural

Expediente.registro OneToOne PROTECT bloquea borrado ORM del registro y sus padres cuando llegan a ese registro. Toda evidencia y emisión usada referencia Documento/ArchivoRegistro con PROTECT. Proteger empresa/cliente y autorías relevantes; desactivar usuarios, no borrar su identidad. Comprobar cascadas completas desde Empresa/Proyecto/Equipo y auditoría; un FK de protección nuevo no protege archivos aún no referenciados.

No eliminación física de expedientes, incluso borradores numerados; anulación auditada. Cerrado, emitido o enviado a revisión implica contenido inmutable; nuevas versiones, nunca reemplazo. El servicio debe bloquear también edición de fecha/tipo/equipo del RegistroEquipo asociado si afecta contenido congelado o carpeta; conservar la libertad heredada en registros sin expediente conforme a pruebas.

Auditar y cubrir admin, acciones masivas, importadores, comandos, endpoints de reemplazo/archivado y rutas públicas/legacy. `registro_eliminar` actual llama a borrado permanente de Drive: comprobar protección ANTES de toda llamada remota, no confiar en un rollback posterior. `PROTECT` es comportamiento del ORM; FK/guardas PostgreSQL y permisos efectivos completan protección frente a SQL. Aplicación sin DDL/TRUNCATE, esquema administrado con credencial distinta. La receta actual de Compose debe auditarse: no demuestra separación de credenciales/propiedad DB.

### 6.2 Contenido y rutas

Preservar PK, tokens QR, nombres originales, hashes, rutas e IDs Drive existentes. No reorganizar ni copiar históricos como parte de migraciones. EvidenciaExpediente vincula archivos; no duplicar blobs para vencer la deduplicación de Documento por proyecto/hash.

**Precisión de integración:** hoy `GoogleDriveService.subir_documento` enruta Documento de equipo a carpetas de categoría bajo Equipo, mientras ArchivoRegistro usa carpeta del registro. Recomendación de Fase 2: conservar ese enrutamiento de Documento para guías, con relación lógica explícita al expediente y acceso protegido; no moverlos al registro ni hacerlos pasar por ArchivoRegistro para lograr asociación. Si después se requiere carpeta física por expediente, será extensión de servicio separada, probada y aprobada. No asumir que agregar FK cambia el destino remoto.

Guías/PDF nuevos: persistir bytes con ruta única, calcular SHA-256, registrar emisión/manifest y reservar ID remoto reutilizando servicios. Carga asíncrona posterior a commit. Generación/sincronización separadas del estado técnico. El volumen media compartido existe en Compose; su persistencia no equivale a respaldo.

ArchivoRegistro heredado sin bytes locales no se puede retransmitir solo con metadatos; mantener reselección cuando haga falta. Para evidencias nuevas esenciales, B06 debe decidir copia persistente o Drive confirmado con respaldo independiente. No declarar recepción completa mientras falten evidencias obligatorias o emisión verificable.

Respaldo futuro: inventario DB+media+Drive (ID, hash, pertenencia, tamaño y versión), política de retención sin plazo inventado, restauración ensayada y detección de archivos ausentes/alterados. No purgar huérfanos automáticamente; conciliarlos con eventos de operaciones incompletas. Una restauración parcial necesita reconciliar contador y números ya emitidos. Acceso del administrador externo de Drive no se elimina con controles Django; acordar retención/permisos con responsable.

## 7. WeasyPrint frente a ReportLab

Fuentes oficiales consultadas el 2026-11-05; se describen capacidades, no una versión ya instalada. No se hicieron benchmarks ni pruebas de renderizado en esta revisión.

| Criterio | WeasyPrint | ReportLab Toolkit / PLATYPUS |
| --- | --- | --- |
| Enfoque | HTML/CSS a PDF; facilita plantillas Django | Composición Python con párrafos, imágenes, tablas y plantillas de página |
| Fotografías | CSS para tamaño y distribución; resolver recursos de forma controlada | Flowables Image, escalado/aspecto explícitos; validar archivos con Pillow |
| Tablas extensas | Tabla HTML, reglas de impresión y saltos; probar filas grandes | Table/LongTable, partición y repeatRows; evitar filas que no caben |
| Firmas visibles | Imagen y bloque de identidad/fecha | Imagen o dibujo y bloque de identidad/fecha |
| Paginación | @page, márgenes, encabezados/pies y contadores; comprobar limitaciones | PageTemplate/callbacks/Canvas; «página X de Y» exige estrategia de dos pasadas o equivalente |
| Integración actual | Reutiliza lógica/contexto Django y parte del marcado; CSS dedicado a impresión | Reutiliza datos, logos, fotos y fuentes; no renderiza templates HTML actuales |
| Complejidad operativa | Pango y dependencias de sistema adicionales; fuente y versión afectan layout | Núcleo moderno en Python; extras de aceleración/gráficos dependen del uso; no requiere Pango para el caso básico |
| Mantenimiento visual | CSS suele facilitar cambios de presentación | Estilos y distribución programados; más trabajo manual para diseños variables |
| Riesgos de recursos | Fetcher puede acceder a red/archivos; requiere allowlist, límites y aislamiento | Controlar rutas/recursos suministrados y escapar marcado de Paragraph; límites de imágenes y procesamiento también necesarios |

Las firmas de la tabla son representaciones visibles. No resuelven validación criptográfica, certificados ni valor jurídico: si se exige firma digital, evaluar una etapa/herramienta específica. Tampoco asumir anexado de PDF arbitrario ni CAD como función automática de ninguno; decidir composición de anexos aparte.

Dependencias: WeasyPrint documenta Python y Pango más paquetes Python; Pillow actual es reutilizable pero no satisface todo el entorno. ReportLab documenta núcleo Python desde 4.x y extras opcionales; verificar dependencias transitivas de la versión elegida y no instalar extras innecesarios. Ambos exigen actualizar requirements.txt y regenerar requirements.lock con hashes compatibles con Python 3.12/Linux, y pruebas de fuentes y glifos en la imagen real. No se modifica ninguno en esta entrega.

**Recomendación técnica para el alcance mínimo:** ReportLab con PLATYPUS, condicionado a B05 y muestra aprobada. Motivo inferido del proyecto: una guía de ingreso de estructura controlada, Pillow existente y Docker slim sin Pango explícito favorecen menor cambio operativo. No se afirma mejor rendimiento ni menor dependencia total sin resolver la versión. No basar informes de varias páginas solo en coordenadas Canvas.

WeasyPrint es una alternativa razonable si se prioriza mantenimiento de informes largos en HTML/CSS y se aprueba incorporar Pango/fuentes y aislar recursos. No reutilizar Bootstrap completo ni navegar una URL autenticada para generar: renderizar template dedicado desde snapshot y recursos permitidos. Actualizaciones del motor requieren regresión visual. Si la prueba de guía/informe representativo favorece claramente CSS, reevaluar antes de fijar el contrato de renderizado; el servicio de emisión debe ser independiente del motor y no incluir ambos sin necesidad.

Fuentes primarias:

- [WeasyPrint: instalación y seguridad](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).
- [WeasyPrint: API, paginación y estabilidad de renderizado](https://doc.courtbouillon.org/weasyprint/stable/api_reference.html).
- [ReportLab: instalación y extras](https://docs.reportlab.com/install/open_source_installation/).
- [ReportLab: composición PLATYPUS](https://docs.reportlab.com/reportlab/userguide/ch5_platypus/).
- [ReportLab: tablas y partición](https://docs.reportlab.com/reportlab/userguide/ch7_tables/).

## 8. Decisiones bloqueantes y responsables propuestos

No hay aprobaciones registradas por esta entrega. Códigos B consolidan pendientes P de Fase 1 sin declararlos resueltos.

| ID | Decisión pendiente / propuesta concreta | Responsable | Bloquea |
| --- | --- | --- | --- |
| B01 (P01) | PDF original o transcripción aceptada como línea base, con alcance Fase 2 = recepción+guía+protecciones | Dueño del producto | Inicio de implementación funcional |
| B02 (P02) | Aprobar correlativo por empresa sin reinicio, formato EXP-Eid-secuencia, sin reciclaje ni promesa legal sin huecos; tipos iniciales MANTENIMIENTO/REPARACION | Producto / oficina | Esquema y apertura de expedientes |
| B03 (P03/P12) | Aprobar matriz, usuarios operativos, rol único, control de autoasignación y ausencia de aprobación implícita ADMIN/superuser | Gerencia / planta | Implementación de autorización; decisiones posteriores siguen deshabilitadas |
| B04 (P04/P02) | Aprobar campos, tratamiento sin guía, copia documental, técnico al ingreso y equipos sin datos de camaronera/placa; muestra de guía | Oficina / planta / producto | Formulario y reglas de finalización |
| B05 (P10) | Aprobar elección PDF, fuentes/licencias, encabezado, firma visible o no y muestra representativa | Responsable técnico / producto | Incorporación de dependencia y aceptación de guía |
| B06 (P09/P10/P11) | Copias/retención/restauración, disponibilidad requerida al finalizar, publicación, correcciones y congelación; propuesta inicial guía local+Drive confirmado y sin edición posterior libre | Gerencia / responsable técnico | Protección y emisión utilizable; no fijar plazo de retención sin decisión |
| B07 (P05) | Verificar esquema/grafo aplicado, credenciales, respaldo restaurable y entorno de pruebas PostgreSQL | Responsable técnico | Ejecutar migraciones futuras/habilitar producción; no bloquea diseño aislado aprobado |
| B08 | Autorización expresa para iniciar Fase 2 | Usuario / dueño del producto | Todo trabajo de Fase 2 |

No bloquean recepción si quedan fuera del alcance y deshabilitados: tolerancias/fórmulas y significado de las 16 mediciones, protocolos de bombas, lista de partes, firmas digitales, decisiones comerciales y cierre integral. Sí bloquean sus respectivas fases futuras. Un rechazo o falta de aprobación no se sustituye por valores predeterminados.

## 9. Plan concreto futuro de Fase 2

### 9.1 Preparación y migraciones aditivas

1. Resolver B01–B06 y B08 para el alcance de trabajo; B07 antes de aplicar cambios al entorno objetivo. Registrar decisiones y alinear los seis diseños donde corresponda. Inventario de solo lectura y copia anonimizada con restauración ensayada; no usar producción para tests.
2. Confirmar grafo: código actual llega a 0014 con merges previos. Reservar nombres nuevos después de comprobar la rama real; no editar migraciones existentes.
3. Migración aditiva A: ContadorExpediente, ExpedienteMantenimiento, RecepcionEquipo, EtapaExpediente y EventoTransicion; UNIQUE/CHECK/PROTECT e índices por empresa/situación y registro. No backfill automático de registros ni creación de carpetas. Inicializar contador de manera segura en primera apertura o mediante datos locales controlados, sin APIs.
4. Migración aditiva B: EmisionInforme, EvidenciaExpediente mínima para recepción; enlaces opcionales en Documento/Auditoria, tipo documental y clasificación privados por defecto. Ampliar choices de rol conservando códigos/usuarios actuales. Ningún NOT NULL nuevo obliga a transformar documentos heredados.
5. Migración C de protección PostgreSQL: guardas de identidad, no eliminación y congelación de recepción/emisiones/evidencias; revisar padres, niños y auditoría. Es cambio aditivo de integridad, no limpieza de históricos. Diseñar restricciones específicas, probar con credencial de aplicación y separar permisos de esquema. Las guardas no deben impedir guardar metadatos de sincronización permitidos ni reemplazar el contenido congelado.
6. Incorporar únicamente motor PDF aprobado en requirements/lock; si WeasyPrint, revisar dependencias nativas/fuentes del Dockerfile. No instalar antes de B05. Ninguna migración genera PDF, llama Drive o encola tareas.

### 9.2 Servicios y pantallas mínimas

Abrir expediente mediante servicio transaccional con idempotencia y contador. Seleccionar Cliente existente por RUC, reutilizar Equipo/RegistroEquipo, incorporar recepción y clasificación de evidencias. Agregar permisos explícitos y protecciones en todos los puntos heredados que afecten registros protegidos; las rutas de los otros módulos permanecen funcionales. Habilitar por empresa solo después de guardas y pruebas.

Finalizar: validar datos → congelar revisión → reservar emisión/versionado → generar fuera de locks → persistir Documento/hash → sincronizar mediante servicios existentes → confirmar revisión/archivo/disponibilidad → emitir guía → recepción FINALIZADO. Si el worker falla, mantener estado de generación/error y permitir reintento idempotente sin nuevo número ni pérdida del borrador. Separar finalización de recepción del cierre general del expediente.

Persistir trabajo pendiente y ofrecer reconciliación: un on_commit que no llega al broker no garantiza entrega. Elegir mecanismo mínimo probado (recuperación desde estados persistidos mediante comando/tarea periódica o outbox si se requiere); no suponer un scheduler ya desplegado. No generar dentro de la transacción del contador ni realizar llamadas Drive bajo nuevos locks largos.

Fase 2 termina con guía y expediente abierto; no habilita inspección/diagnóstico comercial/reparación/calibración/aprobaciones finales/cierre. Las etapas futuras pueden existir como NO_INICIADO, sin endpoints que permitan saltarlas. No convertir el botón «recepción finalizada» en «mantenimiento finalizado».

### 9.3 Pruebas y criterios de aceptación

| Prueba futura | Criterio verificable de aceptación |
| --- | --- |
| Concurrencia PostgreSQL | Al menos dos conexiones con transacciones reales abren expedientes simultáneos en una empresa: correlativos distintos; en empresas distintas contadores independientes. No validar locks solo con SQLite |
| Doble envío y fallo | Misma clave devuelve un expediente/número; payload incompatible devuelve conflicto. Rollback no deja media intervención; fallo PDF/Drive conserva número y reintenta misma emisión |
| Inmutabilidad | Editar fecha/nombre/cliente actual no renumera expediente ni altera PDF emitido; UPDATE/bulk/SQL de identidad y eliminación bloqueados bajo credencial de aplicación |
| Identidad histórica | Antes/después sin cambio de PK, tokens QR, archivos, hashes e IDs Drive existentes; equipo permite varios mantenimientos independientes |
| Permisos y aislamiento | Cada celda denegada de recepción probada con cero escrituras/efectos remotos; empresa/cliente ajenos, inactivos, CLIENTE, CONSULTA y superuser sin rol operativo no eluden reglas |
| Roles nuevos en catálogos | OFICINA puede buscar cliente/equipo y operar recepción; PLANTA asigna técnico; no reciben borrado/reemplazo/gestión de usuarios por permisos excesivos |
| Datos de recepción | Borrador incompleto válido; finalizar rechaza cada obligatorio faltante y guía/ausencia incoherentes; equipos heredados incompletos siguen política B04 sin valores ficticios |
| Conservación | Borrado por padre/registro/archivo, admin, endpoint legacy, importación o acción masiva no destruye evidencia protegida ni llama a Drive; pruebas con mocks registran ausencia de llamada |
| Emisión | Guía usa snapshot y número correctos; nueva corrección crea versión, conserva anterior y permisos; lectura no crea ni mueve carpetas |
| PDF y recursos | Fotos vertical/horizontal, textos largos, caracteres españoles, tabla de varias páginas y firmas visibles si se aprueban; sin cortes/superposición, encabezados y numeración correctos; inspección visual del resultado y datos extraídos |
| Recuperación | Caída de worker/broker, archivo local ausente y respuesta Drive perdida: error explícito, recuperación documentada, sin duplicados ni falsa disponibilidad; no publicar archivo de otra empresa |
| Migración y regresión | Esquema anterior con datos heredados migra aditivamente en entorno aislado sin APIs ni señales remotas; suites accounts/masiscam y QR/rutas legacy pasan; inventario solo cambia por nuevas estructuras esperadas |

No fijar presupuestos de tiempo/memoria sin medición; obtenerlos en la muestra representativa y aprobarlos antes de producción. Aceptación final requiere evidencia de pruebas, muestra de guía aceptada por oficina, restauración demostrada y aprobación de responsable. Rollback operativo deshabilita nuevas altas y mantiene guardas; no desplegar código antiguo que ignore expedientes ni revertir tablas destruyendo datos.

## 10. Dictamen

**Recomendación: ajustar y aprobar el diseño antes de implementar.** Reutilizar los modelos/servicios existentes, complementar RegistroEquipo con expediente y contador por empresa y adoptar provisionalmente ReportLab para la guía, sujeto a B05. No duplicar Cliente, Equipo, QR, almacenamiento o usuarios.

Esta propuesta concreta las decisiones, pero no las aprueba. Fase 2 no ha comenzado. Está preparada para autorización condicionada a B01–B06/B08 y verificación B07 antes de migrar/desplegar. La calibración y las decisiones comerciales continúan fuera del alcance mínimo hasta recibir sus definiciones verificables.
