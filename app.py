from flask import Flask, request
import requests
import logging
import os
import unicodedata
import string
import urllib.parse
from urllib.parse import urlparse, parse_qs 
from datetime import datetime, timedelta

# Librería para la IA
from huggingface_hub import InferenceClient

# Firebase
from conexion_firebase import obtener_productos, db
import firebase_admin
from firebase_admin import firestore

# ==========================================
# 1. CONFIGURACIÓN DEL SERVIDOR
# ==========================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Variables de Entorno
VERIFY_TOKEN = "freres_verificacion"
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

if not PAGE_ACCESS_TOKEN:
    print("❌ ERROR: Faltan credenciales de Facebook en Render.")

# Memoria Caché (RAM)
user_state = {}
productos_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 300 
}

# Límites de Seguridad
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
user_message_count = {} 

# ==========================================
# 2. HERRAMIENTAS Y UTILIDADES
# ==========================================
def normalizar(t):
    if not t: return ""
    t = t.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.translate(str.maketrans("", "", string.punctuation))

def verificar_rate_limit(sender_id):
    global user_message_count
    ahora = datetime.now()
    if sender_id not in user_message_count: user_message_count[sender_id] = []
    user_message_count[sender_id] = [ts for ts in user_message_count[sender_id] if (ahora - ts).total_seconds() < RATE_LIMIT_WINDOW]
    if len(user_message_count[sender_id]) >= RATE_LIMIT_MESSAGES: return False
    user_message_count[sender_id].append(ahora)
    return True

def sanitizar_input(texto):
    if not texto: return ""
    return texto[:500].replace('<', '').replace('>', '').strip()

# --- VALIDACIÓN DE IMÁGENES ---
def clean_google_url(url):
    if not url: return ""
    clean = url.strip()
    if "google." in clean and "imgurl=" in clean:
        try:
            parsed = urlparse(clean)
            query_params = parse_qs(parsed.query)
            if 'imgurl' in query_params:
                return query_params['imgurl'][0]
        except: pass
    return clean

def is_valid_image_url(url, timeout=2):
    if not url: return False
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Bot/1.0)'}
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        return r.status_code == 200 and 'image' in r.headers.get('Content-Type', '')
    except:
        return False

def get_img_url(datos):
    raw_url = datos.get("imagen_url", "")
    nombre_producto = datos.get("nombre", "Producto")
    clean_url = clean_google_url(raw_url)
    if clean_url and clean_url.startswith("http") and len(clean_url) > 10:
        if is_valid_image_url(clean_url):
            return clean_url
    nombre_safe = urllib.parse.quote_plus(nombre_producto)
    return f"https://placehold.co/300x300?text={nombre_safe}"

# ==========================================
# 3. COMUNICACIÓN CON FACEBOOK
# ==========================================
def enviar_mensaje(id_usuario, texto):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    try:
        requests.post(url, json={"recipient": {"id": id_usuario}, "message": {"text": texto}})
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

def enviar_imagen(id_usuario, url_img):
    if not url_img: return
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": id_usuario},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": url_img, "is_reusable": True}
            }
        }
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error enviando imagen: {e}")

# ==========================================
# 4. GESTIÓN DE DATOS (FIREBASE)
# ==========================================
def cargar_sesion(sender_id):
    try:
        doc = db.collection("sesiones").document(sender_id).get()
        return doc.to_dict() if doc.exists else None
    except: return None

def guardar_sesion(sender_id):
    try:
        estado = user_state.get(sender_id)
        if estado:
            estado["ultima_actividad"] = datetime.now()
            db.collection("sesiones").document(sender_id).set(estado)
    except: pass

def obtener_productos_con_cache():
    global productos_cache
    ahora = datetime.now()
    if productos_cache["data"] and productos_cache["timestamp"]:
        if (ahora - productos_cache["timestamp"]).total_seconds() < productos_cache["ttl"]:
            return productos_cache["data"]
    productos = obtener_productos()
    productos_cache["data"] = productos
    productos_cache["timestamp"] = ahora
    return productos

