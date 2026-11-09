# Fase 6 — Informe final, cierre y seguimiento

Fecha: 2026-11-06, America/Bogota. Alcance exclusivamente local/QA. **Fase 6 técnicamente completada y verificada, lista para revisión previa al despliegue. Sistema integral sin autorización de producción y operativamente bloqueado por aprobaciones pendientes.**

## Fuentes y decisiones conservadas

Se revisaron el adjunto «MASISCAM — FASE 6: INFORME FINAL, CIERRE Y DASHBOARD», arquitectura de expedientes, flujo, matriz de roles e implementaciones de Fases 3, 4 y 5, además de modelos, servicios, rutas, almacenamiento, permisos y migraciones hasta 0026. El adjunto autoriza desarrollar componentes técnicamente independientes y exige conservar los bloqueos pendientes.

`MASISCAM_POLITICA_COMERCIAL_LOCAL = None` y `MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL = []` permanecen intactos. No se instalan procedimientos, tolerancias, fórmulas, unidades, aprobaciones del propietario ni excepciones industriales. Subir un manual, aprobar software o generar un PDF no autoriza una reparación ni un protocolo.

La propuesta inicial de Fase 1 mencionaba Drive confirmado al cerrar; las fases autorizadas posteriores establecen copia persistente verificada y sincronización posterior. Se aplica ese contrato existente: Drive/broker indisponibles no sustituyen la copia local ni borran un documento; tampoco autorizan un cierre sin PDF íntegro. La política de respaldo, retención y almacenamiento de producción requiere aprobación antes del despliegue.

## Componentes implementados

Tres entidades aditivas, con FK PROTECT:

| Modelo | Función |
| --- | --- |
| InformeFinal | Expediente y calibración exactos, versión/sustitución/motivo, snapshot y huella, autor, clave UUID, estado, Documento privado, SHA-256, envío y error recuperable |
| RevisionInformeFinal | Decisión APROBADO/OBSERVADO de PLANTA sobre una emisión exacta, responsable, motivo, instante y clave; inmutable |
| CierreExpediente | OneToOne al expediente y al informe, responsable GERENCIA, motivo, instante, huella y clave; inmutable |

No se modifica la identidad inmutable de ExpedienteMantenimiento. La existencia de CierreExpediente expresa el cierre, sin introducir otro estado global editable o copiar Cliente/Equipo/RegistroEquipo. El mantenimiento siguiente se abre como otra intervención del mismo equipo y QR.

Se reutilizan recepción confirmada, revisiones técnicas, diagnóstico y piezas, autorización comercial, recepción/uso de repuestos, actividades/resultados/revisión de reparación, calibración/plantilla/16 mediciones y sus aprobaciones, Documento, Auditoria, ReportLab y Drive/Celery.

## Dependencias y flujo

BORRADOR → PENDIENTE_REVISION → OBSERVADO / APROBADO → FINALIZADO.

1. OFICINA o TECNICO asignado reserva una versión con datos congelados. La misma clave/contenido recupera el mismo resultado; una clave incompatible se rechaza. Otra versión requiere intención explícita y motivo, conserva la anterior y la sustituye para el avance vigente.
2. El worker verifica las evidencias, renderiza fuera de transacción y vuelve a bloquear expediente/emisión antes de asociar el Documento. Revalida fuente y huella; una revisión sustituida durante render no se publica como vigente.
3. Solo tras persistir y leer de vuelta el PDF con tamaño/hash válidos pasa a PENDIENTE_REVISION. Un archivo temporal o un falso éxito del almacenamiento no basta.
4. PLANTA aprueba u observa la emisión exacta con motivo. No puede revisar si fue autor del informe, inspección, diagnóstico, reparación o calibración del expediente, incluidas versiones anteriores y cambios de rol. Una observación se conserva; la corrección crea otra versión.
5. GERENCIA solicita cierre bajo bloqueo transaccional. Debe ser independiente del trabajo y de la revisión final. Se verifican de nuevo todas las dependencias y el PDF; se insertan cierre/auditoría y se finaliza la emisión en una sola transacción.

El cierre exige recepción confirmada y guía íntegra, autorización comercial actual con respaldo e informe inicial íntegros, diagnóstico aprobado vigente, reparación finalizada/revisada para esa autorización, cobertura de los repuestos del diagnóstico exacto, calibración actual aprobada con plantilla exacta validada/aplicable y 16 valores, informe final actual íntegro y revisión independiente favorable.

No hay autorización para declarar una calibración/comercial «no aplicable» por conveniencia. Mientras falte un procedimiento aprobado de no aplicabilidad/excepción, el servicio bloquea ese caso; no inventa etapas o conformidad. Una aprobación final no sustituye las previas. Retirar política/protocolo o cambiar las fuentes impide revisar/cerrar versiones dependientes; el historial no se borra.

