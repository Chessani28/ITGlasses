# META GLASSES INTEGRATION FEASIBILITY ASSESSMENT

**Proyecto:** ITGlasses  
**Fecha:** 21/10/2026  
**Tipo:** Evaluación de viabilidad (no se implementó nada)  
**Enfoque:** Integración con Meta Ray-Ban smart glasses mediante el Wearables Device Access Toolkit (DAT)

---

## 1. Resumen ejecutivo

ITGlasses **puede integrarse con Meta Ray-Ban smart glasses**, pero solo a través de una **aplicación complementaria nativa (Android o iOS)** que utilice el **Meta Wearables Device Access Toolkit (DAT)**. No es posible integrar directamente desde la aplicación web existente ni sustituir la frase de activación nativa "Hey Meta" por una palabra de activación personalizada.

La arquitectura más realista para un prototipo inicial es **Option A** (integración oficial mediante Meta SDK con aplicación complementaria Android), reutilizando el backend FastAPI existente como servicio local al que la app móvil se conecta vía HTTP.

**Limitaciones principales:**
- No se puede sustituir "Hey Meta" por una wake word personalizada ("Oye ITGlasses")
- Solo se puede usar "Hey Meta, start {NOMBRE_APP}" para invocar la app
- HFP y A2DP son mutuamente exclusivos: no se puede tener audio de alta calidad y micrófono abierto simultáneamente
- Se requiere una aplicación complementaria nativa (no funciona solo con la web existente)
- Solo disponible en países donde Meta AI y las gafas son compatibles
- Solo una app de terceros registrada a la vez en modo desarrollador

---

## 2. Investigación de capacidades oficiales de Meta

### 2.1 Meta Wearables Device Access Toolkit (DAT)

**Fuente oficial:** https://wearables.developer.meta.com/docs/develop/dat/build-overview/

El DAT permite que una aplicación móvil (iOS o Android) se integre con gafas de IA compatibles. Establece una sesión con el dispositivo para que la app pueda acceder a sensores soportados.

**Capacidades oficiales del DAT:**
| Capacidad | Estado | Detalle |
|-----------|--------|---------|
| Acceso al micrófono | ✅ Soportado | Via HFP (Hands-Free Profile) sobre Bluetooth. Audio 8 kHz o 16 kHz mono desde el dispositivo hacia la app |
| Reproducción de audio | ✅ Soportado | Via A2DP (alta calidad estéreo 44.1/48 kHz) para salida de audio, o HFP (mono voz) para reproducción simultánea con captura |
| Cámara | ✅ Soportado | Stream de video, captura de fotos, resolución y FPS seleccionables |
| Wake word personalizada | ❌ NO soportado | Solo se puede usar "Hey Meta, start {NOMBRE_APP}" (Voice Invocations) |
| Aplicación web directa | ❌ NO soportado | Se requiere app nativa iOS o Android con SDK |
| Full-duplex con barge-in | ⚠️ Parcial | No hay API oficial de audio; todo se maneja sobre Bluetooth. El desarrollador debe implementar AEC (ej. WebRTC AEC3) por su cuenta |

**Modelo de integración:**
1. La app móvil se registra con la aplicación Meta AI (una vez)
2. Los permisos se solicitan a través de la app Meta AI (Allow once / Allow always)
3. El usuario inicia una sesión desde la app
4. Durante la sesión, el usuario interactúa con la app a través de las gafas

**Limitación importante:** Solo una sesión puede ejecutarse en el dispositivo a la vez. Ciertas funciones no están disponibles mientras la sesión está activa.

### 2.2 Microfono y altavoces

**Fuente oficial:** https://wearables.developer.meta.com/docs/develop/dat/microphones-and-speakers/

| Perfil | Dirección | Calidad | Caso de uso |
|--------|-----------|---------|-------------|
| **A2DP** | Salida solamente | Alta (44.1/48 kHz estéreo) | Música, reproducción multimedia, TTS |
| **HFP** | Bidireccional | Mono voz (16 kHz o 8 kHz negociado) | Captura de voz del micrófono de las gafas |

**Limitaciones críticas de Bluetooth:**
- **HFP y A2DP son mutuamente exclusivos:** activar HFP cambia las gafas desde A2DP, y la salida de audio cae a calidad mono voz (16 kHz o 8 kHz) durante toda la sesión
- Si la app necesita captura de micrófono y reproducción de audio al mismo tiempo, debe usar HFP (calidad reducida para ambos)
- Si solo necesita reproducción de audio, usa A2DP (calidad alta)
- El audio de la cámara puede entregarse junto con video (beta, solo canales de desarrollo)