def reducir_stock(pid, cantidad):
    try:
        ref = db.collection("productos").document(pid)
        doc = ref.get()
        if not doc.exists: return False
        stock = int(doc.to_dict().get("stock", 0))
        if stock < cantidad: return False
        ref.update({"stock": stock - cantidad})
        productos_cache["data"] = None 
        return True
    except: return False

def registrar_conversion(sender_id, pedido_id, total):
    try:
        db.collection("analytics").add({
            "tipo": "conversion", "sender_id": sender_id, 
            "pedido_id": pedido_id, "total": total, "timestamp": datetime.now()
        })
    except: pass

# ==========================================
# 5. LÓGICA DE NEGOCIO Y IA
# ==========================================
def buscar_productos_clave(termino):
    prods = obtener_productos_con_cache()
    resultados = []
    t = normalizar(termino)
    for pid, d in prods.items():
        nombre = normalizar(d.get("nombre", ""))
        cat = normalizar(d.get("categoria", ""))
        if t in nombre or t in cat:
            d['id'] = pid
            d['imagen_url'] = get_img_url(d) 
            resultados.append(d)
    return resultados

def verificar_stock(pid):
    prods = obtener_productos_con_cache()
    if pid in prods:
        d = prods[pid]
        return {
            "nombre": d.get("nombre"),
            "stock": d.get("stock", 0),
            "imagen_url": get_img_url(d),
            "disponible": int(d.get("stock", 0)) > 0
        }
    return None

def mi_ultimo_pedido(telefono):
    try:
        docs = db.collection("pedidos").where("telefono", "==", telefono)\
                  .order_by("fecha", direction=firestore.Query.DESCENDING).limit(1).stream()
        for d in docs:
            ped = d.to_dict()
            ped['id'] = d.id
            return ped
    except: return None

def consultar_ia(sender_id, mensaje):
    if not HF_TOKEN: return "⚠️ IA desactivada (Falta Token)."
    try:
        prods = obtener_productos_con_cache()
        palabras = mensaje.lower().split()
        relevantes = []
        otros = []
        for pid, p in prods.items():
            texto_prod = (str(p.get("nombre")) + " " + str(p.get("categoria"))).lower()
            info = f"- {p.get('nombre')} (ID: {pid}) | ${p.get('precio')} | Stock: {p.get('stock')}"
            match = any(word in texto_prod for word in palabras if len(word) > 3)
            if match: relevantes.append(info)
            else: otros.append(info)
        
        lista_contexto = relevantes[:15] + otros[:5]
        contexto_str = "\n".join(lista_contexto)
        
        prompt = f"""
        [DIRECTIVA] Eres 'Frere's Bot', un vendedor experto.
        [IDIOMA] Responde SIEMPRE en ESPAÑOL (MÉXICO). Nunca uses otro idioma.
        [DATOS] Usa este inventario real:
        {contexto_str}
        [REGLAS]
        - Si preguntan precio/stock, dalo exacto.
        - Si no encuentras el producto en la lista, di amablemente que no lo tienes.
        - Sé breve y usa emojis.
        - Para vender: "Escribe 'pedido ID'".
        """
        
        client = InferenceClient(token=HF_TOKEN)
        resp = client.chat_completion(
            messages=[{"role":"system","content":prompt}, {"role":"user","content":mensaje}],
            model="Qwen/Qwen2.5-7B-Instruct",
            max_tokens=200, 
            temperature=0.3
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"Error IA: {e}")
        return "Dame un segundo, estoy revisando el almacén..."

