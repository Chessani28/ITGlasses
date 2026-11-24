# Phase 1C — Conversational Intelligence and User Experience Improvements

## Resumen de cambios

Se implementaron mejoras conversacionales en ITGlasses manteniendo la arquitectura FastAPI, los dos modos de chat y las salvaguardas RAG.

## Enfoque de reconocimiento de intención

Se creó `backend/app/knowledge/social.py` con detección determinística (regex) de saludos, agradecimientos, preguntas de estado y despedidas. Solo se activa para mensajes cortos (≤60 caracteres) para no interferir con preguntas procedimentales. No requiere llamadas LLM.

## Nuevo comportamiento ante evidencia insuficiente

La función `missing()` ahora distingue:
- Preguntas sobre procedimientos de empresa → mensaje claro indicando que no se pudo verificar el procedimiento oficial.
- Preguntas generales sin evidencia → sugiere usar el Chat general.

Se preservan los códigos de estado internos (`insufficient_evidence`) y las reglas de autorización.

## Implementación de conversación contextual

Se añadió seguimiento de `last_topic` y `last_document_id` en la sesión. Las preguntas de seguimiento (ej. "¿Y si eso no funciona?") heredan el tema anterior y se combinan con él para mejorar la recuperación. Se evita mezclar información de manuales no relacionados.

## Enfoque de normalización de voz

Se creó `backend/app/voice/normalize.py` con `normalize_for_speech()` que:
- Elimina emojis y símbolos decorativos
- Remueve formato Markdown (bold, italic, code, headers, listas)
- Maneja URLs y citas (convierte IDs de cita a "referencia")
- Preserva comandos, códigos de error, nombres de archivo, números y terminología técnica

La normalización se aplica en el endpoint `/synthesize` antes de enviar al TTS, sin modificar la respuesta escrita original ni las citas.

## Cambios en frontend

Se añadió una descripción del modo bajo el selector de modo:
- Conocimiento: "Consulta procedimientos y respuestas basadas en los manuales disponibles."
- Chat general: "Conversa con ITGlasses y consulta temas generales."

## Archivos modificados

- `backend/app/knowledge/social.py` (nuevo)
- `backend/app/knowledge/service.py` (social intent, missing mejorado, contexto)
- `backend/app/voice/normalize.py` (nuevo)
- `backend/app/voice/routes.py` (normalización TTS)
- `frontend/index.html` (descripción de modo)

## Resultados de pruebas automatizadas

Pendiente de ejecución.

## Resultados de pruebas de integración real

Pendiente.

## Limitaciones restantes

- El seguimiento contextual es heurístico basado en patrones; puede no capturar todos los casos de seguimiento natural.
- La normalización de voz es basada en regex; casos edge complejos podrían no cubrirse.
- No se implementó acción de navegación automática al Chat general (requiere confirmación del usuario).