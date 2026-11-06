# ITGlasses — auditoría técnica y estabilización

Fecha: 5 de noviembre de 2026, America/Mexico_City.

La estabilización y las regresiones técnicas están verificadas. **La aceptación completa para una demostración sigue pendiente**: en este equipo faltan Ollama operativo, manuales de empresa y modelos/audio real de voz. No se afirma precisión de RAG con documentos corporativos ni reconocimiento de español mexicano sin esas pruebas.

## 1. Estado inicial y alcance

Se inspeccionó el repositorio real en `D:\Itglasses`. Rama inicial `master`, commit `fb71a339afdaa207341f22fa2eae400e8f63cb75`, remoto `https://github.com/Chessani28/ITGlasses.git`, sin cambios locales iniciales. El trabajo queda sin commit en `codex/audit-stabilization`; `master` conserva su commit. No hubo reset, force-push, push ni sustitución de arquitectura.

Se revisaron FastAPI y rutas, autenticación, asistente unificado y memoria, Ollama, ingesta/extracción, SQLite/FTS y embeddings, selección de evidencia, Vosk/Piper y workers, captura y control de voz, JavaScript/HTML, recuperación pública, configuración, dependencias y pruebas. Se entregó un resumen priorizado antes de modificar código.

El `.venv` que necesita el script de inicio no existía. Se creó uno aislado en el proyecto con Python 3.14.7 y las dependencias de desarrollo declaradas, usando UV; 28 paquetes instalados compatibles según `uv pip check`. No se instalaron modelos ni servicios de nube. Se crearon cachés y fixtures de prueba dentro del proyecto; el arranque real creó un índice SQLite local vacío. Los fixtures no se cargaron en ese índice.

Hardware observado: AMD Ryzen 7 7735HS, 16 procesadores lógicos, 15.31 GiB de RAM utilizable, aproximadamente 2.38 GiB disponibles al inicio, RTX 4050 Laptop de 6141 MiB. NVIDIA reportó 5920 MiB libres en una consulta posterior. Es otro equipo que el i5/RTX 2060 de destino. CIM no estaba disponible en el sandbox; RAM se consultó con Win32 y CPU con registro. No se cargaron modelos con tan poca RAM libre.

## 2. Defectos confirmados y severidad

