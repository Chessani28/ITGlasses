# Examen del cerebro y modo voz — diseño

Fecha: 2026-11-25 · Rama base: `feature/device-registration` · Estado: borrador para aprobación

## Intención

ITGlasses se usa al final con lentes Ray-Ban Meta: el usuario pregunta por voz, sin pantalla delante, y escucha la
respuesta. El dueño quiere convertirlo en un producto vendible. Hoy el asistente responde rápido y bien, pero:

1. **No hay forma de medir el cerebro.** Cada cambio (prompt, reglas de ruta, modelo) puede romper otra cosa sin que
   se note, y no hay números que enseñar a un cliente.
2. **Las respuestas generales están escritas para leerse, no para oírse.** Pueden llegar a 768 tokens; la voz lee
   ~300 caracteres y dice "El resto está en el chat", que en los lentes no existe.

Este trabajo hace dos cosas, en este orden: un **examen automático** repetible y un **modo voz** que se mide con ese
examen.

## Objetivos

- Un banco de casos (~100) que cubra manuales, conceptos de TI, internet, social y conversaciones de varios turnos.
- Un script que lo corre contra el cerebro real (Ollama + manuales indexados) y produce un reporte con aciertos por
  categoría, latencia y diferencia contra la corrida anterior.
- Respuestas generales cortas y "para el oído" cuando la pregunta llega de un dispositivo (celular/lentes), con la
  oferta "¿Quieres más detalle?" y una versión larga a demanda.
- La web no cambia de comportamiento.

## No objetivos

- Cambiar el enrutamiento por un clasificador con el modelo (propuesta 4, trabajo posterior; el examen es su base).
- Voz en streaming (empezar a hablar antes de terminar de generar).
- Cambios a la app Android: el backend ya manda `speech` y la app lo lee tal cual.
- Cambiar cómo se leen los procedimientos de los manuales (se siguen leyendo completos, paso por paso).
- Arreglar Vosk / la cadena de Whisper (se revisa aparte).

## Anti-objetivos (sería un fracaso aunque funcione)

- Que el modo voz recorte o reescriba pasos de un manual, o que el modelo invente pasos.
- Que el examen necesite el puerto 8000, borre o escriba en `storage/` real, o toque historial de usuarios.
- Que las pruebas automáticas normales (`pytest`, `node --test`) pasen a requerir Ollama o GPU.
- Que la respuesta en voz sea más lenta que hoy.

## Restricciones

- RTX 2060 de 6 GB compartida entre Whisper turbo y qwen3:4b-instruct; `num_ctx` 2048.
- Reglas de `CLAUDE.md`: no instalar nada sin preguntar (el diseño no requiere dependencias nuevas; YAML no se usa,
  el banco va en JSON), no tocar secretos, no correr modelos reales sin preguntar.
- Textos que ve u oye el usuario en español.

## Enfoques considerados

**Examen**
- **A. Banco de casos con revisiones automáticas (elegido).** Cada caso declara qué debe pasar (ruta, palabras
  requeridas/prohibidas, largo) y se revisa sin opinión. Repetible, barato, sirve para comparar versiones.
- B. Otro modelo califica las respuestas. qwen3:4b juzgándose a sí mismo no es confiable; queda como posible
  extensión opcional solo para preguntas generales, fuera de este trabajo.
- C. Revisión manual en hoja. No es repetible; descartado.

**Modo voz**
- **A. Respuesta corta según el canal (elegido).** Si la conversación viene de un dispositivo, el prompt general pide
  1–2 frases habladas y la oferta de detalle; "sí"/"explícame más" genera la versión larga. Más rápido porque se
  generan ~60 palabras en vez de ~700.
- B. Una respuesta con resumen arriba y detalle abajo, la voz lee solo el resumen. Descartado: genera texto que nadie
  oye y hace más lenta cada respuesta en los lentes.

El dueño eligió A en ambos.

## Diseño

### Parte 1 — Examen del cerebro

**Ubicación**
- `evals/cases/*.json` — casos, versionados, agrupados por categoría (`manuales.json`, `conceptos.json`,
  `internet.json`, `social.json`, `conversaciones.json`, `voz.json`).
- `evals/run_eval.py` — el corredor.
- `evals/results/` — reportes (no versionado; añadir a `.gitignore`).
- `scripts/run-eval.ps1` — envoltorio PowerShell con el entorno de `CLAUDE.md` (`TEMP`, `.venv`).

