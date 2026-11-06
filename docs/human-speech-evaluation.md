# Evaluación piloto con voz humana mexicana

Estado: no hay grabaciones humanas disponibles ni resultados de precisión humana. Los WAV sintéticos existentes no sirven para esta evaluación. El manifiesto declara procedencia; el programa no puede demostrar por sí mismo que una voz es humana.

1. Pida consentimiento para grabar y evaluar voz localmente. Use identificadores como `speaker-01`, sin nombres ni datos personales. Reclute preferentemente tres personas hablantes de español mexicano; registre por separado condiciones silenciosas y con ruido representativo, sin mezclar resultados.
2. Grabe cada frase completa en un archivo distinto, tres repeticiones por frase y persona/condición. Hable naturalmente, con micrófono y distancia constantes, sin TTS, clonación ni reproducción de otra grabación. Conserve una breve pausa antes/después. Exporte WAV PCM de 16 bits, mono, 16000 Hz, entre 0.1 y 30 segundos. No cambie solo la extensión; el evaluador rechaza otros formatos y nunca remuestrea.
3. Lea exactamente estas frases, sin responderlas:
   - ¿Qué es una VPN?
   - ¿Qué es Active Directory?
   - ¿Cuál es la versión más reciente de Windows 11?
   - Según este manual, ¿cuál es el mantenimiento de las computadoras?
   - ¿Y cuál es el segundo paso?
4. Copie `docs/human-speech-manifest.template.json` a `work/human-speech/manifest.json` (cree esa carpeta). La plantilla usa `pending_human_recording`; cambie ese campo a `human_recorded` solo después de grabar y revisar audio humano. Los paths son relativos al manifiesto. Coloque los WAV en `recordings/` con los nombres indicados y confirme `provenance: human_recorded` solamente cuando corresponda. Añada otros hablantes/condiciones con sus propios IDs y archivos; cada combinación hablante/condición debe incluir las cinco frases. No sustituya archivos faltantes por voz sintética. Revise el audio y mantenga la referencia original aunque el reconocedor diga otra cosa.

Desde D:\Itglasses, valide antes de iniciar una prueba:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_human_speech.py --manifest work/human-speech/manifest.json --validate-only
```

La plantilla sin WAV debe fallar, sin solicitudes de red ni resultados inventados. `--dry-run` es equivalente. Con el backend local ya iniciado y autorizado:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_human_speech.py --manifest work/human-speech/manifest.json --base-url http://127.0.0.1:8000 --output work/human-speech/results.json
```

Si el servidor requiere bearer, configure `ITGLASSES_BEARER_TOKEN` mediante su mecanismo local de secretos; `--bearer-env` permite otro nombre. El evaluador no imprime el token. No agregue secretos al manifiesto. Solo admite HTTP loopback, rechaza redirecciones y envía `audio/wav` exclusivamente a `/api/v1/voice/transcribe`; no llama al asistente, RAG, Ollama ni LLM. No instala ni descarga modelos.

El informe conserva referencia, metadatos y respuesta completa: transcripción cruda/normalizada, arreglo `words`, estimaciones de confianza, `needs_clarification` y `quality_reasons`. Incluye SHA-256 de cada WAV, estado HTTP, código de error del backend sin su mensaje y latencia de pared en milisegundos por solicitud. Un error HTTP/transporte falla la ejecución, queda visible y no se convierte en una transcripción vacía exitosa. El código de salida cero significa transporte/formato válido, no aprobación de calidad.

WER se calcula dos veces, comparando la misma referencia original con `raw_text` y `normalized_text`. Ambos cálculos ignoran mayúsculas, acentos y puntuación, pero no reemplazan números ni siglas. Así, “ve pe ene” frente a “VPN” cuenta como errores en crudo. Se reportan S/D/I, número de palabras de referencia y alineación determinista para inspeccionar cada error. Para WER agrupado use suma(S+D+I)/suma(palabras), no promedie porcentajes sin ponderar. Separe quiet/noise, hablante y frase; conserve también los casos fallidos y solicitudes de aclaración.

Criterios propuestos de demostración para fijar antes de grabar (no son un estándar de precisión): para silencio, mínimo tres personas × cinco frases × tres repeticiones; WER normalizado agregado ≤10%, cero errores en VPN, Active Directory, Windows 11, mantenimiento/computadoras y segundo/paso en transcripciones aceptadas; como máximo 10% de aclaraciones y cero fallos HTTP. Presente ruido como una prueba separada con los mismos criterios, indicando nivel/entorno de ruido en una nota del registro. Publique número de casos y distribución de latencia (mediana/p95), con un objetivo previo de p95 ≤5000 ms para esta máquina, sin ocultar fallos. Cualquier cambio de criterio posterior exige una nueva versión del protocolo y una nueva prueba.

Revise siempre `missing_or_changed_key_terms`, `unblocked_wrong_key_terms` y `high_confidence_wrong_key_terms`. Este último marca términos clave ausentes/cambiados con mínimo de estimaciones de palabra ≥0.85; es una alarma heurística, no una probabilidad de corrección ni una auditoría semántica completa. “Segundo” cambiado por “tercero” debe ser un fallo aunque las otras palabras sean correctas. Revise manualmente también adiciones, negaciones y sustituciones que el detector limitado no cubre. Ninguna confianza de Vosk está calibrada como probabilidad.

Este piloto pequeño no prueba generalización estadística ni precisión poblacional mexicana. Registre resultados negativos y no declare mejoras humanas a partir de WAV sintéticos o tests unitarios. Aquí se evalúa reconocimiento aislado: el seguimiento de “segundo paso”, la actualidad de Windows 11 y la corrección de la respuesta al manual requieren evaluaciones independientes del asistente con contexto controlado.

No se recopilan nombres; revise que el audio no contenga datos de empresa o conversaciones ajenas. Guarde las grabaciones en work, que no deben subirse a Git. La lectura de los WAV se realiza solo cuando todos los archivos y metadatos han pasado la validación.

El detector de términos es una alarma literal limitada; revise equivalencias como «paso 2» frente a «segundo paso» y todas las negaciones. El WER conserva esas diferencias de palabras aunque el significado pueda coincidir.