| Severidad | Defecto inicial | Evidencia y corrección |
|---|---|---|
| Alta | Importación de `requests` sin dependencia declarada | La suite Python no podía recolectar pruebas del asistente; el asistente también dependía de ese import en ejecución. Se usa HTTPX ya declarado. |
| Alta | Pruebas de voz importaban `test_voice`, ausente del repositorio | Error real de colección. Se incorporó un generador de WAV silencioso al propio archivo de pruebas. |
| Alta | «Según internet…» se bloqueaba por contener «según» | Reproducción de la pregunta C sin fuentes. Se corrigió el filtro sin permitir consultas documentales o sensibles al buscador. |
| Alta | Verificación de Windows dependía de escribir «oficial» y de resultados de buscador | Se consulta directamente una URL fija oficial para preguntas sobre versión/compilación actual. Las otras búsquedas siguen por su adaptador previo. |
| Alta, latente | `fetch_url` admitía URL arbitraria, redirecciones y HTTP síncrono sin límites de cuerpo | No se encontraron llamadas activas; era una superficie peligrosa para usos posteriores. Ahora solo admite la fuente oficial fija, con límites y modo offline respetado. |
| Alta | Extractos web podían entrar al modelo en turnos posteriores mediante el historial | Se reprodujo la inyección indirecta y se excluyeron también los turnos web del contexto generado. |
| Alta | Revocar un documento dejaba título y texto en memoria utilizables en respuestas posteriores | Reproducción con SQLite real y revocación. Se revalidan citas, se borra el contexto documental inválido y se evita que extractos documentales alimenten generación general. |
| Media | Respaldo RAG sin citas dentro del texto | Se agregan citas de documento/versión/identificador a cada extracto reautorizado. Se conserva la lista estructurada de fuentes. |
| Media | Listas numeradas sin encabezado reconocible quedaban con título vacío | Se hereda el título del documento, restaurando selección de procedimiento y seguimientos. |
| Media | Catálogo fallaba al comenzar con viñeta | Se soportan viñetas independientes preservando la primera instrucción y el orden. |
| Media | Seguimientos como «¿Qué significa eso?» salían del documento hacia el modelo general | Se conserva la ruta documental y el tema previo para referencias anafóricas. Un cambio explícito a VPN sigue siendo general. |
| Media | Navegador sin transporte del bearer existente | Se añadió un campo de token en Estado del sistema y transporte común para API y síntesis. Se almacena solo en memoria, se puede borrar y no se añade a URLs externas. |
| Media | TTS de respuestas web enviaba IDs documentales nulos | Se incluyen exclusivamente identificadores de citas documentales presentes. Las fuentes públicas mantienen sus enlaces. |
| Media | Prompt general afirmaba siempre que no había documentos indexados | Se retiró esa afirmación falsa y la advertencia documental repetitiva para conceptos generales. |
| Media | Confianza NaN/infinita/fuera de rango podía evitar aclaración | Se valida confianza finita entre 0 y 1; datos inválidos requieren aclaración. No se redujeron los umbrales existentes. |
| Baja | Errores de voz en inglés y textos con codificación dañada | Mensajes de servicio y etiquetas corregidos en español. Se conservan códigos y estados HTTP. |
| Baja | Inicio con nivel de log `critical` ocultaba diagnósticos de workers | Se habilitó nivel `info`, conservando access log desactivado. |
| Rendimiento | Cada cita cargaba todos los chunks/vectores; consulta vacía intentaba contactar embeddings | Consulta SQL por ID y autorización/revisión actual; salida inmediata sin documentos elegibles. |

La revisión independiente detectó dos problemas durante la implementación (omisión de primera viñeta y uso de tabla de versiones para cualquier pregunta sobre Windows 11). Se reprodujeron con pruebas, se corrigieron y se volvieron a revisar. No se entregan esos comportamientos.

## 3. Mejoras implementadas y límites de diseño

RAG mantiene SQLite/FTS, embeddings existentes, permisos, versiones, selección de evidencia y citas. Las respuestas de mantenimiento usan extractos autorizados, incluyendo sus listas numeradas, sin inventar procedimientos. Se eliminó la rama opcional de prosa generada sin validación: el selector actual no la implementaba y el texto devuelto debe poder cotejarse con evidencia. Una fuente revocada o reemplazada deja de ser elegible. Los seguimientos de pasos conservan procedimiento y fuentes vigentes.

La voz mantiene Vosk y Piper. Se normalizan acrónimos explícitos y las expresiones «Windows once» y «Active Directory», conservando `raw_text` y puntuación. No se sustituyen homófonos como «bien» por VPN. Se preservan la política de confianza, detección de silencio, aislamiento por proceso, timeout y recuperación existentes. Los errores 502 estructurados siguen disponibles cuando el worker falla de verdad; no se ocultan como transcripciones exitosas.

La captura ya tenía acumulación de silencio reiniciada al hablar, margen de pausa de 2.5 s por defecto, límite de 28 s, prebuffer y detección de corte. Las regresiones existentes pasan; no había evidencia para cambiar esos valores de producción. El modo diagnóstico sigue permitiendo ajustes propios. No se cambió de motor ni se afirmó resolver precisión acústica sin audio humano.

La consulta pública de Windows usa HTTPS, URL exacta de Microsoft, HTTPX asíncrono, sin proxies de entorno ni redirecciones, máximo de 3 MB y plazo global de 20 s (conexión 3 s, lectura 15 s). Se interpretan filas de disponibilidad general y no se aceptan fechas de disponibilidad futuras. Se distingue versión de características de build y se advierte sobre disponibilidad por dispositivo/canal. Si cambia la tabla, hay timeout o falla la red, no se inventa una versión ni se recurre al conocimiento del modelo. Los textos externos no se envían al LLM como instrucciones.

