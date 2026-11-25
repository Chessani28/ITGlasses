# CLAUDE.md — ITGlasses (backend + web)

## Qué es
Asistente para lentes Ray-Ban Meta. Este repo tiene el **backend FastAPI (Python 3.13)** y la **web** (HTML/CSS/JS
vanilla servido por FastAPI, sin build). Responde con conocimiento general (Ollama + qwen3:4b-instruct), manuales de la
empresa (RAG con SQLite/FTS5 + embeddings qwen3-embedding:0.6b) y búsqueda web. Voz: Whisper (STT, GPU) y Piper (TTS, CPU).
Tiene usuarios y roles (superadmin, admin, usuario), historial de chats por usuario/dispositivo y emparejamiento de
celulares. La app Android está en otro repo: `D:\ITGlasses-Android`. Beta local: el servidor es la PC del trabajo,
publicado en la LAN con Caddy + HTTPS (CA piloto).

## Estructura
- `backend/run.py` — punto de entrada. `backend/app/main.py` arma la app (`create_app(provider=...)`).
- `backend/app/api` — rutas base, esquemas y autenticación por token.
- `backend/app/assistant` — orquestación del chat, contexto, búsqueda web, procedimientos, texto para voz.
- `backend/app/knowledge` — ingesta de documentos, embeddings, recuperación y grounding (RAG).
- `backend/app/history` — conversaciones persistentes, resúmenes, gestión y purga.
- `backend/app/users` — cuentas, roles, sesiones, auditoría, registro.
- `backend/app/pairing` — emparejamiento/aprobación de dispositivos Android.
- `backend/app/providers` — `mock` y `ollama` (sin fallback silencioso entre ellos).
- `backend/app/voice` — STT/TTS (Whisper, Vosk, Piper).
- `frontend/` — la web (`index.html`, `app.js`, módulos `.mjs`).
- `scripts/` — PowerShell para arrancar/parar, crear superadmin, recuperar acceso, token, respaldos, config de Caddy.
- `tests/` — pytest (`test_*.py`), pruebas JS de Node (`*.test.mjs`) y smoke tests reales (`smoke_*.py`).
- `docs/` — guías: `interface.md`, `knowledge.md`, `voice-assistant.md`, `users.md`, `history.md`, `progress.md`.
- `config.toml` — proveedor, modelo, límites. `runtime-config.psd1` — config local (ignorada; ejemplo en `scripts/`).
- No versionados: `.venv/`, `.cache/`, `.runtime/`, `storage/`, `documents/`, `work/`, `logs`.

## Cómo correrlo (PowerShell, desde D:\ITGlasses)
```powershell
Set-Location D:\ITGlasses
.\scripts\check-resources.ps1
.\scripts\start-ollama.ps1                       # terminal 1
.\scripts\start-backend.ps1 -Provider ollama     # terminal 2  (o -Provider mock sin modelo)
# Sin bloquear la terminal (reutiliza un backend sano si ya hay uno):
.\scripts\ensure-backend.ps1 -Provider ollama
.\scripts\stop-backend.ps1
```
Web en http://127.0.0.1:8000/ y docs de la API en http://127.0.0.1:8000/docs. No arranques servicios duplicados:
antes de iniciar, revisa si ya hay uno corriendo (`Invoke-RestMethod http://127.0.0.1:8000/health`).

## Cómo probarlo
```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'; $env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\ITGlasses\.cache\pip'
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.cache/pytest
.\.venv\Scripts\python.exe -m pip check
node --test "tests/*.test.mjs"
```
Las pruebas automáticas usan mocks: no necesitan Ollama, GPU ni modelos. Para un archivo: `... -m pytest -q tests\test_users_web.py`.
Los `smoke_*.py` y `test_voice_real.py` usan modelos reales y el puerto 8000: no los corras sin preguntar.
Con el backend arriba: `Invoke-RestMethod http://127.0.0.1:8000/api/v1/status`.

## Reglas de trabajo
1. **Ramas:** trabaja siempre en una rama nueva `feature/...`, nunca en `master`. Créala desde la rama que yo indique;
   si no digo nada, desde la rama actual (el trabajo real va adelante de `master`). Si hay cambios sin commitear que no
   son tuyos, avísame antes de cambiar de rama.
2. **Secretos:** no toques `.runtime\api-token.dpapi` ni otros secretos: `.runtime\`, `runtime-config.psd1`, `.env`,
   certificados y llaves (`C:\ITGlasses-Caddy\certs`, la CA piloto y su contraseña), contraseñas y tokens. No los leas,
   copies, muestres ni subas.
3. **Instalaciones:** no instales nada sin preguntarme (pip, npm, modelos de Ollama/Whisper/Piper, herramientas, drivers).
4. **Git destructivo:** nunca uses `reset --hard`, `clean` ni `force` (incluye `push --force` y `checkout -f`).
   No muevas, borres ni reescribas tags (`demo-lentes-*`, `voz-rapida-*`, `usuarios-*`, `historial-*`) ni los APK de
   `D:\ITGlasses-APKs`.
5. **No romper lo que funciona:** no cambies Caddy, certificados, puertos ni la config de red sin preguntarme.
6. **Pruebas:** antes de terminar una tarea corre las pruebas de arriba (pytest + node) y arregla lo que falle. Si algo ya
   fallaba antes de tu cambio y no tiene que ver con la tarea, no lo "arregles" a ciegas: dímelo.
7. **Al terminar:** cuando todo pase, haz commit y push de la rama (`git push -u origin feature/...`, sin force) y dame un
   resumen corto de lo que cambiaste y el resultado de las pruebas.
8. Responde en español y de forma concisa. Los textos que ve el usuario en la web van en español.
