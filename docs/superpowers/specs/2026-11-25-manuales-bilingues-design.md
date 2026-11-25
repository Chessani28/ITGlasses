# Manuales en inglés con preguntas en español — diseño corto

Fecha: 2026-11-25 · Rama: `feature/manuales-bilingues` (desde `feature/examen-modo-voz`) · Estado: borrador

## Intención

Los manuales de la empresa están en inglés y los usuarios preguntan en español, por voz. El examen del cerebro mide
los manuales en 16/30 (con embeddings activos): muchas preguntas en español no pasan el filtro de relevancia o traen el
pedazo equivocado del manual, caen a conocimiento general y el modelo inventa. Además, desde que los embeddings
funcionan, cada pregunta intercambia modelos en Ollama y la latencia subió (conceptos: mediana 1.8 s → 6.3 s).

**Objetivo:** que una pregunta en español encuentre el manual en inglés tan bien como la misma pregunta en inglés, sin
hacer más lento el asistente.

## Diagnóstico (medido)

- 10 preguntas de manuales que fallan: en inglés pasan el filtro 9/10, en español 6/10. En inglés también se elige
  mejor el pedazo (p. ej. "¿cuándo debe estar terminado?" → portada en español, "Delivery on time" en inglés).
- Solo se pasan los 2 mejores pedazos al selector de evidencia: un mal orden da "sin evidencia".
- Los embeddings ya corren en CPU (`num_gpu: 0`), pero `OLLAMA_MAX_LOADED_MODELS=1` cuenta ese modelo y saca al de
  chat de la GPU; y `KnowledgeService.query` descarga los embeddings tras cada búsqueda (`embeddings.unload()`).

## Enfoques

- **A. Traducir la pregunta al inglés y buscar con ambas (elegido).** Sin reindexar. Una llamada corta extra al modelo
  (ya cargado en GPU).
- B. Traducir los pedazos al español al indexar: reindexar, ingesta lenta, riesgo de alterar el texto del manual.
  Descartado.
- C. Bajar umbrales: entran documentos ajenos (los umbrales están calibrados contra falsos positivos). Descartado.

El dueño eligió A y quitar el intercambio de modelos.

## Diseño

**Traducción de la consulta**
- `EvidenceSelector.translate_query(question: str) -> str | None` (`knowledge/grounding.py`): una llamada a
  `/api/chat` con salida JSON `{"english": str}`, `temperature 0`, `num_predict 96`, `num_ctx 2048`, mismo
  `keep_alive` que el selector. El prompt trata la pregunta como dato no confiable, pide solo traducir (no responder) y
  conservar nombres propios, siglas, números y comandos tal cual. Si la pregunta ya está en inglés devuelve lo mismo.
  Cualquier error o timeout (10 s) → `None` y se busca solo con la original. Nunca bloquea ni inventa.
- Se registra en el log solo el resultado (`ok`/`error`) y el tiempo, nunca el texto.

**Búsqueda con ambas**
- `Retriever.search(question, candidates=None, alternates=())`: la puntuación semántica de cada pedazo es el máximo
  sobre la pregunta original y las alternas (ya se hace `max` sobre segmentos); BM25 y `overlap` usan los tokens de la
  original más los de las alternas. Sin alternas: exactamente como hoy.
- `KnowledgeService.query` llama `translate_query` antes de buscar (no para identificadores exactos) y pasa la
  traducción como alterna. Los umbrales (0.55 / 0.42 + overlap ≥ 2) no cambian.
- El selector de evidencia recibe los **4** mejores pedazos del documento elegido (hoy 2) y la pregunta original.

**Sin intercambio de modelos**
- `scripts/start-ollama.ps1`: `OLLAMA_MAX_LOADED_MODELS = '2'`. El modelo de embeddings está en CPU, así que no
  usa VRAM adicional; el de chat se queda en la GPU.
- `KnowledgeService.query` deja de llamar `embeddings.unload()` después de cada búsqueda; el modelo de embeddings se
  queda cargado en RAM (~0.6 GB) con su `keep_alive` de 15 min.
- Requiere reiniciar Ollama con el script (lo hace el builder con permiso del dueño).

**Fuera de alcance**
- La respuesta de "información" sigue siendo el extracto literal del manual (en inglés) cuando no hay pasos: hoy no
  se genera prosa con el modelo a partir de manuales, por decisión de seguridad (`grounding.py`). Mejorar eso (resumir
  en español citando la fuente) es una propuesta aparte.

**Medición**
- Examen completo antes (16/30 manuales, reporte `20261125-…` con embeddings) y después. Éxito: manuales ≥ 22/30 sin
  romper `man-no-cubre-*`, y mediana de conceptos de vuelta a ~2 s.

**Pruebas (pytest, sin Ollama)**
- `translate_query`: JSON válido → traducción; error, timeout o JSON inválido → `None`; payload con `temperature 0`.
- `Retriever.search` con alternas: un pedazo que solo coincide con la alterna sube; sin alternas el orden es el de hoy.
- `KnowledgeService.query`: usa la traducción como alterna, sigue funcionando si la traducción falla, ya no descarga
  embeddings, y pasa hasta 4 pedazos al selector.
- `start-ollama.ps1` fija `OLLAMA_MAX_LOADED_MODELS = '2'`.
