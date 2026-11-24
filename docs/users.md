# Usuarios y roles

Guía de operación de la web con usuarios (rama `feature/users`, etapas 1 a 7). La web solo se abre desde
la PC del servidor (`http://127.0.0.1:8000`); Caddy publica únicamente las rutas de los celulares.

## Roles

| Acción | Superadmin | Admin | Usuario |
|---|:-:|:-:|:-:|
| Chat, asistente, voz, ver una fuente citada | ✓ | ✓ | ✓ |
| Mis dispositivos | ✓ | ✓ | ✓ |
| Subir, autorizar, retirar, borrar y reindexar manuales | ✓ | ✓ | ✗ |
| Panel de dispositivos: aprobar, bloquear, borrar, asignar | ✓ | ✓ | ✗ |
| Crear, desactivar, desbloquear y restablecer **usuarios** | ✓ | ✓ | ✗ |
| Crear administradores y cambiar roles (usuario ↔ admin) | ✓ | ✗ | ✗ |
| Ajustes del sistema (diagnóstico de voz) | ✓ | ✗ | ✗ |
| Auditoría | completa | usuarios, dispositivos y documentos | ✗ |

- Nadie administra desde la web a un superadmin ni a sí mismo. La contraseña propia se cambia desde el
  menú de usuario; una cuenta de superadmin solo se recupera con `scripts\reset-access.ps1`.
- Siempre queda al menos un superadmin activo.
- **Privacidad (decisión del 23/11):** cuando llegue el historial de chats, el admin verá solo metadatos
  (cuántas conversaciones, fechas, dispositivo) y nunca el contenido. El superadmin tampoco tendrá acceso
  al contenido; si un cliente lo pide, se agregaría caso por caso, con motivo obligatorio y auditado.

## Primer superadmin y recuperación del acceso

Solo desde la consola de esta PC; nunca desde la web ni con contraseña por defecto.

```powershell
.\scripts\create-superadmin.ps1                      # una sola vez; asigna los dispositivos ya aprobados
.\scripts\reset-access.ps1 -List                     # usuarios, rol, estado, bloqueo y último acceso
.\scripts\reset-access.ps1 -Username laura         # nueva contraseña (cierra sus sesiones y desbloquea)
.\scripts\reset-access.ps1 -Username ana -Temporary  # contraseña temporal: deberá cambiarla al entrar
.\scripts\reset-access.ps1 -Username laura -Unlock # quitar el bloqueo por intentos
.\scripts\reset-access.ps1 -Username laura -Enable # reactivar una cuenta desactivada
```

Las contraseñas se piden sin mostrarlas y nunca se imprimen ni se registran. Cada cambio queda en la
auditoría como «Script local». El backend puede seguir corriendo.

## Contraseñas, sesiones y bloqueo

- Hash `scrypt` de la librería estándar (n=2^15, r=8, p=1, sal de 16 bytes); los parámetros se guardan
  con cada hash para poder subir el costo después.
- Longitud de `ITGLASSES_PASSWORD_MIN_LENGTH` (5 por defecto, de 5 a 256) hasta 256 caracteres, sin
  reglas de composición; no puede ser solo espacios. **Súbela antes de publicar la web a otras PCs.**
- Sesión del lado del servidor: cookie `itg_session` (`HttpOnly; Secure; SameSite=Strict`), de la que la
  base solo guarda el SHA-256. Caduca por inactividad (`ITGLASSES_SESSION_IDLE_MINUTES`, 30) y de forma
  absoluta (`ITGLASSES_SESSION_MAX_HOURS`, 12). Cambiar la contraseña, desactivar o cambiar el rol cierra
  todas las sesiones del usuario.
- Todo lo que modifica exige el `Origin` de la página y el token CSRF de la sesión (`X-CSRF-Token`).
- 5 fallos seguidos bloquean la cuenta 15 minutos, y cada bloqueo siguiente dura el doble, hasta 24 h.
  20 fallos desde una misma IP en 15 minutos bloquean esa IP. El mensaje es siempre el mismo.
- Con un valor inválido en cualquiera de estas variables, el backend no arranca y dice por qué.

## Acceso sin login y tokens compartidos

| Variable | Clave en `runtime-config.psd1` | Valores |
|---|---|---|
| `ITGLASSES_LOOPBACK_MODE` | `LoopbackMode` | `off` (por defecto desde el historial): nada sin login. `chat`: sin login, solo chat y voz, sin guardar. `legacy`: sin login, todo como antes de los usuarios. |
| `ITGLASSES_BEARER_SCOPE` | `BearerScope` | `mobile` (por defecto): los tokens de `ITGLASSES_API_TOKENS` (app v1) solo llegan a las rutas del celular. `full`: llegan a todo, como antes. |

