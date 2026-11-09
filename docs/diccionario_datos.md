# Diccionario de datos del expediente

Fecha: 2026-11-05. Diseño lógico propuesto, no modelos implementados. PDF original no disponible. Campos técnicos, formularios y obligatoriedad sujetos a validación de planta. Relaciones en [arquitectura](arquitectura_expedientes.md); reglas en [flujo](flujo_estados_mantenimiento.md).

## Convenciones

PK: BigAutoField salvo indicación; FK de negocio con PROTECT salvo hijos internos cuya política se especifica. Todos los timestamps de servidor con zona horaria y representación local configurada; fechas reales de operación separadas de fechas de registro. Autores de datos y responsables son FK al usuario existente, sin borrar su identidad al desactivar cuentas. No crear otra tabla de clientes, equipos o archivos binarios.

Revisiones técnicas usan entero positivo `revision`, `creado_por`, `creado_en`, `enviado_en` opcional y FK `sustituye` opcional protegido al mismo tipo. Solo una revisión vigente por expediente y tipo; el reemplazo se realiza mediante servicio con bloqueo. Una revisión enviada es inmutable; su corrección crea otra. Conservar datos y eventos aun si una decisión queda invalidada. Longitudes, precisiones y escalas no definidas aquí se fijarán antes del modelo físico, conforme a muestras reales.

## ExpedienteMantenimiento: raíz

| Campo | Tipo lógico / relación | Regla |
| --- | --- | --- |
| registro | OneToOne RegistroEquipo, PROTECT | Obligatorio, único e inmutable; tipos inicialmente MANTENIMIENTO/REPARACION |
| numero | Texto UNIQUE, generado por servidor | Inmutable; propuesta EXP-UUID completo; no derivar de secuencia cronológica |
| empresa | FK Empresa PROTECT | Se fija desde registro.equipo.proyecto.empresa; frontera de acceso inmutable |
| cliente_contractual | FK Cliente PROTECT | De la misma empresa; coincide con cliente del equipo al abrir; no cambia al transferir el equipo |
| revision_actual | Entero positivo | Control de concurrencia y versión del conjunto; no ID documental |
| situacion | ABIERTO/CERRADO/ANULADO | Gestionada por transiciones |
| responsable_oficina | FK usuario | Activo y con rol OFICINA en la empresa al asignar |
| tecnico_responsable | FK usuario, opcional durante borrador | Requerido antes de inspección; asignado por PLANTA |
| administrador_planta | FK usuario, opcional durante borrador | Requerido antes de revisión técnica; distinto de autores revisados |
| responsable_comercial | FK usuario, opcional durante borrador | Rol GERENCIA; requerido para decisión comercial |
| creado_por / creado_en | FK usuario / timestamp | Auditoría de apertura |
| cerrado_por / cerrado_en | FK usuario / timestamp, opcionales | Ambos presentes solo si cerrado; cierre conserva emisión final |
| anulado_por / anulado_en / motivo_anulacion | FK / timestamp / texto, opcionales | Obligatorios al anular; no borrar intervención |
| motivo_intervencion | Texto | Alcance solicitado al ingreso |

No repetir FK Equipo: se obtiene de registro. No duplicar carpeta Drive: usar registro.drive_folder_id. Cambios de responsables generan eventos y conservan asignaciones/autores históricos mediante sus referencias en revisiones y auditoría.

## RecepcionEquipo

OneToOne al expediente protegido. Campos: fecha_hora_recepcion, recibido_por, entregado_por_nombre, entregado_por_identificacion (opcional y sujeto a necesidad), guia_remision_numero, guia_remision_fecha, guia_documento (FK Documento opcional), motivo_sin_guia, condicion_ingreso, accesorios_recibidos, observaciones y snapshot_identificacion JSON versionado.

Guía presente o ausencia justificada; política exacta pendiente. Snapshot contiene cliente/RUC, marca/modelo/serie, tipo, ratio y demás datos existentes relevantes, sin sustituir FK. Lista de accesorios inicialmente texto; si se requiere control de entrega por ítem, normalizar tras validar formulario. Evidencias se enlazan mediante EvidenciaExpediente. Guía de ingreso generada se registra como EmisionInforme, no como otro FileField.

Recepción enviada queda congelada; correcciones se representan como adendas/revisiones documentadas, manteniendo el snapshot inicial. Requerir protocolo de corrección antes de permitir editar después de finalizar la etapa.

## EtapaExpediente y EventoTransicion

EtapaExpediente: FK expediente, codigo_etapa, estado, responsable (opcional), revision, inicio_en/fin_en opcionales, observacion_actual. UNIQUE(expediente, codigo_etapa). CHECK de códigos/estados y coherencia temporal. Estado editable solo por el servicio; no por formulario genérico.

