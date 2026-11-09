# Revisión local de usuarios y RBAC — 2026-11-09

Ruta: `/app/usuarios/`. Roles existentes: ADMIN, OFICINA, TECNICO, PLANTA,
GERENCIA, CLIENTE y CONSULTA. No faltan roles ni se convierten usuarios.

Matriz efectiva tras la corrección (sujeta a empresa, estado, autoría y asignación):

| Ventana / operación | Acceso efectivo encontrado |
| --- | --- |
| Usuarios | ADMIN; superusuario técnico heredado |
| Dashboard | Todos; CLIENTE con alcance propio |
| Crear/editar clientes | ADMIN y OFICINA, en su empresa; TECNICO denegado |
| Consultar clientes: TECNICO | Clientes contractuales de expedientes con asignación vigente, empresa y propietario coherentes |
| Equipos y QR heredados | Según permisos generales y filtros de objeto existentes |
| Recepción: leer / escribir | ADMIN, CONSULTA, OFICINA, PLANTA, GERENCIA / OFICINA |
| Expedientes técnicos | Internos; TECNICO solo asignado; CLIENTE excluido |
| Inspección, diagnóstico, piezas | TECNICO asignado y autor; PLANTA autoriza nueva revisión |
| Biblioteca | Internos autorizados; TECNICO solo aplicable; PLANTA administra |
| Revisión técnica | PLANTA independiente de las autorías |
| Propuestas y decisión comercial | GERENCIA, con políticas y respaldo válidos |
| Respaldos comerciales | OFICINA, GERENCIA |
| Recepción de repuestos | OFICINA, PLANTA; TECNICO excluido |
| Reparación | TECNICO asignado, autorización comercial válida; revisión PLANTA |
| Calibración | TECNICO asignado; protocolo y decisión independiente PLANTA |
| Solicitar informe inicial / calibración | OFICINA, PLANTA, TECNICO asignado |
| Solicitar informe final | OFICINA, TECNICO asignado; revisión PLANTA |
| Cierre administrativo | GERENCIA con dependencias completas |
| Documentos y descargas | Rol, empresa, asignación, clasificación y etapa; sin publicación nueva al cliente |
| Portal cliente | Accesos históricos propios; sin biblioteca interna |
| CONSULTA | Lectura histórica; no se ofrece para nuevas asignaciones |

Las fases 3–6 requieren sus banderas locales; la matriz no las habilita.
Las decisiones D01–D07 se contrastaron con validacion_fase3.md y su autorización
registrada en implementacion_fase3.md. La matriz propuesta general no reemplaza
los servicios específicos de cada fase.

Corrección autorizada expresamente por el propietario el 2026-11-09: OFICINA
crea, consulta y modifica clientes de su empresa; TECNICO solo consulta los
relacionados con sus expedientes autorizados y no crea, edita ni elimina clientes.
Se conservan los permisos generales de equipos/registros y los servicios de las
fases 2–6. No se conceden aprobaciones técnicas o comerciales a ADMIN u OFICINA.
El PDF original continúa sin estar disponible; la autorización transcrita del
propietario es la base funcional de esta corrección, no una lectura del PDF.

Implementación: permisos específicos `clientes_crear` y `clientes_editar`, sin
ampliar `crear`/`editar` genéricos. Consulta compartida para listado, detalle,
búsqueda AJAX y selectores de clientes de fichas/equipos. Asignación finalizada
o cambio de rol revoca acceso en la siguiente petición. Las vistas y el helper
de escritura validan el permiso en servidor. Botones Crear/Editar y alta modal
usan los mismos permisos. No existe una ruta de eliminación de clientes en la
aplicación; Django admin conserva su frontera previa de solo superusuarios.
CLIENTE, CONSULTA, PLANTA y GERENCIA mantienen sus restricciones.

Se conserva la mejora previa de Usuarios: permisos generales y alcance operativo
por cuenta, empresa de las altas explícita, encabezado adaptable y rechazo de
usuario/empresa inactivos. Ruta de administración: `/app/usuarios/`.

Entorno QA corregido: Python 3.12.12 en `sistema/.venv-rbac`, Pillow 12.3.0 y
Django 5.2.17; dependencias instaladas desde requirements.txt sin modificarlo.
Se añadió pypdf 6.21.4 exclusivamente al entorno QA para las pruebas de informes.
Docker no expuso su motor; se utilizó PostgreSQL 16.4 portable oficial, clúster
nuevo en `sistema/.venv-rbac-pg/data`, exclusivamente `127.0.0.1:55446`, base
`masiscam_qa_rbac` y base de pruebas creada/destruida por Django. Sin datos reales,
servicios instalados, migraciones productivas, Drive ni workers reales.
El clúster QA quedó detenido al finalizar; Django destruyó sus bases de pruebas.

Pruebas: 41 casos iniciales aprobados en SQLite efímera. La selección final
PostgreSQL incluye test_clientes, test_usuarios, test_usuario_admin,
test_cliente_access, test_security, test_recepcion, test_recepcion_confirmacion,
test_tecnica, test_operacion, test_calibracion, test_cierre, test_productos y
test_qr_access, con `--settings=config.recepcion_test_settings --noinput` y
`MASISCAM_TEST_POSTGRES` apuntando únicamente al clúster QA. Ejecución completa:
269 casos en 662,433 s; 268 aprobados y un error por falta de pypdf, sin fallos
de aserción ni omisiones. Tras instalar pypdf, se repitieron los seis casos de
lectura PDF de recepción, técnica, reparación, calibración y cierre: 6/6 aprobados
en 28,830 s. Los 269 casos seleccionados quedan verificados, sin errores pendientes.
Se incluyen pruebas de concurrencia real, descargas, aislamiento, asignaciones,
autoría independiente, sesiones, roles históricos y bloqueo del admin para personal
TECNICO. `git diff --check` correcto. No se repitió toda la suite innecesariamente.

Archivos modificados (incluye mejoras previas de esta revisión): access.py,
forms.py, views.py, usuario_views.py, test_clientes.py, test_usuarios.py y
plantillas clientes.html, cliente_detalle.html, ficha_form.html, usuarios.html;
todos bajo `sistema/masiscam/`. Único documento actualizado: este archivo.
Sin commits, push, despliegue ni cambios en aprobaciones pendientes.

Estado: corrección RBAC validada y lista para revisión/publicación; no desplegada.