La búsqueda genérica conserva el adaptador existente de DDGS, ahora con backend DuckDuckGo explícito. Su instalación queda en `requirements-web.txt`, opcional; no es necesaria para Windows 11. No se verificó búsqueda genérica real en esta sesión. `ITGLASSES_WEB_PROVIDER=offline` desactiva la recuperación pública. La funcionalidad activa es `POST /api/v1/assistant/chat`; el archivo legado `api/web_routes.py` no está montado y no se presenta como endpoint operativo.

## 4. Archivos modificados o añadidos

Rutas relativas a `D:\Itglasses`:

- `.gitignore`: incluir el nuevo archivo de regresiones; excluir scratch de auditoría.
- `README.md`: estado actual y uso; secciones antiguas marcadas como históricas.
- `backend/app/assistant/memory.py`: contexto general acotado separado de extractos documentales y contenido web no confiable.
- `backend/app/assistant/procedures.py`: título por defecto y listas con viñetas.
- `backend/app/assistant/service.py`: prioridad documental, contexto, revocación y fechas de fuentes web.
- `backend/app/assistant/web_search.py`: HTTPX, fuente oficial acotada, errores y modo offline.
- `backend/app/knowledge/service.py`: citas de extractos, seguimiento y salida sin corpus.
- `backend/app/knowledge/store.py`: consulta individual de evidencia con permisos y revisión.
- `backend/app/providers/ollama.py`: prompt general sin afirmación falsa del índice.
- `backend/app/voice/service.py`: mensajes de error en español.
- `backend/app/voice/transcription.py`: normalización conservadora y confianza válida.
- `backend/run.py`: visibilidad de logs diagnósticos.
- `frontend/app.js`, `frontend/core.mjs`, `frontend/index.html`: uso del bearer existente en memoria.
- `frontend/voice-ui.mjs`: bearer en síntesis, filtrado de citas y etiquetas.
- `requirements-web.txt`: dependencia opcional de búsqueda genérica.
- `tests/test_voice_recovery.py`: fixture WAV autónoma.
- `tests/test_audit_regressions.py`: regresiones de auditoría y seguridad.
- `tests/frontend.test.mjs`, `tests/voice-ui.test.mjs`: transporte de token y TTS con fuentes web.
- `docs/superpowers/plans/2026-11-05-stabilization.md`: plan y resultados.
- `docs/technical-audit-2026-11-05.md`: este informe.

## 5. Pruebas y recuentos reales

| Verificación final | Aprobadas | Fallos | Omitidas |
|---|---:|---:|---:|
| Suite Python completa | 162 | 0 | 0 |
| Suite JavaScript completa | 36 | 0 | 0 |
| Comprobaciones HTTP auxiliares de arranque, assets, bearer y CORS | 8 | 0 | 0 |
| Aceptación funcional real A/B/C | 1 | 2 | 0 |

Las dos pruebas funcionales no cumplidas son A y B por recursos ausentes; sus respuestas de indisponibilidad/rechazo fueron correctas. No se confunden con fallos de las pruebas deterministas.

Python mantiene una advertencia no fatal de Starlette/TestClient sobre HTTPX. `uv pip check`: 28 paquetes compatibles; no es un análisis de CVE. `git diff --check` sin errores; Git avisa de conversión LF/CRLF en archivos de pruebas JavaScript. Las 34 pruebas JS de inicio pasaban. La ejecución Python inicial tuvo tres errores de colección; no se atribuyen al estado inicial los 162 éxitos posteriores.