## PDF y decisiones verificables

`final-v1`, A4, ReportLab existente: portada MASISCAM, expediente/versión, cliente y equipo/características disponibles, recepción y excepciones documentadas, fotografías, inspección, diagnóstico/conclusiones, planos referenciados y piezas requeridas, repuestos recibidos/procedencia/guías, actividades/montaje/resultados/usos, fotografías de intervención, calibración/parámetros/16 valores y referencias/tolerancias de la plantilla aprobada, criterios y evaluaciones humanas, responsables/fechas/aprobaciones de las fuentes y manifiesto documental con hashes.

Ausencias se muestran como no documentadas/pendientes o explícitamente sin registros; «sin repuestos requeridos» procede del diagnóstico aprobado. No se rellenan valores, se certifican instrumentos ni se calculan tolerancias/conformidad. Textos escapados; tablas paginadas con encabezados repetidos; fotografías con leyenda. No hay firmas certificadas ni aceptación simulada del cliente.

El PDF se emite **antes** de su revisión final y cierre. No se reescriben sus bytes para fingir que ya contenían decisiones posteriores: RevisionInformeFinal y CierreExpediente identifican esa versión/hash y se muestran en el expediente. El PDF indica dónde verificar esas decisiones. La fecha incluida es finalización técnica registrada; el instante administrativo real está en el cierre inmutable.

Documento permanece privado, versionado, con SHA-256, snapshot y copia persistente. Drive se encola después del commit con el adaptador existente. `reintentar_informes_finales` lista reservas por defecto; `--encolar` explícito requiere habilitación local. Errores seguros conservan reserva; corrupción exige restaurar los bytes originales, sin cambiar el hash para aceptarla.

## Permisos, aislamiento y congelación

| Rol | Facultades de Fase 6 |
| --- | --- |
| OFICINA | Consulta y prepara/reintenta informe desde fuentes aprobadas; sin decisión técnica ni cierre |
| TECNICO | Igual preparación solo en expediente asignado; sin aprobar ni cerrar |
| PLANTA | Consulta y revisión final independiente; no redacta trabajo en nombre del técnico |
| GERENCIA | Consulta y cierre administrativo con precondiciones; no sustituye a PLANTA/cliente |
| ADMIN / CONSULTA | Lectura interna autorizada; sin facultades implícitas de aprobación |
| CLIENTE | Se mantienen rutas históricas; sin acceso nuevo automático al contenido interno |

Los servicios comprueban usuario/perfil/empresa/rol activos, asignación y pertenencia real del equipo. Sin bypass por superusuario. POST no decide estado, autor, hash, empresa ni aprobaciones. Cambiar el filtro empresa no permite cambiar la empresa activa.

Guardas SQL PostgreSQL/SQLite protegen identidad/contenido de emisiones, revisión/cierre y Documento, saltos de estado, enlaces documentales privados de la empresa/equipo correctos y auto-revisión. Tras cierre, rechazan nuevas modificaciones/inserciones técnicas u operativas, incluso sobre revisiones antiguas. El servicio compartido también rechaza escrituras sobre expediente cerrado. Auditoría append-only y metadatos de sincronización siguen funcionando. No se implementan reaperturas/adendas sin procedimiento autorizado.

Documentos finales quedan excluidos de listados/descargas heredados de proyecto y de exploración Drive. Descarga nueva requiere permiso, empresa e integridad; respuestas privadas/no-store/nosniff. No se expone /media/ ni se publica al cliente.

## Seguimiento e historial

Dashboard `/app/seguimiento/`: estados registrados, abiertos/finalizados, recepción/inspección/diagnóstico/comercial/repuestos/reparación/calibración/informe, actividad reciente y alertas documentales. Filtros empresa activa, cliente contractual, equipo, técnico actual, estado y fechas. Indicadores derivan de registros reales y últimas versiones, sin semillas ficticias.

Consultas correlacionadas y lotes obtienen últimas decisiones/resultados y cantidades recibidas; no se ejecuta un servicio con consultas por expediente. La pantalla muestra hasta 100 filas y el total filtrado; los contadores abarcan el conjunto autorizado completo. Los avisos de políticas pendientes son explícitos. Los indicadores no certifican disponibilidad física de cada archivo: la integridad binaria se verifica al emitir, revisar, cerrar y descargar.

Ficha y QR autenticado conservan sus rutas/token y añaden enlace interno al historial de mantenimientos autorizados. Historial conserva todas las intervenciones del equipo, cliente contractual, fechas, asignaciones, estados, versiones/correcciones y enlaces a informes/documentación por módulo. No se concede a clientes acceso nuevo ni se transfieren expedientes al cambiar dueño del equipo.

## Archivos de esta fase

