# 🤖 Documentación: Integración de IA en el Chatbot

Este documento explica cómo se integran conceptos de Inteligencia Artificial en el chatbot de Frere's, incluyendo la relación con **Lógica Fuzzy**, **Perceptrón**, **Teorema de Esquemas** y **Redes Neuronales**.

---

## 📋 Índice

1. [Arquitectura General del Bot](#arquitectura-general-del-bot)
2. [Integración con IA (Hugging Face)](#integración-con-ia-hugging-face)
3. [Conceptos de IA Aplicados](#conceptos-de-ia-aplicados)
   - [Lógica Fuzzy (Difusa)](#1-lógica-fuzzy-difusa)
   - [Perceptrón](#2-perceptrón)
   - [Redes Neuronales](#3-redes-neuronales)
   - [Teorema de Esquemas](#4-teorema-de-esquemas)
4. [Flujo de Procesamiento](#flujo-de-procesamiento)
5. [Modelo de IA Utilizado](#modelo-de-ia-utilizado)

---

## Arquitectura General del Bot

```
┌─────────────────┐     ┌────────────────┐     ┌───────────────────┐
│  Facebook       │────▶│  Flask Server  │────▶│  Firebase         │
│  Messenger      │     │  (app.py)      │     │  (Productos/DB)   │
└─────────────────┘     └───────┬────────┘     └───────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │  Hugging Face API     │
                    │  (Qwen2.5-7B-Instruct)│
                    │  MODELO DE IA         │
                    └───────────────────────┘
```

---

## Integración con IA (Hugging Face)

### Ubicación en el Código

La integración de IA se encuentra en `app.py` en la función `consultar_ia()`:

```python
# Línea 11-12: Importación de la librería
from huggingface_hub import InferenceClient

# Línea 215-268: Función principal de IA
def consultar_ia(sender_id, mensaje):
    if not HF_TOKEN: return "⚠️ IA desactivada (Falta Token)."
    
    # 1. Recuperación de productos relevantes
    prods = obtener_productos_con_cache()
    palabras = mensaje.lower().split()
    relevantes = []
    
    # 2. Filtrado por relevancia (Matching)
    for pid, p in prods.items():
        texto_prod = (str(p.get("nombre")) + " " + 
                      str(p.get("categoria")) + " " + 
                      str(p.get("descripcion", ""))).lower()
        
        # Condición de relevancia
        match = any(word in texto_prod for word in palabras if len(word) > 3)
        
        if match: 
            relevantes.append(info)
    
    # 3. Llamada al modelo de IA
    client = InferenceClient(token=HF_TOKEN)
    resp = client.chat_completion(
        messages=[
            {"role": "system", "content": prompt}, 
            {"role": "user", "content": mensaje}
        ],
        model="Qwen/Qwen2.5-7B-Instruct",
        max_tokens=200,
        temperature=0.4
    )
    return resp.choices[0].message.content
```

---

## Conceptos de IA Aplicados

### 1. Lógica Fuzzy (Difusa)

La **lógica difusa** permite trabajar con grados de verdad en lugar de valores binarios (verdadero/falso). En este chatbot se aplica de las siguientes maneras:

#### a) Normalización de Texto (Grado de Similitud)

```python
# Línea 49-54
def normalizar(t):
    if not t: return ""
    t = t.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.translate(str.maketrans("", "", string.punctuation))
```

> **Concepto Fuzzy**: La normalización elimina acentos, mayúsculas y puntuación para crear una "membresía parcial" - el texto "Cámara" y "camara" tienen un **grado de pertenencia = 1.0** a la misma categoría después de normalizar.

#### b) Matching Parcial en Búsquedas

```python
# Línea 179-190
def buscar_productos_clave(termino):
    prods = obtener_productos_con_cache()
    resultados = []
    t = normalizar(termino)
    for pid, d in prods.items():
        nombre = normalizar(d.get("nombre", ""))
        cat = normalizar(d.get("categoria", ""))
        # Búsqueda fuzzy: si el término está CONTENIDO en nombre o categoría
        if t in nombre or t in cat:
            resultados.append(d)
    return resultados
```

> **Analogía Fuzzy**: En lugar de buscar coincidencias exactas (lógica binaria), se acepta que "cami" coincida con "camisa" - esto representa un **grado de membresía parcial**.

#### c) Detección de Intenciones con Múltiples Palabras Clave

```python
# Línea 346
if any(x in msg for x in ["hola", "inicio", "menu", "buenos dias", "buenas tardes"]):
    return "👋 ¡Hola! Soy Frere's Bot..."
```

> **Conjunto Fuzzy de Saludos**: {hola: 1.0, inicio: 0.8, menú: 0.7, buenos días: 1.0, buenas tardes: 1.0}

---

### 2. Perceptrón

El **perceptrón** es la unidad básica de una red neuronal que realiza una suma ponderada seguida de una función de activación.

#### Analogía en el Código: Sistema de Rate Limiting

```python
# Línea 56-63
def verificar_rate_limit(sender_id):
    ahora = datetime.now()
    # Suma de mensajes (análogo a suma ponderada)
    user_message_count[sender_id] = [
        ts for ts in user_message_count[sender_id] 
        if (ahora - ts).total_seconds() < RATE_LIMIT_WINDOW
    ]
    
    # Función de Activación (umbral)
    if len(user_message_count[sender_id]) >= RATE_LIMIT_MESSAGES: 
        return False  # Bloquear (output = 0)
    
    return True  # Permitir (output = 1)
```

**Modelo Perceptrón Simplificado**:
```
                    ┌─────────────────────┐
Mensajes(t-1) ──────┤                     │
                    │   Σ (Suma de        │
Mensajes(t-2) ──────┤   mensajes en       ├──▶ f(x) ──▶ Permitir/Bloquear
                    │   ventana de 60s)   │
Mensajes(t-n) ──────┤                     │
                    └─────────────────────┘
                    
f(x) = { 1 si Σ < 10 (RATE_LIMIT_MESSAGES)
       { 0 si Σ >= 10
```

#### Analogía en Relevancia de Productos

```python
# Línea 233-236
# El matching actúa como un perceptrón simple
match = any(word in texto_prod for word in palabras if len(word) > 3)

# Equivalente a un perceptrón:
# - Entradas: cada palabra del mensaje
# - Pesos: 1 si len(word) > 3, else 0
# - Función de activación: OR (any)
```

---

### 3. Redes Neuronales

El modelo `Qwen/Qwen2.5-7B-Instruct` es una **Red Neuronal Transformer** con 7 mil millones de parámetros.

#### Arquitectura del Modelo Utilizado

```
┌─────────────────────────────────────────────────────────────────┐
│                    Qwen2.5-7B-Instruct                          │
├─────────────────────────────────────────────────────────────────┤
│  Tipo: Large Language Model (LLM)                               │
│  Arquitectura: Transformer Decoder                              │
│  Parámetros: 7 Billones (7B)                                    │
│  Capas de Atención: Multi-Head Self-Attention                   │
│  Entrenamiento: Instruction-Tuning + RLHF                       │
└─────────────────────────────────────────────────────────────────┘

           ┌──────────────────────────────────────┐
           │          ARQUITECTURA INTERNA         │
           ├──────────────────────────────────────┤
Entrada    │  ┌─────────────────────────────┐     │
   ──────▶ │  │  Embedding Layer            │     │
           │  │  (Tokenización)             │     │
           │  └────────────┬────────────────┘     │
           │               ▼                       │
           │  ┌─────────────────────────────┐     │
           │  │  Transformer Blocks x N      │     │
           │  │  ┌─────────────────────┐    │     │
           │  │  │ Self-Attention      │    │     │
           │  │  ├─────────────────────┤    │     │
           │  │  │ Feed-Forward NN     │    │     │
           │  │  ├─────────────────────┤    │     │
           │  │  │ Layer Normalization │    │     │
           │  │  └─────────────────────┘    │     │
           │  └────────────┬────────────────┘     │
           │               ▼                       │
           │  ┌─────────────────────────────┐     │   Salida
           │  │  Output Layer               │─────┼──────▶
           │  │  (Generación de Tokens)     │     │
           │  └─────────────────────────────┘     │
           └──────────────────────────────────────┘
```

#### Parámetros de Generación en el Código

```python
# Línea 258-264
client = InferenceClient(token=HF_TOKEN)
resp = client.chat_completion(
    messages=[
        {"role": "system", "content": prompt}, 
        {"role": "user", "content": mensaje}
    ],
    model="Qwen/Qwen2.5-7B-Instruct",
    max_tokens=200,      # Limita la longitud de respuesta
    temperature=0.4      # Controla la creatividad
)
```

| Parámetro | Valor | Efecto |
|-----------|-------|--------|
| `max_tokens` | 200 | Respuestas concisas |
| `temperature` | 0.4 | Balance entre coherencia y creatividad |

> **Temperature baja (0.4)**: Respuestas más predecibles y consistentes, ideal para un chatbot de ventas.

---

### 4. Teorema de Esquemas

El **Teorema de Esquemas** de Holland establece que los patrones (esquemas) buenos se propagan y mejoran con el tiempo en algoritmos genéticos.

#### Aplicación: Sistema de Estados del Usuario

```python
# Estructura de Estados (Esquemas de Conversación)
user_state = {
    "sender_id_123": {
        "estado": "viendo_cat",     # Esquema actual
        "nombre": "Juan",
        "telefono": "5512345678",
        "carrito": [...],
        "prods_cat": [...],
        "idx": 0
    }
}
```

#### Diagrama de Estados (Esquemas)

```
                    ┌───────────────┐
                    │    INICIO     │ ◀──────────────────────┐
                    └───────┬───────┘                        │
                            │                                │
          ┌─────────────────┼─────────────────┐              │
          ▼                 ▼                 ▼              │
┌─────────────────┐ ┌───────────────┐ ┌───────────────┐      │
│   REG_NOMBRE    │ │    LOGIN      │ │  VIENDO_CAT   │      │
│   (Registro)    │ │   (Acceso)    │ │  (Catálogo)   │      │
└────────┬────────┘ └───────┬───────┘ └───────┬───────┘      │
         │                  │                 │              │
         ▼                  │                 │              │
┌─────────────────┐         │                 │              │
│    REG_TEL      │         │                 │              │
└────────┬────────┘         │                 │              │
         │                  │                 │              │
         ▼                  │                 │              │
┌─────────────────┐         │                 │              │
│    REG_DIR      │         │                 │              │
└────────┬────────┘         │                 │              │
         │                  ▼                 ▼              │
         │          ┌───────────────────────────┐            │
         └─────────▶│       LOGUEADO            │────────────┘
                    │   (Usuario Activo)        │  (cancelar)
                    └───────────────────────────┘
```

#### Analogía con el Teorema

```python
# El sistema "selecciona" esquemas exitosos basándose en transiciones
# Línea 284-317: Flujo de Registro

if estado == "reg_nombre":
    # El esquema "reg_nombre" evoluciona a "reg_tel"
    user_state[sender_id]["estado"] = "reg_tel"
    return "📱 Gracias. Ahora escribe tu teléfono (10 dígitos):"

if estado == "reg_tel":
    # Validación (fitness function del esquema)
    if not msg.isdigit() or len(msg) != 10: 
        return "❌ Número inválido."  # El esquema no pasa
    
    # El esquema evoluciona exitosamente
    user_state[sender_id]["estado"] = "reg_dir"
    return "📍 ¡Casi listo! Escribe tu dirección de entrega:"
```

> **Esquemas con Alta Aptitud**: Los estados que llevan a conversiones (pedidos completados) son los "esquemas dominantes" que el sistema preserva a través del flujo.

---

## Flujo de Procesamiento

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FLUJO COMPLETO                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. ENTRADA                                                          │
│     │                                                                │
│     ▼                                                                │
│  ┌─────────────────┐                                                 │
│  │ sanitizar_input │ ─── Limpia caracteres peligrosos                │
│  └────────┬────────┘                                                 │
│           ▼                                                          │
│  ┌─────────────────┐                                                 │
│  │   normalizar    │ ─── Lógica Fuzzy: normaliza texto               │
│  └────────┬────────┘                                                 │
│           ▼                                                          │
│  2. CLASIFICACIÓN                                                    │
│     │                                                                │
│     ▼                                                                │
│  ┌─────────────────────────────────────────┐                         │
│  │ manejar_mensaje() - Línea 273            │                        │
│  │ ┌─────────────────────────────────────┐ │                         │
│  │ │ ¿Comando conocido?                  │ │                         │
│  │ │ (hola, catalogo, buscar, etc.)      │ │                         │
│  │ └──────────────┬──────────────────────┘ │                         │
│  │                │                         │                         │
│  │    SÍ ◀────────┴────────▶ NO            │                         │
│  │    │                      │             │                         │
│  │    ▼                      ▼             │                         │
│  │ Respuesta              ┌────────────┐   │                         │
│  │ Predefinida            │consultar_ia│   │                         │
│  │                        └────────────┘   │                         │
│  └─────────────────────────────────────────┘                         │
│                                                                      │
│  3. GENERACIÓN (IA)                                                  │
│     │                                                                │
│     ▼                                                                │
│  ┌─────────────────────────────────────────┐                         │
│  │ Qwen2.5-7B (Red Neuronal Transformer)   │                         │
│  │ - Analiza contexto de productos         │                         │
│  │ - Genera respuesta conversacional       │                         │
│  └────────┬────────────────────────────────┘                         │
│           ▼                                                          │
│  4. SALIDA                                                           │
│     │                                                                │
│     ▼                                                                │
│  ┌─────────────────┐                                                 │
│  │ enviar_mensaje  │ ─── Envía a Facebook Messenger                  │
│  └─────────────────┘                                                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Modelo de IA Utilizado

### Qwen2.5-7B-Instruct

| Característica | Descripción |
|----------------|-------------|
| **Desarrollador** | Alibaba Cloud |
| **Tamaño** | 7 Billones de parámetros |
| **Tipo** | Decoder-only Transformer |
| **Entrenamiento** | Pre-training + Instruction Tuning |
| **Idiomas** | Multilingüe (incluye español) |
| **API** | Hugging Face Inference API |

### Prompt Engineering

El sistema utiliza **prompting estructurado** para guiar las respuestas:

```python
prompt = f"""
[DIRECTIVA] Eres 'Frere's Bot', un vendedor experto, amable y conversacional.
[IDIOMA] Responde SIEMPRE en ESPAÑOL (MÉXICO). Nunca uses otro idioma.
[DATOS] Usa este inventario real para responder preguntas:
{contexto_str}

[REGLAS DE AGILIDAD]
- Responde a la pregunta de manera directa.
- Si te preguntan por un producto específico, usa la Descripción del producto.
- Si el usuario pregunta por el stock o precio, dalo exacto.
- Sé breve y usa emojis.
"""
```

> Este prompt implementa **RAG (Retrieval-Augmented Generation)** al inyectar datos de productos relevantes en el contexto.

---

## Resumen de Conceptos

| Concepto | Aplicación en el Código |
|----------|-------------------------|
| **Lógica Fuzzy** | Normalización de texto, búsqueda parcial, detección de intenciones |
| **Perceptrón** | Rate limiting (suma + umbral), matching de relevancia |
| **Redes Neuronales** | Modelo Qwen2.5-7B para generación de respuestas |
| **Teorema de Esquemas** | Máquina de estados del usuario, flujos de conversación |

---

## Referencias

- [Hugging Face - Qwen2.5](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)
- [Firebase Firestore](https://firebase.google.com/docs/firestore)
- [Facebook Messenger Platform](https://developers.facebook.com/docs/messenger-platform/)

---

*Documento generado para el proyecto Frere's Chatbot*
*Última actualización: Diciembre 2024*