EventoTransicion: FK etapa, revision, estado_anterior, estado_nuevo, actor, creado_en, motivo, clave_idempotencia UUID, detalle JSON con IDs de evidencia y aprobaciones. UNIQUE(etapa, clave_idempotencia). Append-only. No contiene secretos ni bytes. Vincular Auditoria mediante ID de evento/referencia explícita; eventos de negocio y auditoría de acceso tienen propósitos distintos.

## Inspeccion y Diagnostico

Inspeccion: FK expediente, revisión común, fecha_inspeccion, tecnico, condiciones_observadas, observaciones, resultado_inspeccion (texto). Lista concreta de verificaciones pendiente; no crear resultados automáticos sin protocolo. Si se requieren ítems repetibles, ItemInspeccion referenciará criterio de una versión de protocolo y resultado con unidad/evidencia, nunca una lista inventada.

Diagnostico: FK expediente, FK inspeccion revisada, revisión común, hallazgos, causa_identificada (puede ser no determinada expresamente), conclusion, trabajo_propuesto, observaciones, declaracion_sin_piezas. Requiere piezas o declaración explícita de que no se requieren. Informe inicial ligado a revisión mediante EmisionInforme.

## Biblioteca: PlanoTecnico y PartePlano

PlanoTecnico: empresa FK; marca, modelo, ratio, version, titulo, Documento FK PROTECT, vigente, creado_por/en. UNIQUE(empresa, marca, modelo, ratio, version) con política de normalización pendiente. Diferenciar versiones y preservar las usadas históricamente. Alcance por empresa por defecto; compartir entre empresas requiere autorización adicional.

PartePlano: FK plano PROTECT, posicion, numero_parte, descripcion, unidad, cantidad_referencia opcional. UNIQUE(plano, posicion). La unidad y cantidad provienen del fabricante; no inferirlas. Un plano usado se congela; actualización crea versión nueva.

Seleccionar desde listado primero. Coordenadas de selección sobre plano (página, sistema de coordenadas y región) quedan opcionales para una entrega posterior; DWG/DXF requieren vista previa/conversión específica aún no elegida. No asumir que el navegador o un motor PDF interpreta CAD.

## PiezaRequerida

FK diagnostico/revision protegido; PartePlano FK opcional; numero_parte_snapshot, descripcion_snapshot, cantidad Decimal positiva, unidad, condicion, accion_requerida y observaciones. Aceptar entrada manual cuando no haya catálogo, registrando origen MANUAL/BIBLIOTECA. Catálogos de condición/acción, fracciones permitidas y unidades se validan con planta. No vincular una parte de otra empresa ni de una versión técnica incompatible sin excepción sustentada.

## Aprobacion

FK expediente; etapa FK; tipo COMERCIAL/TECNICA/INFORME/ADENDA; revision_objetivo; solicitud UUID; decision APROBADO/OBSERVADO/RECHAZADO; decidido_por; decidido_en; motivo; respaldo_documento FK opcional y snapshot_alcance JSON. UNIQUE(solicitud) para decisión única; reconsideración usa nueva solicitud. Todas las decisiones son inmutables. Invalidar vigencia mediante evento, no cambiar una aprobación antigua a rechazo.

COMERCIAL exige respaldo de cliente: documento, fecha_autorizacion_cliente, nombre/cargo del autorizante, alcance autorizado, referencia proforma/orden si aplica. TECNICA exige revisión de calibración y trabajos relacionados. INFORME referencia emisión y hash exactos. Firmas electrónicas/certificados y valor legal son decisiones pendientes; guardar nombre y fecha no equivale a firma criptográfica.

## RecepcionRepuesto y DetalleRepuesto

RecepcionRepuesto: FK expediente; fecha_hora; recibido_por; procedencia_tipo y procedencia_descripcion; proveedor_nombre (texto, sin crear catálogo innecesario); guia_numero/fecha; guia_documento FK opcional; observaciones. Múltiples entregas por expediente.

DetalleRepuesto: FK recepción PROTECT; pieza_requerida FK opcional; numero_parte, descripcion, cantidad Decimal positiva, unidad, condicion_recibida, conformidad, observacion. Fotografías por EvidenciaExpediente. Distinguir solicitado/recibido/aplicado; no sumar unidades distintas ni aprobar excedentes/sustituciones automáticamente. No representa un módulo de inventario, compras o valoración contable.

## ActividadReparacion y AplicacionRepuesto

ActividadReparacion: FK expediente; revisión común; tecnico; inicio/fin; tipo REPARACION/MONTAJE; descripcion, resultado, observaciones. Fechas coherentes; permisos y aprobación comercial vigentes. Varias actividades por intervención.

AplicacionRepuesto: FK actividad y detalle_recibido protegidos; cantidad positiva; unidad coincidente; observacion. Bajo bloqueo, total aplicado no supera recibido conforme menos cantidades ya aplicadas. Política de devolución, desperdicio y sustitución pendiente; hasta definirla, rechazar inconsistencias en lugar de inventar movimientos.

## ProtocoloCalibracion, ParametroCalibracion y mediciones