| Área | Archivos |
| --- | --- |
| Nuevos modelos/servicios/guardas | `sistema/masiscam/cierre_models.py`, `cierre_services.py`, `cierre_guards.py` |
| PDF, tareas y recuperación | `cierre_pdf.py`, `cierre_tasks.py`, `management/commands/reintentar_informes_finales.py` |
| Dashboard, vistas y formularios | `cierre_dashboard.py`, `cierre_views.py` |
| Plantillas | `templates/masiscam/cierre_dashboard.html`, `cierre_detalle.html`, `cierre_historial.html` |
| Integración | `models.py`, `apps.py`, `tecnica_services.py`, `recepcion_guards.py`, `drive_documents.py`, `views.py`, `urls.py`, `tasks.py`, `templatetags/masiscam_navigation.py`, `templates/masiscam/base.html`, `ficha_detalle.html`, `equipo_publico.html` |
| Configuración | `sistema/config/settings.py`, `recepcion_local_settings.py`; bandera MASISCAM_CIERRE_LOCAL deshabilitada por defecto y condicionada a DEBUG |
| Migraciones aditivas | `0027_cierre_fase6.py`, `0028_guardas_cierre.py` |
| QA y muestra | `test_cierre.py`, `sistema/tools/verificar_fase6_local.py`, `verificar_migracion_fase6.py`, `output/pdf/MASISCAM_Informe_Final_DEMOSTRATIVO.pdf` |
| Entrega | Este documento |

Se preservan los cambios previos no confirmados del árbol. No se cambian dependencias ni migraciones anteriores. Se completa el registro de tareas de fases en el módulo autodetectado por Celery; importar tareas no ejecuta llamadas externas.

## Migraciones y evidencias de conservación

0027 crea tres tablas/restricciones; 0028 añade guardas. Sin backfill ni aprobación/dato industrial de ejemplo.

Ensayos 0026 → 0028 en SQLite temporal y PostgreSQL 16 desechable, con respaldo nativo SQLite/pg_dump previo: filas de **45 tablas** históricas de Fases 2–5 conservadas en todas sus columnas, archivos y SHA-256 intactos, tablas nuevas vacías. Se incluyen QR, IDs Drive ficticios, recepción, plano, piezas, autorizaciones/resultados/revisiones y calibración de 16 posiciones históricos sintéticos. No se usa la base real.

Artefactos excluidos del versionado en `sistema/artifacts/fase6-qa/`: logs, respaldos sintéticos, inventarios/comparaciones, PDF persistido QA, capturas y páginas renderizadas. Las políticas ficticias se limitan a overrides temporales de pruebas, no se instalan en configuración operativa.

## Resultados de pruebas

| Verificación | Resultado observado |
| --- | --- |
| Regresión completa SQLite, `accounts masiscam` | 392 casos: 374 aprobados, 18 omisiones exclusivas de PostgreSQL; cero fallos/errores. `sqlite-suite.txt` |
| Regresión completa PostgreSQL 16, `accounts masiscam` | 392 aprobadas, cero fallos/errores/omisiones. `postgres-suite.txt` |
| Primera suite dirigida Fase 6 | PostgreSQL: 22 aprobadas. SQLite: 20 aprobadas, dos omisiones. `postgres-cierre.txt`, `sqlite-cierre-final.txt` |
| Verificación ampliada Fase 6 | PostgreSQL: 26 aprobadas, sin omisiones. SQLite: 24 aprobadas y dos omisiones. `postgres-cierre-verificacion-final.txt`, `sqlite-cierre-verificacion-final.txt` |
| Escalabilidad de consultas | Una prueba aprobada por motor: mismo número de consultas con uno y seis expedientes. `postgres-escala-final.txt`, `sqlite-escala-final.txt` |
| Integración de anexos operativos y cierre | Una aprobada por motor: informe de reparación existente reutilizado como anexo, PDF final íntegro, revisión/cierre, QR conservado. `postgres-anexos-final.txt`, `sqlite-anexos-final.txt` |
| Selectores y vistas finales | Una aprobada por motor, sobre filtros, historial, detalle y ámbitos finales. `postgres-selectores-final.txt`, `sqlite-selectores-final.txt` |
| Interfaz, vigencia y peticiones inválidas finales | Cuatro aprobadas por motor. Incluyen calibración sustituida, POST con versión inválida, filtros de empresa e historial de técnico sin asignación. `postgres-interfaz-vigencia-final.txt`, `sqlite-interfaz-vigencia-final.txt` |
| Migración 0026 → 0028 | Aprobada en ambos motores; 45 tablas y todos sus archivos/hashes conservados; tablas nuevas vacías |
| Aceptación funcional HTTP | Recorrido sintético de reserva, persistencia, descarga, revisión PLANTA y cierre GERENCIA; bloqueo comercial por defecto; QR y guía de ingreso anteriores intactos |
| PDF demostrativo | A4, siete páginas, extracción pypdf, sin formularios/firmas, render y revisión visual de todas las páginas |
| Interfaz responsive | 15 capturas: cinco estados en 1400, 768 y 390 px, sin desbordamiento del viewport; navegador Edge headless sin red |
| Coherencia | Django check sin incidencias; makemigrations --check --dry-run sin cambios; diff --check sin errores de espacios |

