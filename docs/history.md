# Historial de conversaciones

Rama `feature/chat-history`, seis etapas sobre `usuarios-v1`. Cada conversación tiene dueño: se guarda por
usuario y por dispositivo, sobrevive a los reinicios y solo la ve quien la tuvo.

## Qué se guarda y de quién es

| Desde | Dueño | Cómo se agrupa |
|---|---|---|
| La web con sesión iniciada | el usuario de la sesión | una conversación por cada «Nueva conversación» |
| Celular o lentes (app v3, DPoP) | el dueño del dispositivo (`installs.user_id`) | una por dispositivo mientras el último mensaje tenga menos de 30 minutos; la app abierta y el modo lentes comparten la misma |
| Sin sesión, tokens compartidos (app v1) o un dispositivo sin dueño | nadie | no se guarda; funciona como antes, solo en memoria |

Se guarda solo texto, nunca audio: la pregunta, la respuesta, la ruta (general, manuales, social), las fuentes
citadas y el estado del asistente, con el que el contexto sigue después de un reinicio. El asistente recibe los
intercambios generales anteriores como turnos reales, dentro de `num_ctx` (`ITGLASSES_CHAT_CONTEXT=turns`;
`prompt` vuelve al contexto pegado como texto).

## Privacidad

- Un usuario ve, abre, renombra, borra y restaura **solo sus** conversaciones. La de otro responde igual que
  una que no existe (404).
- Un administrador ve en «Usuarios» solo **metadatos**: cantidad de conversaciones y de mensajes, web o
  dispositivo, borradas pendientes, fechas y dispositivos. Nunca títulos ni mensajes. El superadmin, igual.
- La auditoría registra borrados, restauraciones y purgas con ids y conteos, nunca títulos ni mensajes.
- Desde esta etapa, sin sesión esta PC no puede usar nada (`LoopbackMode = 'off'`, ver abajo).

## Borrar, restaurar y purgar

- **Borrar** es lógico: la conversación desaparece al momento para su dueño y para la memoria del asistente.
  Si era de un dispositivo, su siguiente mensaje empieza una conversación nueva.
- **Restaurar** es posible durante la gracia (`ITGLASSES_HISTORY_DELETED_GRACE_DAYS`, 7 días). Una de dispositivo
  vuelve a la lista, pero el dispositivo no la retoma.
- **Purga**: al arrancar y cada 6 horas se eliminan, con sus mensajes, las borradas cuya gracia terminó y las
  que llevan más de `ITGLASSES_HISTORY_RETENTION_DAYS` (90) sin mensajes. Lo purgado se sobrescribe en el
  archivo (`secure_delete`). Los respaldos diarios conservan su copia hasta que rotan (7 días).

## Configuración

| Variable | Por defecto | Valores |
|---|---|---|
| `ITGLASSES_HISTORY` | `on` | `off` no guarda nada nuevo (lo guardado sigue visible) |
| `ITGLASSES_HISTORY_DB` | `storage/history/history.sqlite3` | otra ruta |
| `ITGLASSES_DEVICE_CONVERSATION_GAP_MINUTES` | 30 | 5 a 1440 |
| `ITGLASSES_HISTORY_RETENTION_DAYS` | 90 | 0 (sin límite por antigüedad) a 3650 |
| `ITGLASSES_HISTORY_DELETED_GRACE_DAYS` | 7 | 0 (se purga en la siguiente limpieza) a 90 |
| `ITGLASSES_CHAT_CONTEXT` | `turns` | `prompt` |
| `ITGLASSES_DAILY_BACKUP` / `ITGLASSES_DAILY_BACKUP_KEEP` | `on` / 7 | `off` / número de copias |
| `ITGLASSES_LOOPBACK_MODE` (`LoopbackMode`) | `off` | `chat` (sin sesión, chat y voz sin guardar), `legacy` |

Con un valor inválido el backend no arranca y dice por qué. Al arrancar, el log muestra
`chat_history enabled=… device_gap_min=… chat_context=…`, `history_retention retention_days=… deleted_grace_days=…`
y `access loopback_mode=… bearer_scope=…`.

## Respaldos y restauración

