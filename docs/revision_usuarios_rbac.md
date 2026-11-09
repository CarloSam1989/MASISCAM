# Revisión local de usuarios y RBAC — 2026-11-09

Ruta: `/app/usuarios/`. Roles existentes: ADMIN, OFICINA, TECNICO, PLANTA,
GERENCIA, CLIENTE, CONSULTA y TALLER. TALLER se añade por autorización del propietario;
no se convierten usuarios existentes.

Matriz efectiva tras la corrección (sujeta a empresa, estado, autoría y asignación):

| Ventana / operación | Acceso efectivo encontrado |
| --- | --- |
| Usuarios | ADMIN; superusuario técnico heredado |
| Dashboard | Internos autorizados; CLIENTE con alcance propio; TALLER redirige a su inicio exclusivo |
| Crear equipos operativamente | Solo TALLER; ADMIN conserva recuperación administrativa previamente autorizada |
| Crear/editar clientes | ADMIN y OFICINA, en su empresa; TECNICO denegado |
| Consultar clientes: TECNICO | Clientes contractuales de expedientes con asignación vigente, empresa y propietario coherentes |
| Equipos y QR heredados | Según permisos generales y filtros de objeto existentes |
| Recepción: leer / escribir | ADMIN, CONSULTA, OFICINA, PLANTA, GERENCIA / OFICINA; TALLER solo sus ingresos |
| TALLER: equipos y expedientes | Equipos ingresados por el usuario, recepción propia y estado; sin ejecución técnica ni acceso comercial |
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

Actualización TALLER: migración aditiva `0030_rol_taller_ingreso`, con
`Equipo.registrado_por` nullable y no editable. Los históricos quedan sin vínculo
automático. La creación operativa exige TALLER en vistas y formularios/servicios,
sin bypass de superusuario; la ruta antigua redirige al registro único y rechaza
su POST. Menú exclusivo: Inicio de taller (`/app/taller/`), Registrar equipo /
recepción, Mis expedientes, Consultar estado y Mi cuenta/cerrar sesión. Solo
se permiten búsquedas de clientes existentes por RUC dentro de su empresa.
La recepción y las guías reutilizan Fase 2 con autorización sobre el objeto;
las operaciones técnicas/comerciales de fases 3–6 siguen bloqueadas para TALLER.
No se crean cuentas ni asignaciones técnicas automáticamente.

Archivos de esta actualización: `accounts/admin.py`, `accounts/views.py`;
en `masiscam/`: `admin.py`, `access.py`, `models.py`, `forms.py`, `views.py`, `taller_views.py`,
`recepcion_services.py`, `recepcion_confirmacion.py`, `recepcion_forms.py`,
`recepcion_views.py`, `usuario_forms.py`, `usuario_views.py`, `urls.py`,
`templatetags/masiscam_navigation.py`, migración 0030; plantillas `base`,
`ficha_form`, `producto_listado`, `proyecto_detalle`, `recepcion_listado` y
`taller_inicio/estado/cuenta`; pruebas `test_taller`, `test_clientes`,
`test_productos`, `test_drive_equipo`. Único documento actualizado: este archivo.

Validación TALLER en PostgreSQL 16.4: regresión inicial de 301 casos, con 297
aprobados y cuatro errores en la clase nueva por cerrar dos veces una respuesta
PDF en el test y perder su conexión transaccional. Corregido usando el helper
existente `consumir_respuesta`; repetición de los ocho casos: 8/8 aprobados.
Se aprobaron además el recorrido TALLER → TECNICO asignado → PLANTA independiente
y el upgrade 0029 → 0030 sin cambios en equipos/roles históricos. Comprobación
final de módulos afectados: 59/59; control final de Django Admin y regresión de
usuarios: 4/4. Total: 304 casos distintos verificados, sin fallos pendientes.
Incluye fases 2–6, concurrencia real, QR, historial, descargas y bloqueos vigentes.
`check`, `makemigrations --check --dry-run` y `git diff --check` correctos.

TALLER está listo para publicación controlada con la migración 0030 preparada;
no se aplicó a bases reales. Sin producción, Drive real, commits, push o deploy.