# ==========================================
# 6. CEREBRO DEL BOT (Manejo de Mensajes)
# ==========================================
def manejar_mensaje(sender_id, msg):
    # Recuperamos el estado actual. Si no existe, es "inicio".
    usuario_data = user_state.get(sender_id, {})
    estado = usuario_data.get("estado", "inicio")
    prods_cache = obtener_productos_con_cache()

    # --- 0. COMANDO DE CANCELACIÓN (Prioridad Máxima) ---
    if msg == "cancelar":
        user_state[sender_id]["estado"] = "inicio"
        return "❌ Operación cancelada. ¿En qué puedo ayudarte?"

    # --- 1. FLUJOS ACTIVOS (Registro/Login) ---
    # Procesamos estos PRIMERO para evitar que palabras como "hola" rompan el flujo.

    if estado == "reg_nombre":
        user_state[sender_id]["nombre"] = msg # Guardamos lo que escribió (aunque esté normalizado)
        user_state[sender_id]["estado"] = "reg_tel"
        return "📱 Gracias. Ahora escribe tu teléfono (10 dígitos):"
    
    if estado == "reg_tel":
        # Verificamos si son dígitos (normalizar ya quitó guiones y espacios)
        if not msg.isdigit() or len(msg) != 10: 
            return "❌ Número inválido. Por favor escribe solo los 10 dígitos (Ej: 5512345678)."
        
        user_state[sender_id]["telefono"] = msg
        user_state[sender_id]["estado"] = "reg_dir"
        return "📍 ¡Casi listo! Escribe tu dirección de entrega:"
    
    if estado == "reg_dir":
        try:
            tel = user_state[sender_id].get("telefono")
            nombre = user_state[sender_id].get("nombre")
            
            # Guardamos en Firebase
            db.collection("usuarios").document(tel).set({
                "nombre": nombre,
                "telefono": tel,
                "direccion": msg, # Dirección ingresada
                "rol": "Cliente",
                "Fecha_registro": datetime.now().strftime("%d/%m/%y")
            })
            
            # Actualizamos sesión local
            user_state[sender_id]["estado"] = "logueado"
            user_state[sender_id]["direccion"] = msg
            return "✅ ¡Registro completado con éxito!\n\nYa puedes hacer pedidos. Escribe *catalogo* para ver nuestros productos."
        except Exception as e:
            print(f"Error Registro: {e}")
            user_state[sender_id]["estado"] = "inicio" # Reiniciamos por seguridad
            return "❌ Hubo un error al guardar tus datos. Intenta escribir *registrar* nuevamente."

    if estado == "login":
        doc = db.collection("usuarios").document(msg).get()
        if not doc.exists: 
            return "❌ No encontré ese número. Verifica o escribe *registrar* para crear cuenta."
        
        d = doc.to_dict()
        # Recuperar carrito si tenía
        cart = user_state.get(sender_id, {}).get("carrito", [])
        
        # Guardar sesión logueada
        user_state[sender_id] = {
            "estado": "logueado", 
            "nombre": d.get('nombre'), 
            "telefono": msg, 
            "direccion": d.get('direccion'), 
            "carrito": cart
        }
        return f"👋 ¡Bienvenido de nuevo, {d.get('nombre')}!"

    # --- 2. ACTIVADORES DE REGISTRO / LOGIN ---
    # Si no hay flujo activo, buscamos comandos.
    
    if "registrar" in msg or "crear cuenta" in msg:
        user_state[sender_id] = {"estado": "reg_nombre"} # Iniciamos flujo limpio
        return "📝 ¡Bienvenido! Para registrarte, primero escribe tu nombre completo:"

    if msg.startswith("iniciar") or "entrar" in msg or "login" in msg:
        user_state[sender_id] = {"estado": "login"}
        return "🔐 Por favor, escribe tu número de teléfono registrado:"

    # --- 3. COMANDOS GENERALES ---

    if any(x in msg for x in ["hola", "inicio", "menu", "buenos dias", "buenas tardes"]):
        return "👋 ¡Hola! Soy Frere's Bot.\n\nEscribe:\n🛍 *Catalogo*\n🔍 *Buscar (producto)*\n🆕 *Novedades*\n📦 *Mi Pedido*\n👤 *Registrar / Entrar*"

    if "contacto" in msg: return "📞 WhatsApp: 55-1234-5678"
    
    # --- NOVEDADES / OFERTAS ---
    if any(x in msg for x in ["nuevo", "novedad", "oferta"]):
        es_oferta = "oferta" in msg
        items = []
        for pid, d in prods_cache.items():
            if (es_oferta and d.get('oferta')) or (not es_oferta):
                d['id'] = pid
                d['imagen_url'] = get_img_url(d) 
                items.append(d)
        
        items = items[:3]
        if not items: return "No encontré productos en esta sección."
        
        titulo = "🔥 *OFERTAS:*" if es_oferta else "🆕 *NOVEDADES:*"
        enviar_mensaje(sender_id, titulo)

        for p in items:
            txt = f"🔹 *{p['nombre']}*\n💲 ${p['precio']}\n🆔 ID: {p['id']}\nStock: {p['stock']}"
            enviar_mensaje(sender_id, txt)
            enviar_imagen(sender_id, p['imagen_url'])
        return None 

    # --- BÚSQUEDA ---
    if msg.startswith("buscar"):
        term = msg.replace("buscar", "").strip()
        if len(term) < 2: return "🔍 Escribe: *buscar camisa*"
        
        items = buscar_productos_clave(term)
        if not items: return f"😕 No encontré '{term}'."
        
        enviar_mensaje(sender_id, f"🔍 Resultados para '{term}':")
        for p in items[:3]:
            txt = f"🔸 *{p['nombre']}*\n💲 ${p['precio']}\n🆔 ID: {p['id']}"
            enviar_mensaje(sender_id, txt)
            enviar_imagen(sender_id, p['imagen_url'])
            
        if len(items) > 3: enviar_mensaje(sender_id, "ℹ️ Hay más resultados, intenta ser más específico.")
        return None

    # --- STOCK POR ID ---
    if msg.startswith("stock"):
        import re
        m = re.search(r'\d+', msg)
        if not m: return "📦 Escribe: *stock ID*"
        info = verificar_stock(m.group(0))
        if not info: return "❌ ID no encontrado."
        txt = f"📦 *{info['nombre']}*\nStock: {info['stock']} unidades"
        if not info['disponible']: txt += " (Agotado)"
        enviar_mensaje(sender_id, txt)
        enviar_imagen(sender_id, info['imagen_url'])
        return None

    # --- CARRITO ---
    if "carrito" in msg and "ver" in msg:
        c = user_state.get(sender_id, {}).get("carrito", [])
        if not c: return "🛒 Tu carrito está vacío."
        txt = "🛒 *Tu Pedido:*\n"
        total = 0
        for it in c:
            sub = it['precio'] * it['cantidad']
            total += sub
            txt += f"- {it['cantidad']}x {it['nombre']} (${sub})\n"
        txt += f"\n💰 Total: ${total}\nEscribe *finalizar* para comprar."
        return txt

    if "vaciar" in msg:
        if sender_id in user_state: user_state[sender_id]["carrito"] = []
        return "🗑️ Carrito vaciado."

    # --- PEDIDOS ---
    if "mi pedido" in msg:
        tel = user_state.get(sender_id, {}).get("telefono")
        if not tel: return "🔒 Inicia sesión (escribe *entrar*) para ver tus pedidos."
        ped = mi_ultimo_pedido(tel)
        if not ped: return "No tienes pedidos recientes."
        return f"🧾 Pedido #{ped['id']}\nEstado: {ped.get('estado')}\nTotal: ${ped.get('total')}"

    # --- CATÁLOGO ---
    all_cats_map = {} 
    for p in prods_cache.values():
        c_raw = p.get('categoria', 'Varios')
        all_cats_map[normalizar(c_raw)] = c_raw

    if "catalogo" in msg:
        if sender_id not in user_state: user_state[sender_id] = {"estado": "inicio"}
        cats_uniques = list(set(all_cats_map.values()))
        return "📂 *Categorías Disponibles:*\n" + "\n".join([f"- {c}" for c in cats_uniques]) + "\n\nEscribe el nombre de una categoría."

    # Detección de Categoría
    cat_match = None
    if msg in all_cats_map: cat_match = msg
    
    if cat_match:
        cat_real_name = all_cats_map[cat_match]
        prods = [p for pid, p in prods_cache.items() if normalizar(p.get('categoria', '')) == cat_match]
        
        if not prods: return f"La categoría '{cat_real_name}' está vacía."

        for p in prods: p['imagen_url'] = get_img_url(p)
        user_state[sender_id]["prods_cat"] = prods
        user_state[sender_id]["idx"] = 0
        user_state[sender_id]["estado"] = "viendo_cat"
        
        p = prods[0]
        txt = f"📂 *Categoría: {cat_real_name}*\n\n🔹 *{p['nombre']}*\n💲 ${p['precio']}\n\nEscribe *si* para agregar al carrito, o *no* para ver el siguiente."
        enviar_mensaje(sender_id, txt)
        enviar_imagen(sender_id, p['imagen_url'])
        return None

    if estado == "viendo_cat":
        if msg in ["no", "siguiente", "otro"]:
            idx = user_state[sender_id]["idx"] + 1
            prods = user_state[sender_id]["prods_cat"]
            if idx >= len(prods):
                user_state[sender_id]["estado"] = "logueado" if user_state[sender_id].get("telefono") else "inicio"
                return "🏁 Fin de la categoría. Escribe *catalogo* para ver otras."
            user_state[sender_id]["idx"] = idx
            p = prods[idx]
            txt = f"🔹 *{p['nombre']}*\n💲 ${p['precio']}\n\n¿Lo agregamos?"
            enviar_mensaje(sender_id, txt)
            enviar_imagen(sender_id, p['imagen_url'])
            return None
        
        if msg in ["si", "lo quiero", "agregar"]:
            prods = user_state[sender_id]["prods_cat"]
            p = prods[user_state[sender_id]["idx"]]
            cart = user_state[sender_id].setdefault("carrito", [])
            cart.append({"id": "CATALOGO", "nombre": p['nombre'], "precio": p['precio'], "cantidad": 1})
            return "🛒 Agregado. Escribe *siguiente* para ver más o *finalizar* para pagar."

    # --- AGREGAR POR ID ---
    if msg.startswith("pedido") or (msg.isdigit() and len(msg) <= 4):
        import re
        pid_match = re.search(r'\d+', msg)
        if pid_match:
            pid = pid_match.group(0)
            info = verificar_stock(pid)
            if info and info['disponible']:
                cart = user_state.setdefault(sender_id, {}).setdefault("carrito", [])
                prods = obtener_productos_con_cache()
                precio = prods[pid]['precio']
                cart.append({"id": pid, "nombre": info['nombre'], "precio": precio, "cantidad": 1})
                return f"🛒 {info['nombre']} agregado al carrito."
            return "❌ ID no válido o producto agotado."

    # --- FINALIZAR PEDIDO ---
    if "finalizar" in msg or "comprar" in msg:
        cart = user_state.get(sender_id, {}).get("carrito")
        if not cart: return "🛒 Tu carrito está vacío."
        
        if not user_state.get(sender_id, {}).get("telefono"):
            return "🛑 Para procesar tu compra necesito saber quién eres.\n\nEscribe *registrar* (si eres nuevo) o *entrar*."
        
        total = sum([i['precio'] * i['cantidad'] for i in cart])
        pedido = {
            "telefono": user_state[sender_id]["telefono"],
            "nombre": user_state[sender_id]["nombre"],
            "fecha": datetime.now(),
            "estado": "pendiente",
            "productos": cart,
            "total": total,
            "entrega": "pendiente"
        }
        ref = db.collection("pedidos").add(pedido)
        for item in cart:
            if item['id'] != "CATALOGO": reducir_stock(item['id'], item['cantidad'])
        registrar_conversion(sender_id, ref[1].id, total)
        user_state[sender_id]["carrito"] = []
        return f"✅ ¡Pedido #{ref[1].id} Recibido!\nTotal: ${total}\nNos pondremos en contacto contigo."

    # --- IA POR DEFECTO ---
    return consultar_ia(sender_id, msg)

# ==========================================
# 7. ENDPOINTS FLASK
# ==========================================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN: return request.args.get("hub.challenge")
    return "Error de validación", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and not event["message"].get("is_echo"):
                    sender_id = event["sender"]["id"]
                    text = event["message"].get("text", "")
                    
                    if verificar_rate_limit(sender_id):
                        text = sanitizar_input(text)
                        msg_norm = normalizar(text)
                        
                        if sender_id not in user_state:
                            s = cargar_sesion(sender_id)
                            if s: user_state[sender_id] = s
                        
                        resp = manejar_mensaje(sender_id, msg_norm)
                        if resp:
                            enviar_mensaje(sender_id, resp)
                        
                        guardar_sesion(sender_id)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)