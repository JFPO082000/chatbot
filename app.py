from flask import Flask, request
import requests
import logging
import os
import unicodedata
import string
import urllib.parse  # ✅ IMPORTANTE: Para codificar el nombre en la URL de la imagen
from datetime import datetime, timedelta

# Importamos la librería de Hugging Face para la IA
from huggingface_hub import InferenceClient

# Firebase - Importamos las funciones de tus otros archivos
from conexion_firebase import obtener_productos, db
import firebase_admin
from firebase_admin import firestore

# ------------------------------------------------------------
# CONFIGURACIÓN DEL SERVIDOR
# ------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Tokens de seguridad (Variables de Entorno en Render)
VERIFY_TOKEN = "freres_verificacion"
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")  # Token para la IA

if not PAGE_ACCESS_TOKEN:
    print("❌ ERROR: No se encontró PAGE_ACCESS_TOKEN en Render.")
if not HF_TOKEN:
    print("⚠️ ADVERTENCIA: No se encontró HF_TOKEN. La IA no funcionará.")

# Memoria temporal (RAM)
user_state = {}
productos_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 300  # 5 minutos
}

# Límites y seguridad
SESSION_TIMEOUT = 1800 
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
user_message_count = {} 

# ------------------------------------------------------------
# UTILIDADES
# ------------------------------------------------------------
def normalizar(t):
    if not t: return ""
    t = t.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.translate(str.maketrans("", "", string.punctuation))
    return " ".join(t.split())

def verificar_rate_limit(sender_id):
    global user_message_count
    ahora = datetime.now()
    if sender_id not in user_message_count:
        user_message_count[sender_id] = []
    # Limpiar viejos
    user_message_count[sender_id] = [ts for ts in user_message_count[sender_id] if (ahora - ts).total_seconds() < RATE_LIMIT_WINDOW]
    if len(user_message_count[sender_id]) >= RATE_LIMIT_MESSAGES:
        return False
    user_message_count[sender_id].append(ahora)
    return True

def sanitizar_input(texto):
    if not texto: return ""
    return texto[:500].replace('<', '').replace('>', '').strip()

# ✅ NUEVA FUNCIÓN: GENERADOR DE IMÁGENES AUTOMÁTICO
def get_img_url(datos):
    """
    Si el producto tiene URL en Firebase, la usa.
    Si no, genera una imagen automática con el nombre del producto.
    """
    url = datos.get("imagen_url", "")
    # Si existe y parece un link válido, úsalo
    if url and url.startswith("http") and len(url) > 10:
        return url
    
    # Si no, crea un placeholder
    nombre = datos.get("nombre", "Producto")
    # Limpiamos el nombre para que funcione en la URL (ej: "Tenis Nike" -> "Tenis+Nike")
    nombre_safe = urllib.parse.quote_plus(nombre)
    return f"https://placehold.co/300x300?text={nombre_safe}"

# ------------------------------------------------------------
# COMUNICACIÓN CON FACEBOOK
# ------------------------------------------------------------
def enviar_mensaje(id_usuario, texto):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": id_usuario}, "message": {"text": texto}})

def enviar_imagen(id_usuario, url_img):
    if not url_img or not url_img.startswith("http"): return
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
    try: requests.post(url, json=payload)
    except: pass

# ------------------------------------------------------------
# GESTIÓN DE DATOS (FIREBASE)
# ------------------------------------------------------------
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
    
    # Llamamos a la función real de Firebase (importada)
    productos = obtener_productos()
    productos_cache["data"] = productos
    productos_cache["timestamp"] = ahora
    return productos

# ------------------------------------------------------------
# ANALYTICS & STOCK
# ------------------------------------------------------------
def registrar_mensaje(sender_id, mensaje, tipo="recibido"):
    try:
        db.collection("analytics").add({
            "tipo": "mensaje", "sender_id": sender_id, "mensaje": mensaje,
            "direccion": tipo, "timestamp": datetime.now()
        })
    except: pass

def registrar_busqueda(sender_id, termino, count):
    try:
        db.collection("analytics").add({
            "tipo": "busqueda", "sender_id": sender_id, "termino": termino,
            "resultados": count, "timestamp": datetime.now()
        })
    except: pass

def registrar_conversion(sender_id, pedido_id, total):
    try:
        db.collection("analytics").add({
            "tipo": "conversion", "sender_id": sender_id, "pedido_id": pedido_id,
            "total": total, "timestamp": datetime.now()
        })
    except: pass

def reducir_stock(pid, cantidad):
    try:
        ref = db.collection("productos").document(pid)
        doc = ref.get()
        if not doc.exists: return False
        stock = doc.to_dict().get("stock", 0)
        if stock < cantidad: return False
        ref.update({"stock": stock - cantidad})
        productos_cache["data"] = None # Invalidar caché
        return True
    except: return False

