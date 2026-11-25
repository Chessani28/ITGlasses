# Auditoría de ITGlasses y ITGlasses-Android — 2026-11-25

Estado de los dos sistemas pensando en que el producto final son unos lentes Ray-Ban Meta que se puedan vender.
Basado en el código de hoy (ramas `feature/respuestas-manuales` y Android `feature/device-registration`), el examen
del cerebro con Ollama real y lo que vimos en las pruebas con el celular. Donde algo no lo verifiqué, lo digo.

## Resumen

El **cerebro ya es bueno para una demo seria**: 88/102 en el examen (era 68/102 en la mañana), voz pensada para
lentes, manuales en inglés respondidos en español sin inventar, y una base de seguridad sólida (usuarios, roles,
emparejamiento con llaves en el celular, web con CSP). Lo que **lo separa de un producto vendible** no es el cerebro
sino la operación: arrancar todo a mano, RAM/VRAM muy justas en esta PC, cuatro ramas sin integrar a `master`,
secretos dentro de un manual, y una app Android que todavía no tiene build de release.

---

## 1. Lo que ya funciona al 100

### ITGlasses (backend y web)
| Área | Evidencia |
|---|---|
| Chat general y conceptos de TI | Examen: conceptos 24/25, social 5/5. |
| Modo voz (lentes/celular) | Examen: voz 12/12. Respuesta corta + "¿Quieres más detalle?", "¿Sigo?" en lo largo, procedimientos de 3 en 3, sin oferta tras una aclaración. |
| Procedimientos de manuales | Se leen tal cual del catálogo, paso por paso, con navegación ("el paso 3", "el siguiente"). |
| Manuales en inglés con preguntas en español | Traducción de la consulta + respuestas con frases del manual traducidas con candados (números, nombres en pantalla, polaridad). Nunca redacta el modelo. |
| Usuarios, roles, sesiones, CSRF, auditoría | Suite extensa de pruebas; verificado por ti en el sistema real (progress.md). |
| Historial por usuario y dispositivo | Pruebas + verificado por ti. |
| Emparejamiento de celulares (DPoP) | Aprobación desde el panel; llaves en el AndroidKeyStore. |
| Seguridad de la web | CSP estricta, sin `innerHTML`, enlaces solo `https`, `noopener`, CSRF. |
| Aviso de búsqueda degradada | Barra superior en ámbar si falta el modelo de embeddings. |
| Examen del cerebro | `scripts/run-eval.ps1`, 102 casos, reporte con aciertos, latencia y cambios. |
| Pruebas | 1366 pytest + 104 node pasando; `pip check` limpio. |

### ITGlasses-Android
| Área | Evidencia |
|---|---|
| Modo app abierta + "Usar lentes" y modo celular bloqueado | Tags `demo-lentes-v1..v3`; probado hoy contigo. |
| Emparejamiento con autoregistro y aprobación | Llaves EC en AndroidKeyStore, DPoP. |
| Voz: Whisper en el servidor con reintento a Vosk tras 503 | `SttEngine`; la app marca "Motor de voz básico" cuando cae a Vosk. |
| Lectura del campo `speech` del backend | `SpokenAnswer`: el modo voz funciona sin cambios en la app. |
| Pruebas unitarias | 51 archivos de prueba JUnit. **No los corrí hoy** (no hubo cambios en Android). |

---

## 2. Lo que necesita ajustes

### Alta prioridad
1. **Memoria de esta PC (16 GB RAM, 6 GB VRAM).** Whisper Small necesita 3 GiB libres y hoy hay ~3.3–3.7 GB: cualquier
   pestaña extra lo tira a Vosk (que entiende muy mal: "phishing" → "fin sin"). Whisper turbo en GPU no carga porque
   apps de escritorio (Chrome, Edge, reproductores, overlay de NVIDIA) ocupan VRAM. Además el `llama-server` del chat
   usa ~5.3 GB de RAM aunque el modelo está entero en la GPU: hay que investigar (mmap del modelo, `num_ctx`).
2. **Secretos dentro de un manual.** "Training Setup Guideline" trae en texto plano un token de autenticación y un par
   de llaves de acceso. Cualquier celular emparejado puede preguntar por ellos y el asistente podría leerlos en voz
   alta. Para un producto esto es obligatorio de resolver (ver "Lo que falta", redacción al indexar).
3. **Arranque manual y frágil.** Ollama, backend y Caddy se arrancan a mano; la app de escritorio de Ollama
   secuestraba el puerto sin el modelo de embeddings (ya quitamos su inicio automático). Caddy está atado a la IP
   `172.16.5.142` por DHCP: si el router le da otra IP, los celulares dejan de conectar.
