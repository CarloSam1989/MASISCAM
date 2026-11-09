# Plan de migraciones futuras

Fecha: 2026-11-05. Plan exclusivamente documental. No se generó ni ejecutó migración, ni se autoriza ejecutar los pasos siguientes en esta fase.

## Línea base

La carpeta `sistema/masiscam/migrations/` llega a `0014_orden_cronologico_registros`. Contiene ramas 0009/0010 reconciliadas mediante `0010_merge_20260922_2021` y `0011_merge_20260924_1307`. `0012` permite varios usuarios por cliente; `0013` añade ArchivoRegistro y secuencia; `0014` ordena/renumera por fecha. No reescribir, fusionar ni volver a ejecutar manualmente esas migraciones para introducir expedientes.

El estado aplicado en producción no se ha verificado. Confirmar grafo y `django_migrations` del entorno objetivo mediante lectura autorizada antes de asignar números reales a las nuevas migraciones. No asumir que la numeración 0015 está libre al implementar.

## Secuencia propuesta

| Paso futuro | Cambio | Protección y criterio de salida |
| --- | --- | --- |
| M0: inventario | Lectura del esquema, migraciones aplicadas y conteos; inventario de PK/tokens/IDs/hashes y respaldo consistente DB+archivos | Baseline firmada por responsable; restauración comprobada en entorno aislado; no exponer datos personales en logs |
| M1: expansión | Tablas ExpedienteMantenimiento, RecepcionEquipo, EtapaExpediente y EventoTransicion; roles nuevos; enlaces opcionales en Documento/Auditoria | Ninguna fila histórica requiere expediente; ningún default crea datos o carpeta; UNIQUE y PROTECT definidos |
| M2: protección | Guardas de identidad/cierre, servicios de autorización y bloqueo en rutas legacy/admin/importación | Desplegar guardas antes de habilitar la primera alta; impedir llamadas destructivas a Drive antes de borrar |
| M3: recepción | Habilitación controlada del flujo de recepción y emisión de guía | QR y registros anteriores iguales; rol explícito; almacenamiento validado |
| M4: datos técnicos | Inspecciones, diagnósticos, planos/partes, piezas y evidencias | Campos técnicos aprobados; revisiones y pertenencia verificadas |
| M5: comercial y reparación | Aprobaciones, repuestos, actividades y aplicaciones | Bloqueos y decisiones inmutables probados; unidades y cantidades validadas |
| M6: calibración | Protocolos, parámetros, mediciones, aprobación técnica | Solo después de validar formulario de 16 posiciones; ninguna tolerancia de ejemplo en datos |
| M7: informes/cierre | Emisiones versionadas, adendas, publicación y cierre | Instantáneas/hash, protección y recuperación verificadas |

Estos pasos son unidades de cambio, no archivos ya creados. EmisionInforme y enlace EvidenciaExpediente mínimos pueden adelantarse a M1/M3 para la guía; luego ampliarse de forma aditiva. La Fase 2 requiere autorización expresa y solo cubre el alcance acordado.

## Compatibilidad de datos

- No crear automáticamente expedientes para todos los registros. Los antiguos siguen sin extensión y sus pantallas permanecen utilizables.
- Conversión histórica optativa: seleccionar por ID, verificar tipo y empresa, informar datos faltantes y obtener validación humana. Crear extensión idempotente por registro, nunca otro Equipo/RegistroEquipo ni otra carpeta.
- No inventar fechas, técnicos, guías, aprobaciones o 16 mediciones para satisfacer NOT NULL. Un histórico incompleto no se declara cerrado/completo; si se convierte, debe quedar identificado como legado incompleto y bloqueado hasta validación.
- No usar fecha o secuencia existente como número oficial. Asignar una vez el nuevo identificador y preservar el nombre histórico.
- Campos nuevos en modelos actuales inicialmente opcionales y sin backfill remoto. Agregar restricciones NOT NULL solo tras comprobar datos de las filas a las que corresponden; los documentos/registros heredados sin expediente continúan válidos.
- Revisar duplicados de serie antes de cualquier unicidad en Equipo. No imponer restricción nueva ni corregir series automáticamente en esta fase.
- No alterar FK cliente de equipos ni mapear ADMIN a GERENCIA/PLANTA automáticamente. Asignaciones operativas son una decisión explícita, auditada y reversible antes de uso.

## Restricciones futuras

UNIQUE de registro OneToOne, número, etapa por expediente, versión por tipo de emisión y posición de medición. CHECK de cantidades positivas, posición 1..16, estados, fuentes de evidencia excluyentes y coherencia de fechas/límites. Índices para empresa/situación, responsables, expediente/etapa y documentos por expediente/tipo.

Pertenencia entre tablas y congelación requieren servicios transaccionales y, para garantizar protección frente a update/bulk/SQL con credencial de aplicación, guardas PostgreSQL diseñadas y probadas. Un CHECK no puede verificar por sí solo empresa de una FK ni la existencia de exactamente 16 filas relacionadas. No introducir triggers genéricos sin analizar rutas, auditoría, importación y reversión.

Proteger registros con expediente mediante relación PROTECT; proteger evidencias usadas y fuentes de emisiones. Las guardas deben cubrir padre e hijos cerrados/enviados y evitar borrado de empresa/proyecto/equipo que los contenga. Aplicación sin permisos DDL/TRUNCATE; auditoría append-only en DB bajo credencial de aplicación. Los cambios de infraestructura quedan fuera de esta fase.

## Drive, señales y operaciones diferidas

Ninguna migración contacta Drive/Celery ni genera PDFs. Backfills futuros deben evitar activar señales que creen carpetas; reutilizar IDs existentes y ensayar ese comportamiento. Reorganizaciones remotas, si fueran necesarias, son procedimientos separados, con inventario, simulación y autorización. No forman parte de este plan de esquema.

Un envío posterior a commit puede perderse si el proceso cae antes de encolar. La futura implementación necesita reconciliación por estados persistidos y tareas idempotentes; validar si basta un barrido periódico o se requiere outbox antes de prometer entrega garantizada. No duplicar archivos ni decisiones al reintentar.

## Despliegue y reversión futuros

Patrón expandir → desplegar guardas/código compatible → habilitar por empresa → observar. En tablas grandes evaluar locks/duración e índices según volumen real en PostgreSQL. No ejecutar cambios de datos masivos en una transacción con llamadas remotas.

Mientras existan expedientes, no volver a una versión de aplicación que ignore sus protecciones: deshabilitar funciones nuevas y mantener guardas, o aplicar corrección hacia adelante. No revertir esquema destruyendo expedientes o emisiones. Restauración completa solo con plan que concilie DB, contenido local y Drive; restaurar SQLite o DB por separado no recupera archivos borrados remotamente.

## Validación de salida

Comparar conteos, PK, tokens, hashes, relaciones, carpetas/IDs Drive y acceso antes/después; registrar explícitamente únicamente nuevas filas/campos esperados. Probar migración desde copia anonimizada del esquema real, repetición del backfill selectivo, concurrencia PostgreSQL y rollback operativo sin pérdida. Ver [riesgos y pruebas](riesgos_y_validaciones.md).