### 2.3 Voice Invocations ("Hey Meta")

**Fuente oficial:** https://wearables.developer.meta.com/docs/develop/dat/voice-invocations/

**Cómo funciona:**
1. El usuario dice "Hey Meta, start {NOMBRE_APP}" en las gafas
2. Meta AI resuelve la frase y lanza o pone en primer plano la app
3. La app detecta que fue lanzada por una invocación de voz
4. La app inicia un stream de Voice Invocations y recibe la invocación
5. La app procesa y responde a través del handle de respuesta

**Puntos clave:**
- Las invocaciones de voz son un stream a nivel de Wearables, NO una capacidad de sesión de dispositivo
- No se requiere permiso de micrófono para Voice Invocations (no usa HFP para la invocación inicial)
- La identidad de la app no puede ser suplantada (nombre de paquete Android o bundle identifier iOS leído desde la configuración del SDK)
- Se configura en el Wearables Developer Center (nombre de la app, package/bundle ID, versión de integración)
- Una Web App en primer plano tiene prioridad sobre las invocaciones de voz

**Lo que NO es posible:**
- ❌ No se puede usar una wake word personalizada como "Oye ITGlasses"
- ❌ No se puede interceptar "Hey Meta" para activar la app directamente
- ❌ No se pueden declarar frases de invocación en AndroidManifest.xml o Info.plist

### 2.4 Permisos y registro

**Fuente oficial:** https://wearables.developer.meta.com/docs/develop/dat/permissions-requests/

- La app se registra con la app Meta AI para ser una integración permitida
- Los permisos de cámara se conceden a nivel de app; cada dispositivo debe confirmar permisos específicamente
- El acceso al micrófono usa HFP, por lo que los permisos se solicitan a través de diálogos de plataforma iOS o Android
- Los usuarios eligen "Allow once" (temporal) o "Allow always" (persistente)
- Solo una app de terceros puede permanecer registrada a la vez en modo desarrollador
- Para distribución en producción, los usuarios deben estar en el canal de release adecuado

### 2.5 Dispositivos soportados y conectividad de red

**Fuente oficial:** https://developers.meta.com/wearables/faq/

**Dispositivos soportados:**
- Ray-Ban Meta (Gen 1 y Gen 2)
- Ray-Ban Meta Display
- Oakley Meta HSTN
- Oakley Meta Vanguard

La disponibilidad de capacidades varía por dispositivo. Solo disponibles en países donde las gafas de IA son compatibles.

**Requisitos de conectividad de red:**
- La aplicación Meta AI debe usarse para emparejar las gafas
- Los datos pueden procesarse localmente o mediante plataformas cloud/edge
- La app móvil necesita conexión a internet para comunicarse con el backend FastAPI

---

## 3. Comparación de opciones

### Option A: Integración oficial mediante Meta SDK (DAT)

**Arquitectura:** Aplicación complementaria nativa (Android o iOS) que integra el SDK de Meta Wearables DAT, se comunica con las gafas vía Bluetooth, y se conecta al backend FastAPI existente vía HTTP desde el teléfono.

| Aspecto | Evaluación |
|---------|------------|
| **Viabilidad técnica** | ✅ Alta - SDK oficial estable (v1.0, lanzado 30/09/2026) |
| **Acceso al micrófono** | ✅ Sí (vía HFP, 8/16 kHz mono) |
| **Reproducción de audio** | ✅ Sí (vía A2DP alta calidad, o HFP mono voz) |
| **Wake word** | ⚠️ Solo "Hey Meta, start ITGlasses" (no personalizable) |
| **App complementaria requerida** | ✅ Sí (Android o iOS nativa) |
| **Backend FastAPI reutilizable** | ✅ Sí (vía HTTP desde la app móvil) |
| **Limitaciones de perfil Bluetooth** | ⚠️ HFP y A2DP mutuamente exclusivos |
| **Países soportados** | ⚠️ Solo donde Meta AI y gafas son compatibles |
| **Modo desarrollador** | ⚠️ Solo una app de terceros registrada a la vez |

**Complejidad:** Media-Alta (requiere desarrollo de app nativa móvil)

### Option B: Aplicación complementaria Android usando capacidades Bluetooth soportadas

**Arquitectura:** Similar a Option A pero enfocada específicamente en Android, usando el SDK Android del DAT.

| Aspecto | Evaluación |
|---------|------------|
| **Viabilidad técnica** | ✅ Alta (SDK Android disponible en GitHub) |
| **Diferencia con Option A** | Es un subconjunto de Option A limitado a Android |
| **Ventaja** | Un solo plataforma, menos fragmentación |
| **Desventaja** | No soporta iOS usuarios |

