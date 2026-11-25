# Respuestas de manuales en español con frases del manual — diseño corto

Fecha: 2026-11-25 · Rama: `feature/respuestas-manuales` (desde `feature/aviso-manuales`) · Estado: borrador

## Intención

Cuando la pregunta a un manual no pide pasos ("¿qué etiquetas se permiten?", "¿cuánta RAM?"), el asistente devuelve
hoy `reserve_answer`: los pedazos del manual pegados en inglés, con números de página sueltos ("Según la
documentación autorizada: 39 / 4.3 For Dominos CS / …"). En los lentes se oye mal y en la web se ve poco profesional.

**Objetivo:** una respuesta corta en español, hecha solo de frases del manual (traducidas con los candados que ya
existen), con su fuente y página. Se mantiene la regla de seguridad: el modelo nunca redacta lo que dice el manual.

## Enfoques

- **A. El modelo elige frases por número y se traducen con candados (elegido por el dueño).** Cada frase es
  rastreable al manual; si su traducción no pasa los candados, se muestra la original en inglés.
- B. Resumen redactado por el modelo y validado: más natural, pero rompe la regla (un validador no atrapa un matiz
  cambiado). Descartado.
- C. Solo limpiar el extracto: sigue en inglés. Descartado (se usa como respaldo de A).

## Diseño

**Frases candidatas (knowledge, determinista)**
- `knowledge/excerpts.py`: `sentences(rows) -> list[{"id": int, "text": str, "page": int|None}]` a partir de los
  pedazos de evidencia ya validados: quita portadas e índices (`is_cover_page`, `is_index_page`), líneas que son solo
  un número de página, bloques "Review History / Version / Reviewer", viñetas (`➢ • ▪ -`) y espacios repetidos;
  separa en frases (`. ! ?` o fin de línea de una viñeta). Máximo 40 frases, cada una ≤ 400 caracteres.
- `clean_excerpt(rows) -> str`: las mismas frases unidas (respaldo C), para cuando falla la selección.

**Selección (modelo, solo números)**
- `EvidenceSelector.pick_sentences(question, sentences) -> list[int] | None`: `/api/chat` con salida JSON
  `{"ids": [str]}`, temperatura 0, `num_predict` 64. Pide las frases (máx. 3) que responden la pregunta; trata
  pregunta y frases como datos no confiables. Solo sobreviven ids existentes; error o lista vacía → `None`.

**Respuesta del servicio de conocimiento**
- En modo `information` (no `steps`, no `identify`), en lugar de `reserve_answer`: si hay frases elegidas,
  `KnowledgeAnswer.excerpts = [{"text", "page"}]` (en orden del documento) y `answer` = esas frases en inglés con la
  fuente (para la ruta `/api/v1/knowledge/chat`, que no traduce). Si no, `answer = clean_excerpt(...)` y `excerpts`
  vacío. Las fuentes, la reautorización y el resto del flujo no cambian.
- `KnowledgeAnswer` gana `excerpts: list[dict] = []` (opcional, compatible).

**Respuesta del asistente**
- Donde hoy usa `result.answer` para una respuesta de información: si `result.excerpts`, compone
  `"Según «<título>», p. <página>: " + " ".join(_translate(frase))`; cada frase pasa por el `_translate` existente
  (candados de números, nombres propios, polaridad e instrucciones añadidas); si se rechaza, queda la original en
  inglés. Sin `excerpts`, como hoy.
- En voz (`voice_mode.voice_speech`) se lee tal cual: ya es corto y no lleva listas.

**Fuera de alcance:** procedimientos y pasos (ya se leen del catálogo), la ruta de conocimiento de la web
(`/knowledge/chat`) más allá de mostrar las frases limpias en inglés, y resumir varias páginas.

**Medición:** examen completo. Éxito: manuales ≥ 24/30 sin bajar `man-no-cubre-*`, y ninguna respuesta de manuales
con "Según la documentación autorizada:" seguido de un número de página suelto.

**Pruebas (pytest, sin Ollama)**
- `sentences`: quita portada, índice, número de página suelto, historial de revisión y viñetas; parte frases; límites.
- `pick_sentences`: ids válidos → lista; id inexistente, JSON inválido, error HTTP → `None`; temperatura 0.
- `KnowledgeService.query` en modo información: con selección → `excerpts` en orden del documento y `answer` sin
  "Según la documentación autorizada"; sin selección → extracto limpio; en modo pasos no cambia.
- Asistente: con `excerpts` compone en español con fuente y página; una traducción rechazada por los candados deja
  la frase original; sin `excerpts`, igual que hoy.
