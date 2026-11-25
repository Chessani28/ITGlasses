# Examen del cerebro

Mide el asistente con preguntas reales y revisiones automáticas. Corre en proceso: no levanta servidor, no usa el
puerto 8000 (solo consulta `/health` para avisar si el backend está activo) y nunca escribe en `storage/` (usa una copia
del índice de manuales en `.cache/eval/`, que borra al terminar).

```powershell
Set-Location D:\ITGlasses
.\scripts\run-eval.ps1 --provider mock              # prueba el corredor, sin modelo ni internet
.\scripts\run-eval.ps1                              # examen real: Ollama, manuales y búsqueda web
.\scripts\run-eval.ps1 --category voz               # una categoría (repetible)
.\scripts\run-eval.ps1 --case voz-dns               # un caso (repetible)
.\scripts\run-eval.ps1 --compare evals\results\baseline.json
```

El reporte queda en `evals\results\<fecha-hora>.json` y `.md` (no versionados): aciertos totales y por categoría,
mediana y p90 de latencia por turno, casos fallidos con su motivo, y qué cambió contra la corrida anterior (por defecto
la última). Si el backend real estaba activo, el reporte lo avisa: alguien usando los lentes a la vez altera las
latencias.

## Casos

Cada archivo de `evals/cases/` es una lista de casos. `evals/cases/local/` no se versiona: ahí van los casos que
citan contenido interno de los manuales de la empresa.

```json
{"id": "voz-dns", "category": "voz", "channel": "voice", "turns": [
  {"message": "¿Qué es el DNS?", "expect": {"route": "general", "max_sentences": 3, "ends_with_offer": true}},
  {"message": "sí", "expect": {"min_chars": 300}}]}
```

- `channel`: `text` (web, por defecto) o `voice` (celular/lentes).
- Cada caso corre en una sesión nueva; un turno sin `expect` solo prepara contexto.
- Revisiones (todas opcionales; texto comparado sin acentos ni mayúsculas):
  `route`, `contains_all`, `contains_any`, `not_contains`, `max_chars`, `min_chars` (sobre la respuesta);
  `max_speech_chars`, `max_sentences`, `ends_with_offer` (sobre lo que se oiría: `speech` o la respuesta);
  `has_sources`, `source_document`, `steps_verbatim` (manuales: cada paso numerado está literal en el documento);
  `web_used` (alguna fuente con URL).
- Un caso mal escrito (clave desconocida, sin turnos, id repetido) sale en "Errores de carga" y el resto corre igual.
- Nunca pongas en un caso contraseñas, tokens ni llaves, aunque aparezcan en un manual.