La base del historial es un archivo propio, separado del de dispositivos y usuarios. Antes de cualquier
migración se guarda una copia verificada. Además hay un respaldo diario (7 copias) de ambas bases en
`storage/backups/`, y uno manual:

```powershell
.\scripts\backup-data.ps1                      # respaldo ahora (dispositivos e historial)
.\scripts\backup-data.ps1 -List
.\scripts\stop-backend.ps1
.\scripts\restore-pairing-backup.ps1 -History -List
.\scripts\restore-pairing-backup.ps1 -History storage\backups\...\history.sqlite3   # pide escribir RESTAURAR
```

Resumen solo de metadatos, sin contenido: `.venv\Scripts\python -m backend.app.history.summary`.

## En la web

- Panel «Historial» junto al chat. Permite filtrar por canal y dispositivo; abrir y continuar (también las
  del celular: lo que escribas se agrega a ellas); renombrar; borrar, con «Deshacer»; «Ver borradas» y
  «Restaurar»; y «Ver más».
- Debajo del cuadro de texto, una línea dice si la conversación se guarda y quién puede verla.
- «Mis dispositivos»: «Nueva conversación en este dispositivo».
- Voz: si Whisper no puede usarse en un turno (memoria de la GPU, GPU no disponible, no configurado), la web
  repite con Vosk y muestra el motivo. La app actual muestra «Motor de voz básico»; el motivo le llega en
  `notice` para cuando una versión de la app lo muestre.

## API

| Ruta | Quién |
|---|---|
| `GET /api/v1/conversations?channel=&device_id=&deleted=&limit=&cursor=` | usuario con sesión: las suyas |
| `GET`, `PATCH {title}`, `DELETE /api/v1/conversations/{id}` | usuario con sesión: solo la suya |
| `POST /api/v1/conversations/{id}/restore` | usuario con sesión, durante la gracia |
| `POST /api/v1/me/devices/{id}/new-conversation` | dueño del dispositivo |
| `GET /api/v1/admin/conversations/stats` | admin o superadmin: metadatos |

Todas piden sesión iniciada en esta PC; los cambios piden además el `Origin` de la página y el token CSRF.
Caddy no las publica.

## Lista de aceptación

Automática: `.venv\Scripts\python -m pytest -q` (incluye `tests/test_history_acceptance.py`, el flujo completo)
y `node --test "tests/*.test.mjs"`. Manual, después de reiniciar el backend:

1. El log dice `access loopback_mode=off`. En `http://127.0.0.1:8000` aparece el login, sin la opción de
   continuar sin sesión.
2. Con un usuario: preguntar, continuar, abrir la conversación desde el historial, renombrar, borrar,
   deshacer y restaurar.
3. Celular v3: preguntar desde la app y luego por voz; las dos quedan en una sola conversación del
   dispositivo, visible en la web del dueño.
4. «Nueva conversación en este dispositivo»: la siguiente pregunta del celular empieza otra.
5. Admin: la columna «Historial» de «Usuarios» muestra conteos, sin títulos. En la Auditoría aparecen el
   borrado y la restauración.
6. Reiniciar el backend: la conversación sigue y el asistente recuerda el tema.
7. Con los lentes y el celular bloqueado: la pregunta llega a la misma conversación del dispositivo.

## Cómo revertir

| Si falla… | Sin tocar código | Con git |
|---|---|---|
| La web sin sesión era necesaria | `LoopbackMode = 'chat'` (o `'legacy'`) y reiniciar | — |
| Guardar el historial | `ITGLASSES_HISTORY=off` y reiniciar: todo funciona como antes, en memoria | `git revert` de la etapa |
| El contexto como turnos | `ITGLASSES_CHAT_CONTEXT=prompt` | — |
| La purga | `ITGLASSES_HISTORY_RETENTION_DAYS=0` (la de borradas sigue) | — |
| La base del historial | restaurar un respaldo con `restore-pairing-backup.ps1 -History` | — |

## Pendiente

- Panel de historial: tiene scroll horizontal y los títulos se cortan.
- App Android: mostrar `notice` (el motivo de usar Vosk). Requiere recompilar.
- Probar con los lentes y el celular bloqueado, cuando estén disponibles.
- Publicar la web por HTTPS en Caddy para otras PCs: subir antes `PasswordMinLength`.