# ------------------------------------------------------------
# CONSULTAS DE PRODUCTOS (CON FOTOS AUTOMÁTICAS)
# ------------------------------------------------------------
def productos_nuevos(dias=30):
    prods = obtener_productos_con_cache()
    res = []
    hoy = datetime.now()
    for pid, d in prods.items():
        if len(res) < 5: 
            d['id'] = pid
            # ✅ Aseguramos imagen
            d['imagen_url'] = get_img_url(d)
            res.append(d)
    return res

def productos_en_oferta():
    prods = obtener_productos_con_cache()
    res = []
    for pid, d in prods.items():
        if d.get('oferta'):
            d['id'] = pid
            # ✅ Aseguramos imagen
            d['imagen_url'] = get_img_url(d)
            res.append(d)
    return res

def buscar_producto(termino):
    prods = obtener_productos_con_cache()
    res = []
    t = normalizar(termino)
    for pid, d in prods.items():
        if t in normalizar(d.get('nombre', '')):
            d['id'] = pid
            # ✅ Aseguramos imagen
            d['imagen_url'] = get_img_url(d)
            res.append(d)
    return res

def verificar_stock(pid):
    prods = obtener_productos_con_cache()
    if pid in prods:
        d = prods[pid]
        return {
            "nombre": d.get("nombre"), 
            "stock": d.get("stock", 0), 
            "imagen_url": get_img_url(d), # ✅ Aseguramos imagen
            "disponible": d.get("stock", 0) > 0
        }
    return None

def mi_ultimo_pedido(telefono):
    try:
        docs = db.collection("pedidos").where("telefono", "==", telefono).order_by("fecha", direction=firestore.Query.DESCENDING).limit(1).stream()
        for d in docs: 
            ped = d.to_dict()
            ped['id'] = d.id
            return ped
    except: return None

# ------------------------------------------------------------
# INTELIGENCIA ARTIFICIAL (QWEN)
# ------------------------------------------------------------
def consultar_ia(sender_id, mensaje):
    if not HF_TOKEN: return "⚠️ Servicio de IA no disponible."
    
    try:
        prods = obtener_productos_con_cache()
        
        # Filtro inteligente (Smart Search)
        palabras = mensaje.lower().split()
        relevantes = []
        otros = []
        
        for pid, p in prods.items():
            texto = (str(p.get("nombre")) + " " + str(p.get("categoria"))).lower()
            info = f"- {p.get('nombre')} (ID: {pid}) | ${p.get('precio')} | Stock: {p.get('stock')}"
            
            if any(w in texto for w in palabras if len(w)>3):
                relevantes.append(info)
            else:
                otros.append(info)
        
        contexto = "\n".join(relevantes[:15] + otros[:10])
        
        prompt = f"""
        [ROL] Eres el asistente de ventas de 'Frere's Collection'.
        [REGLA 1] HABLA SOLO ESPAÑOL (MÉXICO). NUNCA uses otro idioma.
        [REGLA 2] Usa SOLO estos datos:
        {contexto}
        
        Si preguntan qué tienes, resume. Si no encuentras algo, di que no hay.
        Para comprar: "Escribe 'pedido ID'".
        Sé muy breve.
        """
        
        client = InferenceClient(token=HF_TOKEN)
        resp = client.chat_completion(
            messages=[{"role":"system","content":prompt}, {"role":"user","content":mensaje}],
            model="Qwen/Qwen2.5-7B-Instruct",
            max_tokens=150, temperature=0.3
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"IA Error: {e}")
        return "Dame un momento, estoy verificando el inventario..."

