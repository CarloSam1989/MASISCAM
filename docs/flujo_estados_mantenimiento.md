# Flujo y estados de mantenimiento

Fecha: 2026-11-05. Propuesta pendiente de validación. No describe un flujo ya existente. Roles en [matriz](matriz_roles_permisos.md); datos en [diccionario](diccionario_datos.md).

## Etapas y estados

Orden: RECEPCION → INSPECCION → DIAGNOSTICO → APROBACION_COMERCIAL → REPARACION → CALIBRACION → APROBACION_TECNICA → INFORME_FINAL → CIERRE.

Una EtapaExpediente por código y expediente. Estados: NO_INICIADO, EN_PROCESO, PENDIENTE_APROBACION, OBSERVADO, APROBADO, FINALIZADO, RECHAZADO. No todos se aplican a todas las etapas. APROBADO significa decisión favorable; FINALIZADO significa trabajo o etapa concluida. Los estados de carga Drive y Equipo.estado son independientes.

Expediente tiene `situacion` ABIERTO/CERRADO/ANULADO y un indicador versionado de revisión. Su etapa actual se deriva de la primera etapa obligatoria no finalizada; el dashboard muestra estado de cada etapa y bloqueos. No almacenar varios estados globales editables que puedan contradecirse.

## Reglas de transición

| Etapa | Inicio y trabajo | Condición de salida | Responsable |
| --- | --- | --- | --- |
| Recepción | NO_INICIADO → EN_PROCESO | Identidad, cliente, fecha, responsable, condición de ingreso, guía/remisión o ausencia justificada; guía de ingreso emitida → FINALIZADO | OFICINA |
| Inspección | Solo con recepción finalizada; NO_INICIADO → EN_PROCESO | Inspección y evidencias obligatorias completas → FINALIZADO | TECNICO asignado |
| Diagnóstico | Solo con inspección finalizada; NO_INICIADO → EN_PROCESO | Hallazgos, conclusión, piezas o declaración sin piezas; informe inicial emitido → FINALIZADO | TECNICO asignado |
| Aprobación comercial | Diagnóstico e informe inicial vigentes; NO_INICIADO → PENDIENTE_APROBACION | Respaldo del cliente y alcance/revisión coincidentes; APROBADO → FINALIZADO | GERENCIA |
| Reparación | Solo con comercial finalizada favorable y repuestos necesarios recibidos o excepción justificada autorizada | Actividades, montaje, repuestos aplicados y resultados completos → FINALIZADO | TECNICO asignado |
| Calibración | Solo con reparación finalizada; NO_INICIADO → EN_PROCESO | Protocolo validado, parámetros y exactamente 16 mediciones requeridas completas; envío congela revisión → FINALIZADO | TECNICO asignado |
| Aprobación técnica | Solo con calibración enviada y evidencias disponibles; NO_INICIADO → PENDIENTE_APROBACION | PLANTA independiente aprueba revisión concreta; APROBADO → FINALIZADO | PLANTA |
| Informe final | Solo con aprobación técnica favorable vigente | Generación desde instantánea; revisión del PDF por PLANTA; APROBADO → FINALIZADO al emitir | Preparación OFICINA/TECNICO; autorización PLANTA |
| Cierre | Solo con informe final emitido y verificable, requisitos documentales y aprobaciones vigentes | EN_PROCESO → FINALIZADO y situacion=CERRADO | GERENCIA |

La recepción de repuestos es un subproceso del expediente; puede registrarse después de autorización comercial y antes/durante reparación. No habilita por sí misma la reparación. La excepción de repuestos solo justifica ausencia/no aplicación; nunca salta una aprobación comercial o técnica.

No iniciar calibración automática sin protocolo validado. Si un tipo de bomba no aplica al mismo formulario, debe definirse su protocolo; no rellenar ceros, simular mediciones o marcarlo finalizado con campos desconocidos.

## Observación, rechazo y corrección

- En etapa operativa: EN_PROCESO → OBSERVADO con motivo, responsable y requisitos de corrección; OBSERVADO → EN_PROCESO mediante nueva revisión. Conservar revisión anterior.
- En etapa de aprobación: PENDIENTE_APROBACION → OBSERVADO, RECHAZADO o APROBADO. Motivo obligatorio al observar/rechazar. Decisión incluye autor, instante, revisión y evidencia. APROBADO → FINALIZADO se registra como transición separada dentro de la misma transacción.
- OBSERVADO permite corrección y nuevo envío → PENDIENTE_APROBACION. RECHAZADO no habilita ninguna etapa posterior. GERENCIA solicita reconsideración comercial o PLANTA reconsideración técnica; se crea nueva solicitud/revisión conservando el rechazo anterior.
- No confundir rechazo con anulación. Si el cliente no autoriza, expediente queda bloqueado y abierto hasta reconsideración o anulación por GERENCIA con respaldo y motivo. La entrega/devolución por rechazo y sus documentos están pendientes de definición; no cerrar como mantenimiento completado.
- Si se corrige una etapa finalizada, registrar solicitud de revisión con motivo y alcance. PLANTA autoriza corrección técnica; si afecta alcance/costo/autorización, también GERENCIA. Incrementar revisión, registrar invalidación de aprobaciones dependientes y bloquear todas las etapas posteriores afectadas. Las decisiones antiguas sobreviven como no vigentes.
- Cambiar diagnóstico/piezas después de autorización comercial exige nueva autorización comercial. Cambiar reparación/calibración después de aprobación técnica exige nueva aprobación técnica e informe nuevo. No recalcular ni reemplazar una emisión previa.
- Tras cierre no se editan datos originales. Una adenda enlaza emisión anterior y motivo; una reapertura excepcional conserva el evento de cierre, requiere autorizaciones y una revisión nueva. Un mantenimiento nuevo crea otro RegistroEquipo y expediente.

## Invariantes de ejecución

Toda transición exige permiso, pertenencia, precondición, versión esperada y completitud. Repetir una petición con la misma clave devuelve el mismo resultado; una petición con revisión obsoleta se rechaza como conflicto. Edición concurrente y aprobación se serializan con bloqueo del expediente.

No borrar eventos. EventoTransicion almacena etapa, revisión, estado anterior/nuevo, usuario, fecha del servidor, motivo, clave idempotente y referencias de evidencia. Se crea junto con Auditoria en la misma transacción. Las etapas posteriores invalidadas reciben eventos explícitos; no borrar sus resultados para simular un primer intento.

Las fechas efectivas aportadas por el usuario se diferencian de timestamps de registro; toda corrección de fecha se audita. No usar `RegistroEquipo.secuencia` como identificador, ni estados PENDIENTE/ERROR de archivos como estados técnicos.

## Emisión y fallos externos

La generación y sincronización tienen estados propios (RESERVADA/GENERANDO/GENERADA/ERROR y estado Drive existente). Error de PDF o Drive no equivale a rechazo técnico. Un reintento usa la misma emisión/id remoto cuando corresponde y no repite aprobaciones. Cierre exige PDF íntegro y copia documental confirmada conforme a la política de almacenamiento aprobada; propuesta inicial: copia local persistente y Drive confirmado.

Pendientes: obligatoriedad exacta por etapa, revisión del informe inicial, devolución de rechazados, formato de firmas, aprobación de excepciones y protocolos específicos de bombas. Ninguna excepción queda autorizada por este diseño.