**Formato de un caso**
```json
{
  "id": "conc-vpn-01",
  "category": "conceptos",
  "channel": "voice",
  "turns": [
    {
      "message": "¿Qué es una VPN?",
      "expect": {
        "route": "general",
        "contains_any": ["red privada", "túnel", "cifrad"],
        "not_contains": ["El resto está en el chat"],
        "max_speech_chars": 300,
        "max_sentences": 3,
        "ends_with_offer": true
      }
    },
    {
      "message": "sí",
      "expect": { "route": "general", "min_chars": 300 }
    }
  ]
}
```
Revisiones disponibles por turno (todas opcionales): `route`, `contains_all`, `contains_any`, `not_contains`
(comparación sin acentos ni mayúsculas, como `_fold`), `max_chars`/`min_chars` sobre `answer`, `max_speech_chars`
sobre lo que se oiría (`speech` o, si es nulo, `answer`), `max_sentences`, `ends_with_offer`, `has_sources`
(manuales: al menos una fuente del documento esperado, `source_document`), `steps_verbatim` (manuales: cada paso de la
respuesta aparece literal en el documento), `web_used`. Un turno pasa si pasan todas sus revisiones; un caso pasa si
pasan todos sus turnos. Los turnos sin `expect` solo preparan contexto.

**Contenido del banco (~100 casos)**
- Manuales (~30): preguntas que se responden con los documentos indexados en `documents/managed`, incluidos "dame los
  pasos", "¿cómo se llama…?", navegación ("el paso 3", "el siguiente", "repite") y preguntas que el manual **no**
  cubre (debe decirlo y ofrecer alternativa, sin inventar). Los genera el builder leyendo los documentos indexados;
  el dueño los revisa antes de darlos por buenos.
- Conceptos de TI (~25): VPN, DNS, DHCP, BIOS, SSD vs HDD, phishing, MFA, etc.
- Internet (~15): hechos actuales ("última versión de…", noticias de TI). Revisan que se use la web y que haya fuentes;
  no fijan el dato concreto porque cambia.
- Social (~5): saludos, gracias, despedida.
- Conversaciones (~15): seguimientos ("¿y eso cómo se configura?"), cambio de tema, pronombres sin contexto.
- Voz (~10): las mismas preguntas en canal `voice` vs `web` para comprobar que solo cambia el voice.

**El corredor (`evals/run_eval.py`)**
- Arma el asistente en proceso, igual que `create_app(provider="ollama")`, sin levantar servidor ni puerto.
- Usa un directorio de trabajo temporal bajo `.cache/eval/` para estado e historial; el índice de manuales se abre en
  **solo lectura** desde la ubicación real (o se copia a `.cache/eval/` si abrirlo en solo lectura no es posible).
  Nunca escribe en `storage/`.
- Cada caso corre en una sesión nueva. Para simular el canal, el corredor llama al mismo punto que la ruta HTTP usa
  para pasarle el canal (ver Parte 2) y calcula `speech` con `spoken_summary`, igual que `routes.py`.
- Mide la latencia de cada turno.
- Puede correr con el backend real encendido o apagado *(llamada del builder)*: usa la misma instancia de Ollama, así
  que no carga una segunda copia del modelo en la GPU, y no carga Whisper. Al empezar revisa
  `http://127.0.0.1:8000/health`; si el backend está arriba, el reporte marca las latencias como "con backend activo",
  porque alguien usando los lentes al mismo tiempo las alteraría. Para comparar latencias contra la línea base se
  recomienda correrlo en las mismas condiciones.
- Opciones: `--category`, `--case`, `--provider mock|ollama` (mock para probar el propio corredor), `--compare
  <reporte>` (por defecto, el último en `evals/results/`).
- Salida: `evals/results/<fecha-hora>.json` (todo el detalle) y un resumen en consola y en `.md`: aciertos totales y por
  categoría, latencia mediana y p90 por categoría, lista de casos fallidos con el motivo, y los casos que pasaron de
  pasar a fallar (y al revés) respecto a la corrida comparada.
- Código de salida 0 siempre que corra completo (es una medición, no una prueba que bloquea).

**Pruebas del corredor**
- `tests/test_eval_runner.py` (pytest normal, con proveedor mock): carga de casos, cada tipo de revisión, el reporte
  y la comparación. No usa Ollama.

**Línea base**
- Antes de tocar el modo voz se corre el examen completo con el código actual y se guarda como referencia. Así la
  Parte 2 se mide contra ella. (Correrlo usa modelos reales: se pide permiso al dueño antes.)

### Parte 2 — Modo voz