Cobertura nueva: fuentes vigentes y retirada de aprobaciones, independencia/rol histórico, corrección/versionado, PDF completo/hash, errores de almacenamiento y falso éxito, corrupción/descarga protegida, UUID conflictiva/reintentos, emisión y cierre concurrentes, congelación ORM/SQL, aislamiento, peticiones manipuladas, registro de tareas en worker, Drive posterior a commit simulado, filtros, historial/QR y consultas por lotes.

Las verificaciones dirigidas se informan separadamente porque incluyen repeticiones; no se suman sus ejecuciones como casos únicos. La suite completa comenzó con 22 casos nuevos; los cuatro casos ampliados y la prueba de escalabilidad se añadieron después. No se reduce ninguna prueba previa ni se cuentan omisiones como aprobaciones. Los errores iniciales de fixtures/expectativa de excepción se corrigieron y las suites dirigidas se repitieron.

La muestra `output/pdf/MASISCAM_Informe_Final_DEMOSTRATIVO.pdf` contiene exclusivamente datos/personas/criterios/aprobaciones FICTICIOS QA e ilustración sintética. No representa un equipo real ni una autorización industrial/comercial. Las anclas temporales se retiran al terminar el escenario; configuración operativa queda sin aprobaciones. El hash y las capturas finales se registran en `aceptacion-visual.json`.

SHA-256 de la muestra: `2f444aaa1f1ddf8a6b0cdf0b83bd54f60b037ddb0b634236daa93614000bcac6`. `resumen-final.json` valida logs terminados, migraciones, 15 capturas, PDF y limpieza del contenedor QA. La suite actual contiene 27 casos propios de Fase 6: los 22 de la regresión inicial, cuatro ampliados y uno de escalabilidad; las ampliaciones y ajustes posteriores se verificaron de forma dirigida, sin atribuirlos retrospectivamente a la suite completa inicial.

Se retiró exclusivamente el PostgreSQL 16 desechable, verificados su etiqueta, imagen, tmpfs y ausencia de bases de prueba activas; `postgres-cleanup.json`. Los servicios reales existentes conservaron su ejecución y estado saludable. No se ejecutó Compose, migración real, acceso a base real/Drive real, instalación de dependencias, commit, push ni despliegue.

## Riesgos y requisitos previos a despliegue

- Aprobar procedimiento comercial concreto, alcance/respaldo y tratamiento de excepciones; hoy la configuración permanece bloqueada.
- Aprobar protocolos industriales exactos por equipo/tipo, unidades, fuentes, criterios/tolerancias y política metrológica. Sin ello no hay cierre operativo válido.
- Acordar los casos de no aplicabilidad, devolución por rechazo, reapertura/adenda, retención y publicación/aceptación del cliente. No se activan por esta fase.
- Aprobar y probar almacenamiento persistente, backups/restauración, retención, credenciales Drive/broker y permisos mínimos de base de datos en un entorno de despliegue separado. QA usa Drive/Celery simulados y no certifica credenciales ni disponibilidad real.
- El usuario de infraestructura con DDL/superusuario puede retirar triggers o alterar almacenamiento; las guardas no sustituyen controles operativos y respaldos.
- Los contadores recorren la proyección autorizada completa y no verifican todos los binarios en cada carga; evaluar volumen/índices/monitor de integridad antes de grandes instalaciones.
- Aprobar presentación del PDF y verificar que la trazabilidad separada de decisiones posteriores cumple las necesidades documentales del propietario. No se implementa firma certificada ni conformidad normativa implícita.
- Planificar el orden de aplicación de migraciones y reinicio de procesos: el código integrado requiere las tablas/guardas nuevas. No arrancar workers con código nuevo sobre un esquema anterior ni ejecutar Compose sin autorización.

Estado técnico final: **Fase 6 completada en local/QA; el sistema integral está preparado para revisión técnica previa al despliegue, sujeto a los límites de las pruebas descritas.** No está operativamente listo: faltan aprobaciones comerciales/industriales y validación de infraestructura/documentación de producción. El siguiente paso mínimo es revisar esta entrega y aprobar los procedimientos y fuentes exactos pendientes; después, planificar una aceptación previa al despliegue con respaldo y autorización expresa. No se activa producción por esta entrega.