**Complejidad:** Media (solo Android)

### Option C: Aplicación web existente con audio enrutado a través de dispositivos compatibles

**Arquitectura:** Usar la aplicación web FastAPI existente y intentar enrutar audio a través de las gafas.

| Aspecto | Evaluación |
|---------|------------|
| **Viabilidad técnica** | ❌ Muy baja - No existe API oficial para acceso a micrófono/altavoces desde web |
| **Acceso al micrófono** | ❌ No soportado desde aplicaciones web |
| **Reproducción de audio** | ❌ No soportado desde aplicaciones web |
| **Wake word** | ❌ No soportado |
| **App complementaria requerida** | ❌ No hay camino oficial sin app nativa |
| **Backend FastAPI reutilizable** | ✅ Sí (la web ya lo usa) |

**Complejidad:** No viable (no hay API oficial)

---

## 4. Evaluación de viabilidad por opción

| Criterio | Option A (SDK oficial) | Option B (Android solo) | Option C (Web existente) |
|----------|------------------------|-------------------------|--------------------------|
| **Viabilidad técnica** | ✅ Alta | ✅ Alta (solo Android) | ❌ No viable |
| **Acceso al micrófono** | ✅ HFP oficial | ✅ HFP oficial | ❌ No soportado |
| **Reproducción de audio** | ✅ A2DP/HFP oficial | ✅ A2DP/HFP oficial | ❌ No soportado |
| **Wake word personalizada** | ❌ No posible | ❌ No posible | ❌ No posible |
| **Reutilización backend** | ✅ Total | ✅ Total | ✅ Total |
| **App complementaria requerida** | ✅ Sí (iOS + Android) | ✅ Sí (solo Android) | ❌ No hay camino oficial |
| **Complejidad de desarrollo** | Media-Alta | Media | N/A (no viable) |
| **Listo para producción** | ✅ SDK v1.0 estable | ✅ SDK v1.0 estable | ❌ No |

**Ganador: Option A** — Integración oficial mediante Meta SDK con aplicación complementaria (Android e iOS).

---

## 5. Reutilización del backend FastAPI existente

**Sí, el backend FastAPI puede reutilizarse prácticamente sin cambios.**

### Cómo se integraría:

```
Meta Ray-Ban Gafas
     ↓ Bluetooth (HFP/A2DP)
App móvil Android/iOS (con SDK DAT)
     ↓ HTTP (localhost o red local)
Backend FastAPI (D:\ITGlasses\backend\)
     ↓
Ollama (qwen3:4b-instruct) + RAG (SQLite + embeddings)
```

### Cambios necesarios en el backend:

1. **CORS:** El backend actualmente restringe origen a `http://127.0.0.1:8000` y `http://localhost:8000`. Se necesitaría permitir peticiones desde la app móvil (que corre en el mismo teléfono, por lo que podría ser `http://127.0.0.1:8000` si el backend corre localmente en el teléfono, o una IP de red si el backend corre en otro dispositivo).

2. **Endpoints de voz existentes:** Los endpoints `/api/v1/voice/transcribe` y `/api/v1/voice/synthesize` ya existen y pueden ser reutilizados por la app móvil.

3. **Audio PCM:** La app móvil recibiría PCM 16 kHz mono del micrófono (vía HFP) y podría enviarlo al endpoint `/api/v1/voice/transcribe`. La respuesta TTS (WAV vía Piper) podría enviarse de vuelta para reproducción vía A2DP.

4. **No es necesario modificar el motor RAG ni Ollama:** El backend existente ya maneja consultas de conocimiento, embeddings, y generación de respuestas.

### Cambios necesarios en la arquitectura de voz actual:

| Componente actual | Cambio necesario |
|-------------------|------------------|
| Frontend web (JavaScript) | Se reemplaza por app móvil nativa con SDK DAT |
| Captura de micrófono (AudioWorklet) | Se reemplaza por HFP sobre Bluetooth vía SDK |
| Wake word (prefijo de texto) | Se reemplaza por "Hey Meta, start ITGlasses" (Voice Invocations) |
| TTS (Piper vía backend) | Se reutiliza; audio WAV se envía a las gafas vía A2DP |
| STT (Vosk vía backend) | Se reutiliza; PCM del micrófono se envía al backend |

---

## 6. Privacidad, permisos y restricciones empresariales

### 6.1 Permisos requeridos