4. **Ramas sin integrar.** ITGlasses: `master` está 89 commits atrás (último 2026-11-05) y hay 4 ramas encadenadas.
   Android: `master` 29 commits atrás, 7 ramas remotas `claude/*` viejas y 3 locales `feature/glasses-*`.

### Media prioridad
5. **Búsqueda en internet solo por palabras clave** ("versión", "última"): "¿Teams tiene una caída?", "precio de un
   SSD", "¿Windows 10 tiene soporte?" no buscan (internet 12/15).
6. **Manuales 22/30.** Lo que falta ya depende del modelo de 4B (elige la página vecina) y del tamaño de los pedazos
   (casi una página cada uno). Pedazos más finos al indexar ayudarían.
7. **Pasos de procedimientos en inglés en la voz.** En los lentes se leen tal cual del manual en inglés; ya existe la
   traducción con candados ("explícame el paso N"), falta aplicarla a la lectura por partes.
8. **La suite de pytest tarda 10–45 min.** Frena cada cambio; vale la pena perfilar las pruebas más lentas.
9. **`stop-backend.ps1` falla** si un proceso hijo ya terminó (pasó hoy dos veces); el resto del comando no corre.
10. **Android `lintMetaDebug` falla** por `android:exported` faltante en el manifest del flavor `meta`.

---

## 3. Lo que hace falta para venderlo

| Qué | Por qué |
|---|---|
| **Redacción de secretos al indexar manuales** (tokens, llaves, contraseñas, cadenas hex/base32 largas) | Que el asistente nunca pueda leer un secreto por voz. |
| **Arranque con un solo comando y como servicio** (Ollama + backend + Caddy, con chequeo de modelos) | Que reiniciar la PC no requiera a un técnico. |
| **IP fija o nombre** (reserva DHCP o DNS local) para Caddy | Que los celulares no se desconecten por un cambio de IP. |
| **Build de release de Android**: firma, R8/minify, `versionCode` real, URL de producción (hoy vacía) | Hoy solo hay APK de debug. |
| **Certificados de producto** (dominio y certificado reconocido, o una forma guiada de instalar la CA) | La CA piloto sirve para la prueba, no para clientes. |
| **Respuestas en streaming** (empezar a hablar mientras se genera) | Menos espera percibida en los lentes. |
| **Monitoreo**: latencia por etapa (STT, búsqueda, modelo, TTS), errores y motor de voz usado | Saber qué falla en campo sin pedir capturas. |
| **El examen en cada cambio** (antes de integrar a `master`) | Números para vender y para no retroceder. |
| **Revisión de licencias** (Qwen, Whisper, Piper y sus voces, SDK de Meta) | Requisito para comercializar. |
| **Integración real con el SDK de Meta** (flavor `meta`) | Hoy la demo usa los lentes como audífonos Bluetooth (flavor `phone`). |

---

## 4. Lo que sería mejor eliminar o cambiar

| Qué | Cambio propuesto |
|---|---|
| **Vosk como respaldo de voz** | Reemplazarlo por Whisper `base`/`tiny` en CPU (~1 GB de RAM): entiende mucho mejor y cabe donde Small no. |
| **Lógica de voz duplicada en Android** (`MainActivity` y `VoicePipeline`, marcado `TODO(unificar)`) | Unificar en `VoicePipeline` y que `MainActivity` (1238 líneas) lo use. |
| **Modo de token compartido en Android** (siempre usa Vosk) | Retirarlo cuando todo esté emparejado. |
| **Enrutamiento con ~2500 líneas de reglas** (`assistant/service.py`) | Ir moviendo decisiones a un clasificador con el modelo (JSON), con el examen como red de seguridad. |
| **`OllamaProvider.generate_grounded_answer`** | Código muerto: el servicio de conocimiento lo busca en el selector, nunca en el proveedor. Eliminar. |
| **Ramas viejas** (`claude/*` en Android, `feature/glasses-*`, y las 4 de ITGlasses una vez integradas) | Integrar o borrar en GitHub (lo haces tú o con tu OK). |
| **Carpeta `Claude outputs/` en la raíz de ITGlasses** (capturas sin versionar) | Moverla fuera del repo o ignorarla. |
| **Mezcla de inglés y español en `docs/`** | Unificar el idioma de la documentación. |

---

## 5. Orden recomendado

1. **Integrar las 4 ramas a `master`** (y la de Android) y crear el arranque con un solo comando + IP fija.
2. **Redactar secretos al indexar** los manuales.
3. **Voz estable**: respaldo con Whisper `base` en lugar de Vosk e investigar la RAM del `llama-server`.
4. **Android de release** (firma, R8, versión, URL) y unificar la lógica de voz.
5. **Cerebro**: búsqueda web decidida por el modelo y pasos de procedimientos traducidos en voz.