**Canal**
- El canal es `"voice"` cuando la petición viene de un dispositivo emparejado (`request.state.device_id` presente),
  tenga o no dueño y se guarde o no su historial; `"text"` en cualquier otro caso (web, token de API). No se usa
  `target.source`, porque un dispositivo sin dueño o con el historial caído cae en el destino legado con
  `source="web"`. *(Llamada del builder: así los lentes reciben modo voz siempre.)*
- `UnifiedAssistantService.chat(message, session_id, channel="text")` recibe el canal; `history.chat.chat` lo pasa.
  El valor por defecto mantiene el comportamiento actual para cualquier otro llamador. La ruta también pasa el canal a
  `spoken_summary(answer, channel, route)`.

**Respuesta general en voz**
- Solo afecta a `_general_answer` (y a `_general_fallback_answer` / `_general_alternative_answer`, que generan texto
  general). Manuales, procedimientos, social y mensajes fijos no cambian.
- Con canal `voice`, el prompt cambia de "breve y clara" a instrucciones para el oído: responde en 1–2 frases (máximo
  ~40 palabras), sin listas, viñetas, markdown, URLs ni símbolos; números y siglas como se dicen. El backend añade
  (no el modelo) la oferta final "¿Quieres más detalle?", para que sea siempre igual y detectable.
- Límite de salida en voz: ~120 tokens (en vez de 768), para que no se alargue y sea más rápido.
- Si la respuesta usa la web, se mantienen las fuentes en `web_sources` y en el texto de la web, y el `speech` las quita
  como hoy.
- El estado guarda `pending_detail = {"question": <pregunta original>}` cuando se ofrece detalle.

**Pedir detalle**
- El siguiente mensaje responde a la oferta: afirmaciones (las mismas de `_AFFIRMATION`) o "explícame más", "más
  detalle", "dime más", "continúa". Genera la versión larga de la pregunta original con el prompt de hoy
  ("breve y clara", 768 tokens), usando la conversación.
- La versión larga en voz se lee sin "El resto está en el chat": hasta ~1200 caracteres se lee completa; si pasa de
  ahí se lee hasta la última frase completa antes del límite y se añade "¿Sigo?".
- "No" / "no gracias" a la oferta → "De acuerdo." Cualquier otro mensaje descarta la oferta y se procesa normal.

**Dos ofertas distintas** *(llamada del builder)*
- **"¿Quieres más detalle?"** (`pending_detail`): solo tras una respuesta general corta en voz. "Sí" **regenera** la
  versión larga con el modelo general.
- **"¿Sigo?"** (`pending_rest`): para cualquier respuesta en voz demasiado larga para leerse de una vez, de cualquier
  ruta salvo los procedimientos (que se siguen leyendo completos). Guarda el **resto del texto ya generado**; "sí" /
  "sigue" lo lee tal cual, sin pasar por el modelo. Así nunca se reescribe contenido de un manual.
- Las dos se evalúan antes que `pending_general` / `pending_alternative` solo si son la oferta del último turno; sólo
  puede haber una oferta activa a la vez (la nueva reemplaza a la anterior).

**Texto de "El resto está en el chat"**
- En canal voice desaparece por completo y lo reemplaza "¿Sigo?". En canal text (web) `speech` sigue como hoy.

**Historial**
- Se guarda en el historial la respuesta corta con la oferta y luego la de detalle; la web, al abrir esa conversación,
  muestra ambas, igual que cualquier otro intercambio.

**Pruebas**
- Pytest con proveedor mock: canal voice usa el prompt de voz y el límite corto; canal text no cambia (las pruebas
  actuales siguen pasando sin tocarlas); la oferta se añade solo en voice y solo en respuestas generales; "sí" genera
  detalle; "no" cierra; otro mensaje descarta la oferta; procedimientos en voice salen idénticos a hoy; `speech` de
  detalle no contiene "El resto está en el chat".
- Examen: los casos de `voz.json` y la categoría de conceptos en canal voice comprueban largo, número de frases y
  oferta; se compara contra la línea base.

## Lo que decidió el dueño

- Examen con banco de casos y revisiones automáticas (A).
- Modo voz por canal, con respuesta corta y detalle a demanda (A).
- Orden: primero el examen, luego el modo voz.
- Los procedimientos de los manuales se siguen leyendo completos.

## Lo que queda al builder

- Redacción exacta de los prompts de voz (se ajusta con el examen).
- Lista final de casos (los de manuales los revisa el dueño).
- Detalles del reporte `.md` y del envoltorio PowerShell.
- Cómo abrir el índice de manuales sin escribir en él (solo lectura o copia en `.cache/eval/`).