Las regresiones de defectos corregidos se observaron fallar antes de su corrección; otras pruebas añadidas caracterizan protecciones ya existentes. Cubren RAG con extracción y SQLite reales, revocación y reemplazo, referencias anafóricas, path traversal de nombres de archivo, endpoint de fuentes protegido, rechazo de origen ajeno, redirecciones, página demasiado grande, tabla inválida, modo offline, errores de red simulados, confianza inválida y transporte del token. Las llamadas externas y embeddings/LLM usan fixtures en la suite. Una prueba ejecuta el worker real con WAV silencioso bajo SelectorEventLoop de Windows; eso verifica el transporte y la recuperación, no reconocimiento acústico.

Comandos reproducibles, desde el proyecto (usar un directorio `--basetemp` nuevo reservado para pruebas):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp D:\Itglasses\work\audit-recheck
node --test tests/frontend.test.mjs tests/voice.test.mjs tests/voice-ui.test.mjs tests/diagnostic.test.mjs
uv pip check --python .venv\Scripts\python.exe
git diff --check
```

## 6. Tres escenarios requeridos

| Escenario | Resultado real | Ruta | Ollama real | Documentos reales | Evidencia |
|---|---|---|---|---|---|
| A: «Según este manual, ¿cuál es el mantenimiento que se debe realizar a las computadoras?» | No cumplido: no hay manuales cargados | `knowledge` | No | No | Rechazo correcto, sin instrucciones inventadas ni fuentes falsas. |
| B: «¿Qué es una VPN?» | No cumplido: Ollama no escucha | `general` | No; se intentó conectar al proveedor configurado | No aplican | Se informó indisponibilidad, sin simular respuesta local. |
| C: «Según internet, ¿cuál es la versión más reciente de Windows 11?» | Aprobado por HTTP y red reales | `general`, recuperación pública | No es necesario | No aplican | Tabla oficial Microsoft consultada durante la petición, enlace y fecha incluidos. |

A también pasó como prueba controlada con un manual de mantenimiento español sintético, extracción y SQLite reales, embeddings/selector simulados. Se verificaron apagado, limpieza, bitácora, citas, segundo paso y revocación. B pasó como prueba de ruta y adaptador Ollama con respuesta HTTP simulada sobre una conexión cifrada; no demuestra calidad de Qwen3 real. C tiene además regresiones con tablas y errores simulados, distintas de la verificación en internet real.

En C se recuperaron 26H2 y build 26300.9818, revisión 2026-10-28, de la [tabla oficial de Microsoft](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information). La fecha/hora exacta UTC está en el JSON de aceptación; corresponde a la tarde del 5 de noviembre en Ciudad de México. Es un resultado de esa consulta, no una constante añadida al código.

No se transcribieron las tres preguntas con micrófono o audio humano: no estaban disponibles modelos locales ni grabaciones compatibles. Vosk/Piper reportaron `stt_available=false` y `tts_available=false`. El navegador integrado falló al crear una sesión utilizable; no se afirma verificación visual completa. Se sirvieron los assets reales y se ejecutaron pruebas JS del comportamiento.

## 7. Problemas y limitaciones pendientes

- Antes de declarar la demo lista: arrancar Ollama, comprobar los modelos existentes, liberar RAM y repetir A/B con inferencia real y un manual corporativo autorizado; repetir A/B/C por voz con grabaciones de hablantes mexicanos.
- No hay reconocimiento acústico ni TTS reales certificados en esta sesión. Piper conserva la voz es_ES existente; no se añadió una voz mexicana.
- RAG conserva umbrales y límites existentes; no se calibró con un corpus empresarial grande. Documentos escaneados necesitan OCR, que sigue ausente. Traducciones de extractos ingleses siguen siendo conservadoras y pueden conservar el original para evitar inventar instrucciones.
- La autorización es global de documento en una aplicación local de usuario único, no RBAC por empleado. Todos los bearer configurados comparten capacidades. No se sustituyó ese modelo.
- El modo local de desarrollo continúa sin credenciales si no se exige autenticación. Para la demo se recomienda activar el bearer existente. No hay un filtro TrustedHost añadido; no se certifica resistencia a DNS rebinding en el modo sin autenticación.
- Orígenes de escritura/voz siguen limitados a localhost:8000 y 127.0.0.1:8000. LAN, otro puerto y proxies requieren evaluación específica; no se habilitaron ni se probaron.
- Los workers separan procesos y tienen límites de tiempo, pero no son una sandbox del sistema operativo ni imponen cuotas duras de RAM. La ingesta con reload/SelectorEventLoop no se certificó; el inicio normal y worker de voz sí se verificaron en su alcance.
- La búsqueda genérica opcional requiere DDGS y no se validó contra red real. Un cambio de HTML de Microsoft producirá un fallo explícito y requerirá ajustar el parser; no debe activar respuestas inventadas.
- La comprobación global de estado conserva campos históricos como `rag_enabled`; el estado específico de conocimiento es la referencia de disponibilidad documental.

## 8. Seguridad y rendimiento

Se conserva bearer y fallo cerrado cuando se exige autenticación, sin cookies ni tokens en query como alternativa. El ensayo HTTP real comprobó 401 sin token y éxito con token de prueba. El token introducido en interfaz no se persiste en localStorage, sessionStorage ni cookies; la síntesis usa el mismo transporte. En localhost HTTP funciona para la demo local; esto no certifica una exposición remota segura.

Se preservan validación de tipos y tamaño de documentos, nombres de almacenamiento generados, límites de extracción y revisión/autorización de citas. Nombres con `../`, separadores Windows y rutas absolutas no escaparon del directorio administrado en pruebas. Los endpoints de fuentes no devolvieron evidencia revocada. CORS no concedió acceso a un origen ajeno en preflight real. No se realizó pentest exhaustivo ni escaneo de vulnerabilidades de dependencias.

La recuperación oficial no envía el texto de la pregunta o historial a Microsoft: solo solicita la página pública fija. Consultas explícitas de manuales conservan la ruta documental; las sensibles no se envían al buscador. Para búsqueda genérica se envía únicamente la pregunta pública actual, nunca el historial ni chunks. No se siguen enlaces proporcionados por documentos. El contenido externo se muestra como datos y no se usa para dar órdenes al modelo.

La evidencia por ID usa SQL en lugar de cargar todos los vectores por cada cita. Con índice vacío, A pasó de aproximadamente 2.1 s a 0.03 s en peticiones observadas al evitar el contacto innecesario con embeddings; no es un benchmark estadístico. La consulta C final tardó 1.429 s, variable con la red. No se midieron latencias reales de Qwen3/RAG o precisión de voz. Se conservan configuración de contexto, cuantización y límites existentes, sin añadir modelos grandes, colas o infraestructura.

## 9. Próximos pasos y entrega Git

1. Revisar el diff y los archivos nuevos de `codex/audit-stabilization`; no se creó commit ni se publicó nada.
2. Liberar RAM y arrancar el Ollama local existente, verificando Qwen3 y embeddings sin descargas automáticas.
3. Cargar una copia autorizada del manual aplicable con versión y ámbito correctos. Repetir A y seguimientos; revisar citas contra el original y probar revocación.
4. Repetir B con Qwen3 real y comprobar respuestas conceptuales y continuidad. Repetir C con red disponible y desconectada.
5. Restaurar/instalar los modelos de voz aprobados según la guía existente y grabar las tres preguntas con hablantes mexicanos. Registrar texto original, normalización, aclaraciones, cortes y errores del worker.
6. Hacer una comprobación visual real del navegador y usar el campo Token de acceso en Estado del sistema si el backend exige bearer.
7. Mantener la demostración local y limitada hasta cerrar estas verificaciones. Guardar resultados de aceptación reales antes de ampliar usuarios o red.

El servidor temporal de aceptación se terminó al finalizar su ejecución. Los scripts y evidencias intermedias quedan bajo `work/audit-*`, ignorados por Git. El informe y el plan se incluyen en `docs`; las evidencias exportadas se entregan además en la carpeta de outputs del chat.
