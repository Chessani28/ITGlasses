# Phase 1C Hotfix — Knowledge Chat and Document Answer Quality

## 1. Causa raíz de cada issue

### ISSUE 1 — Saludos ignorados en Knowledge Chat

**Causa raíz:** El backend en ejecución (PID 18484) fue iniciado ANTES de aplicar los cambios de Phase 1C. El proceso cargó el código antiguo en memoria. Además, el regex de saludos original no cubría elongaciones como "Holaaa" (solo coincidía "hola" exacto).

**Corrección:**
- Se actualizó el regex en `backend/app/knowledge/social.py` para aceptar elongaciones: `hola+`, `hey+`, `hi+`, `hello+`.
- Se reinició el backend para cargar el código actualizado.

### ISSUE 2 — Respuestas con chunks crudos

**Causa raíz:** El pipeline RAG de Knowledge Chat es **extractivo por diseño**. La línea 259 de `service.py` construye la respuesta concatenando los chunks recuperados directamente:

```python
answer = "Extractos de la documentación (no se han ejecutado comandos):\n\n" + "\n\n".join(f'[{row["id"]}]\n{row["text"]}' for row in evidence)
```

No se invoca Ollama para generar una respuesta natural. El selector (`selector.select()`) solo decide qué chunks mostrar, no genera texto. Este es un diseño intencional para preservar el grounding documental estricto.

**Cambio más pequeño y seguro para soportar respuestas generativas fundamentadas:**
1. Añadir un paso de generación con Ollama que reciba los chunks seleccionados como contexto.
2. Usar un prompt que instruya al modelo a responder SOLO con la evidencia proporcionada, citar fuentes, y no inventar pasos.
3. Mantener el fallback extractivo si Ollama falla o no está disponible.
4. Validar que la respuesta generada no contenga información no presente en los chunks (post-validación).

### ISSUE 3 — Phase 1C no estaba activa

**Causa raíz:** El proceso backend (PID 18484) se inició el 21/10/2026 a las 11:08:46, antes de aplicar los cambios de Phase 1C. El código nuevo estaba en disco pero no cargado en memoria. El usuario reinició la aplicación pero el proceso anterior persistió o se reinició con caché.

## 2. ¿Phase 1C estaba realmente cargada?

No. El proceso backend servía código antiguo. Tras reiniciar con el código actualizado, los cambios se reflejan correctamente.

## 3. Archivos modificados

- `backend/app/knowledge/social.py` — regex de saludos actualizado para elongaciones
- `backend/app/knowledge/service.py` — (Phase 1C original, ya aplicado)
- `backend/app/voice/normalize.py` — (Phase 1C original, ya aplicado)
- `backend/app/voice/routes.py` — (Phase 1C original, ya aplicado)

## 4. Respuestas API antes/después

### "Holaaa" — Knowledge Chat

**Antes (código antiguo):**
```json
{"status":"insufficient_evidence","answer":"Evidencia insuficiente. No encontré información suficiente en la documentación autorizada y vigente..."}
```

**Después (código actualizado):**
```json
{"status":"answered","answer":"¡Hola! ¿En qué puedo ayudarte? Puedes consultar procedimientos en los manuales disponibles o usar el Chat general para otros temas.","sources":[],"documentation_sufficient":true}
```

### "pasos para la COMPUTER PREPARATION"

**Antes y después (sin cambio — por diseño extractivo):**
```json
{"status":"answered","answer":"Extractos de la documentación (no se han ejecutado comandos):\n\n[68b5179f-...]\n2 \n \n1. COMPUTER PREPARATION \n ..."}
```

## 5. Verificación en navegador

No se pudo automatizar la verificación en navegador (no hay herramienta de browser automation disponible en este entorno). Se verificó mediante API directa. El frontend carga los assets actualizados desde `/ui/`.

## 6. Verificación de invocación real de Ollama

En el pipeline de Knowledge Chat actual, **Ollama NO se invoca para generar la respuesta final**. Solo se usa para:
- Selección de chunks (selector.select())
- Clasificación de intención (intent())

La respuesta final es extractiva (concatenación de chunks). Para soportar respuestas generativas, se requiere añadir un paso de generación con Ollama usando los chunks como contexto.

## 7. Resultados de pruebas de regresión

- 114 tests existentes pasando.
- "Holaaa" → saludo correcto (verificado por API).
- "Holaa" → saludo correcto (verificado por API).

## 8. Limitaciones restantes

- Las respuestas de Knowledge Chat siguen siendo extractivas (chunks crudos). Para respuestas naturales se requiere implementar generación con Ollama (pendiente).
- La normalización de voz es basada en regex; casos edge complejos podrían no cubrirse.
- No se implementó acción de navegación automática al Chat general.

## 9. Instrucciones para reiniciar y verificar

1. Detener el proceso backend anterior:
   ```
   Stop-Process -Id <PID> -Force
   ```
2. Iniciar el backend actualizado:
   ```
   cd D:\ITGlasses
   .venv\Scripts\python.exe -m backend.run
   ```
3. Verificar que el código nuevo está activo:
   ```
   curl -X POST http://127.0.0.1:8000/api/v1/knowledge/chat -H "Content-Type: application/json" -d '{"message":"Holaaa"}'
   ```
   Debe retornar el saludo "¡Hola! ¿En qué puedo ayudarte?"

4. Verificar General Chat:
   ```
   curl -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d '{"message":"Holaa"}'
   ```