# ------------------------------------------------------------
# LÓGICA DEL BOT
# ------------------------------------------------------------
def manejar_mensaje(sender_id, msg):
    estado = user_state.get(sender_id, {}).get("estado", "inicio")

    # Comandos básicos
    if any(x in msg for x in ["hola", "inicio", "empezar"]):
        return "👋 ¡Hola! Soy Frere's Bot.\n\nEscribe:\n🛍 *Catalogo*\n🔍 *Buscar (producto)*\n📦 *Mi Pedido*\n🆕 *Novedades*\n📞 *Contacto*"

    if "contacto" in msg: return "📞 WhatsApp: 55-1234-5678"
    if "horario" in msg: return "🕒 Lunes a Sábado: 10am - 7pm"

    # Consultas Visuales
    if any(x in msg for x in ["nuevo", "novedad"]):
        items = productos_nuevos()
        if not items: return "Nada nuevo por hoy."
        txt = "🆕 *Recién llegados:*\n"
        for i, p in enumerate(items[:5]):
            if i<3: enviar_imagen(sender_id, p.get("imagen_url"))
            txt += f"🔹 {p['nombre']} (${p['precio']}) - ID: {p['id']}\n"
        return txt

    if "oferta" in msg:
        items = productos_en_oferta()
        if not items: return "Sin ofertas hoy."
        txt = "🔥 *Ofertas:*\n"
        for i, p in enumerate(items[:5]):
            if i<3: enviar_imagen(sender_id, p.get("imagen_url"))
            txt += f"💥 {p['nombre']} (${p['precio']}) - ID: {p['id']}\n"
        return txt

    if msg.startswith("buscar"):
        term = msg.replace("buscar","").strip()
        if len(term)<2: return "Escribe: *buscar tenis*"
        items = buscar_producto(term)
        registrar_busqueda(sender_id, term, len(items))
        if not items: return "No encontré nada similar."
        txt = f"🔍 *Resultados para '{term}':*\n"
        for i, p in enumerate(items[:5]):
            enviar_imagen(sender_id, p.get("imagen_url"))
            txt += f"🔸 {p['nombre']} (${p['precio']}) - ID: {p['id']}\n"
        return txt

    if msg.startswith("stock"):
        import re
        m = re.search(r'\d+', msg)
        if not m: return "Escribe: *stock ID*"
        info = verificar_stock(m.group(0))
        if not info: return "ID no encontrado."
        enviar_imagen(sender_id, info.get("imagen_url"))
        return f"📦 {info['nombre']}\nStock: {info['stock']}"

    # Carrito y Pedidos
    if "carrito" in msg and "ver" in msg:
        c = user_state.get(sender_id, {}).get("carrito", [])
        if not c: return "🛒 Carrito vacío."
        txt = "🛒 *Tu Carrito:*\n"
        total = 0
        for it in c:
            sub = it['precio']*it['cantidad']
            total += sub
            txt += f"- {it['cantidad']}x {it['nombre']} (${sub})\n"
        txt += f"\nTotal: ${total}\nEscribe *finalizar* para comprar."
        return txt

    if "vaciar" in msg and "carrito" in msg:
        if sender_id in user_state: user_state[sender_id]["carrito"] = []
        return "🗑️ Carrito vaciado."

    if any(x in msg for x in ["mi pedido", "mis pedidos"]):
        tel = user_state.get(sender_id, {}).get("telefono")
        if not tel: return "🔒 Inicia sesión para ver tus pedidos."
        ped = mi_ultimo_pedido(tel)
        if not ped: return "No tienes pedidos recientes."
        return f"🧾 Pedido {ped['id']}\nEstado: {ped.get('estado')}\nTotal: ${ped.get('total')}"

    # Autenticación
    if msg in ["registrar", "crear cuenta"]:
        user_state[sender_id] = {"estado": "reg_nombre"}
        return "📝 Nombre completo:"
    
    if estado == "reg_nombre":
        user_state[sender_id]["nombre"] = msg
        user_state[sender_id]["estado"] = "reg_tel"
        return "📱 Teléfono (10 dígitos):"
    
    if estado == "reg_tel":
        if not msg.isdigit() or len(msg)!=10: return "Número inválido."
        user_state[sender_id]["telefono"] = msg
        user_state[sender_id]["estado"] = "reg_dir"
        return "📍 Dirección de entrega:"
    
    if estado == "reg_dir":
        try:
            db.collection("usuarios").document(user_state[sender_id]["telefono"]).set({
                "nombre": user_state[sender_id]["nombre"],
                "telefono": user_state[sender_id]["telefono"],
                "direccion": msg,
                "rol": "Cliente",
                "Fecha_registro": datetime.now().strftime("%d/%m/%y")
            })
            user_state[sender_id]["estado"] = "logueado"
            user_state[sender_id]["direccion"] = msg
            return "✅ ¡Registro exitoso! Ya puedes comprar."
        except: return "Error al registrar."

    if msg.startswith("iniciar") or msg=="entrar":
        user_state[sender_id] = {"estado": "login"}
        return "🔐 Tu teléfono registrado:"
    
    if estado == "login":
        doc = db.collection("usuarios").document(msg).get()
        if not doc.exists: return "No existe. Escribe *registrar*."
        d = doc.to_dict()
        cart = user_state.get(sender_id, {}).get("carrito", [])
        user_state[sender_id] = {"estado":"logueado", "nombre":d['nombre'], "telefono":msg, "direccion":d.get('direccion'), "carrito":cart}
        return f"👋 Hola de nuevo, {d['nombre']}."

    # Flujo de Compra
    if "catalogo" in msg:
        if sender_id not in user_state: user_state[sender_id] = {"estado": "inicio"}
        cats = set([p['categoria'] for p in obtener_productos_con_cache().values()])
        user_state[sender_id]["cats_pend"] = list(cats)
        return "📂 *Categorías:*\n" + "\n".join([f"- {c}" for c in cats]) + "\n\nEscribe el nombre de una."

    if msg in [c.lower() for c in user_state.get(sender_id, {}).get("cats_pend", [])]:
        # Mostrar productos de la categoría (con fotos)
        prods = [p for p in obtener_productos_con_cache().values() if p['categoria'].lower() == msg]
        user_state[sender_id]["prods_cat"] = prods
        user_state[sender_id]["idx"] = 0
        user_state[sender_id]["estado"] = "viendo_cat"
        
        if not prods: return "Vacío."
        p = prods[0]
        # ✅ AQUÍ TAMBIÉN GENERAMOS IMAGEN SI FALTA
        url_img = get_img_url(p)
        enviar_imagen(sender_id, url_img)
        return f"🔹 {p['nombre']}\n💲 ${p['precio']}\n\nEscribe *si* para agregar, o *no* para siguiente."

    if estado == "viendo_cat":
        if msg in ["no", "siguiente"]:
            idx = user_state[sender_id]["idx"] + 1
            prods = user_state[sender_id]["prods_cat"]
            if idx >= len(prods):
                user_state[sender_id]["estado"] = "logueado"
                return "Fin de categoría. Escribe *finalizar* o *catalogo*."
            user_state[sender_id]["idx"] = idx
            p = prods[idx]
            # ✅ AQUÍ TAMBIÉN
            url_img = get_img_url(p)
            enviar_imagen(sender_id, url_img)
            return f"🔹 {p['nombre']}\n💲 ${p['precio']}\n\n¿Lo quieres?"
        
        if msg in ["si", "lo quiero"]:
            prods = user_state[sender_id]["prods_cat"]
            p = prods[user_state[sender_id]["idx"]]
            cart = user_state[sender_id].setdefault("carrito", [])
            cart.append({"id": "CAT", "nombre": p['nombre'], "precio": p['precio'], "cantidad": 1})
            return "🛒 Agregado. Escribe *siguiente* o *finalizar*."

    # Agregar directo por ID
    if msg.startswith("pedido") or (msg.isdigit() and len(msg)<=4):
        import re
        pid = re.search(r'\d+', msg).group(0)
        info = verificar_stock(pid)
        if info and info['disponible']:
            cart = user_state.setdefault(sender_id, {}).setdefault("carrito", [])
            cart.append({"id": pid, "nombre": info['nombre'], "precio": obtener_productos_con_cache()[pid]['precio'], "cantidad": 1})
            return f"🛒 {info['nombre']} agregado."
        return "ID no válido o agotado."

    if "finalizar" in msg or "comprar" in msg:
        cart = user_state.get(sender_id, {}).get("carrito")
        if not cart: return "Carrito vacío."
        
        # VALIDACIÓN: Solo permitir si hay teléfono (login)
        if not user_state.get(sender_id, {}).get("telefono"):
            return "🛑 Para comprar, necesitas identificarte.\nEscribe *registrar* o *iniciar sesion*."
        
        total = sum([i['precio']*i['cantidad'] for i in cart])
        
        # Guardar pedido
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
        
        # Bajar stock
        for item in cart:
            if item['id'] != "CAT": reducir_stock(item['id'], item['cantidad'])
            
        registrar_conversion(sender_id, ref[1].id, total)
        user_state[sender_id]["carrito"] = [] # Limpiar
        
        return f"✅ Pedido #{ref[1].id} creado.\nTotal: ${total}\nTe contactaremos para el pago."

    # IA por defecto
    return consultar_ia(sender_id, msg)

# ------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN: return request.args.get("hub.challenge")
    return "Error", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if "message" in event and not event["message"].get("is_echo"):
                sender_id = event["sender"]["id"]
                text = event["message"].get("text", "")
                
                if verificar_rate_limit(sender_id):
                    text = sanitizar_input(text)
                    msg_norm = normalizar(text)
                    registrar_mensaje(sender_id, msg_norm)
                    
                    if sender_id not in user_state:
                        s = cargar_sesion(sender_id)
                        if s: user_state[sender_id] = s
                    
                    resp = manejar_mensaje(sender_id, msg_norm)
                    if resp:
                        enviar_mensaje(sender_id, resp)
                        registrar_mensaje(sender_id, resp, "enviado")
                    
                    guardar_sesion(sender_id)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)