| Permiso | Cómo se solicita | ¿Quién lo concede? |
|---------|------------------|-------------------|
| Registro de app | Flujo único vía Meta AI app | Usuario |
| Cámara | Diálogo Meta AI (Allow once / Allow always) | Usuario por dispositivo |
| Micrófono (HFP) | Diálogo de plataforma iOS/Android | Usuario |
| Voice Invocations | Configuración en Developer Center | Meta (configuración de desarrollador) |

### 6.2 Restricciones empresariales

1. **Solo países soportados:** El DAT solo está disponible en países donde las gafas de IA y Meta AI son compatibles.

2. **Una sola app de terceros en modo desarrollador:** Solo una app puede estar registrada a la vez. Registrar una nueva desregistra automáticamente la anterior.

3. **Dependencia de Meta AI app:** Las gafas deben estar emparejadas con la app Meta AI. Todas las sesiones y pases de permisos pasan por ella.

4. **No hay API para reemplazar "Hey Meta":** Meta AI es el propietario del wake phrase. Las apps de terceros solo pueden ser invocadas mediante "Hey Meta, start {APP_NAME}".

5. **Datos de audio:** Meta no accede a fotos/videos o audio transferido directamente de las gafas a la app de terceros (según documentación oficial).

6. **Política de uso aceptable:** Todas las integraciones deben cumplir con la Meta Wearables Developer Acceptable Use Policy.

---

## 7. Limitaciones técnicas identificadas

### 7.1 Limitaciones de audio (críticas)

| Limitación | Impacto | Mitigación |
|------------|---------|------------|
| HFP y A2DP mutuamente exclusivos | No se puede tener TTS de alta calidad y micrófono abierto simultáneamente | Usar HFP para toda la conversación (calidad mono voz 16 kHz) o alternar entre perfiles |
| No hay AEC proporcionado por firmware | El eco del altavoz puede entrar al micrófono | Implementar AEC por software (ej. WebRTC AEC3) en la app móvil |
| No hay wake word personalizada | El usuario debe decir "Hey Meta, start ITGlasses" | Usar Voice Invocations oficial; no hay alternativa oficial |
| Audio de cámara solo en beta | Captura de audio junto con video no disponible en producción | Usar HFP para micrófono por separado |

### 7.2 Limitaciones de plataforma

| Limitación | Impacto |
|------------|---------|
| Requiere app nativa Android/iOS | No se puede usar solo la aplicación web existente |
| Solo una sesión a la vez | El usuario no puede usar otra app de gafas simultáneamente |
| Dependencia de Meta AI app | Si Meta AI no está instalado o configurado, no funciona |
| Disponibilidad geográfica limitada | Solo funciona en países soportados |

### 7.3 Limitaciones de experiencia de usuario

| Limitación | Impacto |
|------------|---------|
| "Hey Meta" no es "Oye ITGlasses" | El usuario debe usar la frase de Meta, no una personalizada |
| Calidad de audio reducida en HFP | TTS suena a calidad telefónica durante conversación con micrófono |
| No hay barge-in nativo | El desarrollador debe implementar detección de interrupción por software |

---

## 8. Arquitectura recomendada para prototipo inicial

```
┌─────────────────────────────────────────────────────────────┐
│                    Meta Ray-Ban Gafas                        │
│  Micrófono (HFP 16kHz mono)  ←→  Altavoces (A2DP estéreo)   │
└────────────────────────┬────────────────────────────────────┘
                         │ Bluetooth
┌────────────────────────▼────────────────────────────────────┐
│              App móvil Android/iOS (SDK DAT)                │
│  • Registro con Meta AI app                                  │
│  • Voice Invocations: "Hey Meta, start ITGlasses"           │
│  • Captura PCM vía HFP                                       │
│  • Reproducción WAV vía A2DP                                 │
│  • Cliente HTTP hacia backend                                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (localhost/red local)
┌────────────────────────▼────────────────────────────────────┐
│              Backend FastAPI (reutilizado)                  │
│  • POST /api/v1/voice/transcribe (Vosk STT)                 │
│  • POST /api/v1/knowledge/chat (RAG)                        │
│  • POST /api/v1/voice/synthesize (Piper TTS)                │
│  • GET /api/v1/voice/status                                 │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP local
┌────────────────────────▼────────────────────────────────────┐
│              Ollama (qwen3:4b-instruct + embeddings)        │
│              SQLite (RAG store)                             │
└─────────────────────────────────────────────────────────────┘
```

### Flujo de interacción:

1. Usuario dice: "Hey Meta, start ITGlasses"
2. Meta AI lanza la app móvil ITGlasses
3. App inicia stream de Voice Invocations y HFP para micrófono
4. Usuario dice: "¿Cómo soluciono un problema de VPN?"
5. App captura PCM 16 kHz mono vía HFP
6. App envía audio al backend `/api/v1/voice/transcribe`
7. Vosk transcribe a texto
8. App envía texto al backend `/api/v1/knowledge/chat`
9. Backend busca en RAG, genera respuesta con Ollama
10. App recibe respuesta y cita fuentes
11. App envía texto al backend `/api/v1/voice/synthesize`
12. Backend genera WAV con Piper TTS
13. App reproduce audio vía A2DP (calidad alta)
14. Usuario puede hacer seguimiento ("¿Y si no funciona?")

---

## 9. Requisitos para el siguiente paso (sin implementar aún)

Si se aprueba proceder con Option A, el siguiente desarrollo requeriría:

1. **Desarrollo de aplicación complementaria Android** con SDK de Meta Wearables DAT
2. **Configuración en Wearables Developer Center** (package name, app name, Voice Invocations)
3. **Ajustes CORS** en el backend FastAPI para permitir peticiones desde la app móvil
4. **Implementación de AEC por software** (si se requiere full-duplex con barge-in)
5. **Pruebas con hardware real** (Meta Ray-Ban Gen 2 o superior)
6. **Verificación de disponibilidad geográfica** (países soportados)

---

## 10. Conclusión y respuesta a preguntas

### 1. ¿Puede ITGlasses funcionar realisticamente con Meta Ray-Ban?

**Sí, pero con condiciones.** ITGlasses puede funcionar con Meta Ray-Ban smart glasses mediante una aplicación complementaria nativa (Android o iOS) que utilice el Meta Wearables Device Access Toolkit. La aplicación web existente no puede integrarse directamente; se requiere una app móvil que actúe como puente entre las gafas y el backend FastAPI.

### 2. ¿Podemos usar "Hey Meta" para activar nuestra propia IA?

**Parcialmente.** Se puede usar **"Hey Meta, start ITGlasses"** (o el nombre que se configure en el Developer Center) para lanzar la aplicación. **No se puede** usar una wake word personalizada como "Oye ITGlasses" ni interceptar "Hey Meta" para activar la app directamente. Meta AI es propietaria del wake phrase.

### 3. ¿Qué arquitectura deberíamos usar?

**Option A: Integración oficial mediante Meta SDK (DAT) con aplicación complementaria Android/iOS.** Esta es la única opción técnicamente viable y oficialmente soportada. El backend FastAPI existente se reutiliza como servicio local al que la app móvil se conecta vía HTTP.

### 4. ¿Podemos reutilizar el backend existente?

**Sí.** El backend FastAPI puede reutilizarse prácticamente sin cambios. Los endpoints de voz existentes (`/api/v1/voice/transcribe`, `/api/v1/voice/synthesize`, `/api/v1/knowledge/chat`) ya están implementados y pueden ser consumidos por la app móvil. Solo se necesitarían ajustes menores de CORS y posiblemente de configuración de red.

### 5. ¿Cuáles son las limitaciones técnicas?

- **No se puede sustituir "Hey Meta"** por una wake word personalizada
- **HFP y A2DP son mutuamente exclusivos**: no se puede tener audio de alta calidad y micrófono abierto simultáneamente
- **Se requiere app nativa**: la aplicación web existente no basta
- **Solo un país soportado**: disponible solo donde Meta AI y las gafas son compatibles
- **Una sola app de terceros en modo desarrollador**
- **No hay AEC nativo**: debe implementarse por software si se requiere full-duplex
- **Calidad de audio reducida** en modo conversación (HFP mono 16 kHz)

### 6. ¿Cuál debería ser nuestra siguiente fase de desarrollo?

**Fase recomendada: Prototipo de integración con Meta Ray-Ban (Option A)**

1. Desarrollar una aplicación complementaria Android mínima con el SDK de Meta Wearables DAT
2. Configurar Voice Invocations en el Wearables Developer Center
3. Conectar la app móvil al backend FastAPI existente (ajustes CORS)
4. Probar el flujo completo: "Hey Meta, start ITGlasses" → pregunta → respuesta RAG → TTS a través de las gafas
5. Validar con hardware real (Meta Ray-Ban Gen 2)

Esta fase no requiere modificar el motor RAG, Ollama, ni los endpoints existentes del backend. Solo requiere desarrollo de la capa móvil y pruebas de integración de extremo a extremo.

---

**Fin de la evaluación de viabilidad**

*Este documento es una evaluación de viabilidad únicamente. No se ha modificado código de aplicación, no se han instalado dependencias, y no se han hecho commits de Git.*