Los celulares v3 (DPoP) no dependen de ninguno de los dos. El valor efectivo queda en el log al arrancar:
`access loopback_mode=… bearer_scope=…`.

## Base de datos, respaldo y restauración

Los usuarios, las sesiones web y la auditoría viven en `storage/pairing/devices.sqlite3` (esquema v2),
junto con los dispositivos. Antes de migrar una base existente se guarda una copia verificada en
`storage/pairing/backups/devices-v<N>-<fecha>.sqlite3`; si la copia falla, no se migra.

```powershell
.\scripts\stop-backend.ps1
.\scripts\restore-pairing-backup.ps1 -List
.\scripts\restore-pairing-backup.ps1 storage\pairing\backups\devices-v1-....sqlite3   # pide escribir RESTAURAR
```

Antes de reemplazarla, la base actual se guarda como `…-pre-restore-…`. Una base v1 restaurada se vuelve
a migrar al arrancar el código nuevo: para quedarse en v1 hay que volver también al código anterior
(tag `voz-rapida-v3`).

## Auditoría

Se registran las acciones sobre usuarios (crear, editar, activar, desactivar, desbloquear, roles,
contraseñas), dispositivos (aprobar, rechazar, bloquear, desbloquear, borrar, asignar) y manuales (subir,
actualizar, autorizar, retirar, borrar, reindexar), además de los inicios de sesión, los bloqueos y la
creación del superadmin. Cada entrada guarda quién, qué, cuándo, la IP y el resultado. Nunca guarda
contraseñas, tokens, hashes ni el nombre escrito en un login fallido de un usuario inexistente. Es de
solo lectura (página «Auditoría» y `GET /api/v1/admin/audit`).

## API

| Ruta | Quién |
|---|---|
| `POST /api/v1/auth/login`, `POST /logout`, `GET /me`, `POST /password`, `GET /config` | web de esta PC |
| `GET, POST /api/v1/admin/users`; `PATCH /admin/users/{id}`; `POST …/{id}/reset-password`, `…/unlock`, `…/role` | admin o superadmin con sesión |
| `POST /api/v1/admin/devices/{id}/assign`; `…/approve` acepta `{user_id}` opcional | admin o superadmin con sesión |
| `GET /api/v1/admin/audit?limit=&before_id=` | admin o superadmin con sesión |
| `GET /api/v1/me/devices` | cualquier usuario con sesión |

La tabla de permisos de las rutas protegidas está en `backend/app/users/access.py`. Una prueba verifica
que coincida exactamente con las rutas registradas.

## Lista de aceptación

Automática: `.venv\Scripts\python -m pytest -q` (incluye `tests/test_users_acceptance.py`, el flujo completo
de punta a punta) y `node --test "tests/*.test.mjs"`. Manual, después de reiniciar el backend:

1. El log dice `access loopback_mode=off bearer_scope=mobile` (o el valor configurado).
2. `http://127.0.0.1:8000`: aparece la pantalla de login, sin la opción de continuar sin sesión. Con
   `LoopbackMode = 'chat'`, sin sesión solo se ven Conversaciones y Estado del sistema, y el chat responde.
3. Con el superadmin: Documentos, Mis dispositivos, Usuarios, Dispositivos y usuarios, Auditoría y el panel de
   dispositivos.
4. Un usuario nuevo recibe una contraseña temporal, debe cambiarla al entrar y solo ve su parte.
5. Asignar un dispositivo aparece en Mis dispositivos del usuario y en la Auditoría.
6. Celular v3: pregunta escrita, por voz y con los lentes y el celular bloqueado. App v1: pregunta escrita y por voz.

## Cómo revertir

| Si falla… | Sin tocar código | Con git |
|---|---|---|
| La web o los manuales | `LoopbackMode = 'chat'` o `'legacy'` y reiniciar el backend | `git revert` de la etapa |
| La app v1 (token compartido) | `BearerScope = 'full'` y reiniciar el backend | — |
| La app v3 (DPoP) | no hay interruptor: los usuarios no cambiaron esa ruta | volver a `voz-rapida-v3` y restaurar el respaldo v1 |

## Pendiente (etapas futuras)

1. ~~Historial de chats~~: hecho, ver [history.md](history.md); `LoopbackMode` pasó a `off`.
2. **Publicar la web por HTTPS en Caddy** para otras PCs, después del historial: subir antes
   `PasswordMinLength`, emitir el certificado para el nombre o IP de la web y revisar el bloqueo por IP
   detrás del proxy.