ProtocoloCalibracion: empresa, código, versión, tipo_producto, marca/modelo/ratio aplicables, fuente_documento FK, validado_por/en opcionales, estado BORRADOR/VALIDADO/RETIRADO. Versiones usadas son inmutables. No habilitar captura final sin protocolo validado por planta.

DefinicionParametro: FK protocolo, codigo, nombre, unidad, mínimo/máximo opcionales, fuente y observacion. Incluye parámetros de shims, backlash, presión y tolerancias según el formulario real. Tipos numéricos, escala, condiciones y aplicación exacta pendientes. Sin defaults técnicos ni fórmulas propuestas. No usar float para mediciones decimales.

Calibracion: FK expediente, revisión común, protocolo FK PROTECT, tecnico, fecha, instrumento_identificacion, certificado_documento opcional, observaciones y conclusion_tecnico. Política de vigencia/certificado del instrumento pendiente.

ValorParametroCalibracion: FK calibración y definición; valor Decimal, unidad_snapshot, mínimo/máximo_snapshot opcionales, observación y fuente_snapshot. UNIQUE(calibracion, definicion). Restricción mínimo <= máximo cuando ambos existan. No deducir conforme si faltan límites aplicables.

MedicionCalibracion: FK calibración; posicion entero entre 1 y 16; valor Decimal; unidad; etiqueta/punto_medicion_snapshot; mínimo/máximo opcionales; resultado CONFORME/NO_CONFORME/NO_EVALUADO; observaciones. UNIQUE(calibracion, posicion). El significado físico de cada posición, si hay presión por punto, repetición, condiciones de ensayo y fórmulas se deben validar. Si el formulario real contiene varios valores por posición, ajustar a detalle por parámetro antes de migrar.

Exactamente 16 posiciones distintas y completas se exige al enviar, no durante borrador. Un CHECK por fila no garantiza 16 filas: validar bajo bloqueo y congelar revisión; evaluar guarda diferida PostgreSQL al diseñar modelo físico. No permitir aprobar NO_EVALUADO donde el protocolo exija conformidad. Aprobar excepciones técnicas no está autorizado por este documento.

## EvidenciaExpediente y Documento existente

EvidenciaExpediente enlaza FK expediente y revisión/etapa, finalidad (recepción, inspección, diagnóstico, repuesto, reparación, calibración o aprobación), descripción, autor/fecha; exactamente uno de FK ArchivoRegistro o FK Documento, ambos PROTECT. No GenericForeignKey: usar FK explícitas opcionales a inspección/diagnóstico/detalle_repuesto/actividad/calibración según finalidad y verificar pertenencia. Restricción de exclusión de ambas fuentes y reglas de destino en servicio/DB según modelo físico. Fuentes solo de la misma empresa y del registro correspondiente cuando sean ArchivoRegistro.

Documento conserva categorías y su deduplicación por proyecto/hash. Añadir FK expediente opcional, tipo_documental opcional y clasificación de acceso. Para vincular un documento ya existente a varios usos, reutilizarlo vía EvidenciaExpediente; no crear copia binaria para vencer el hash único. Proyecto/equipo del Documento deben ser coherentes con expediente al vincular. Un cambio posterior de proyecto del equipo no reescribe documentos emitidos; preservar contexto histórico.

Tipos documentales propuestos: GUIA_REMISION, GUIA_INGRESO, DIAGNOSTICO, PROFORMA, ORDEN_COMPRA, AUTORIZACION, RECEPCION_REPUESTOS, FACTURA, CALIBRACION, INFORME_FINAL y OTRO. La obligatoriedad depende de etapa y se valida con operación; no exigir factura/proforma indistintamente sin política.

## EmisionInforme

FK expediente; tipo GUIA_INGRESO/INICIAL/CALIBRACION/FINAL/ADENDA; version entero positivo; revision_fuente; estado RESERVADA/GENERANDO/GENERADA/ERROR/EMITIDA; Documento FK opcional PROTECT; snapshot_datos JSON versionado; manifest_evidencias (IDs, hashes, revisiones); version_plantilla; hash_sha256; generado_por/en; emitido_por/en; emision_anterior FK opcional PROTECT; clave_idempotencia UUID única; mensaje_error seguro. UNIQUE(expediente, tipo, version).

Documento es obligatorio al GENERADA/EMITIDA; no duplicar su FileField. Aprobación de informe vincula hash y emisión. Una emisión emitida y su archivo son inmutables; nueva versión conserva anterior. Snapshot no depende de nombres o rutas Drive cambiables. Guardar identidad/roles de firmantes al emitir, sin secretos. No incluir documentos comerciales privados en un PDF público por defecto.

## Pendientes antes del esquema físico

Formato del número; política de correcciones de recepción; checklist y documentos obligatorios; catálogos de condición/acción/unidad; formato técnico de las 16 posiciones; precisión/escala por magnitud; tolerancias y fuentes; firmas; retención y publicación; traslados de propietario; protocolo de bombas. Ninguno de estos datos se considera aprobado por su inclusión como campo propuesto.
