
from flask import Flask, request
import requests
import logging
import os
import unicodedata
import string
from datetime import datetime

# Firebase
from conexion_firebase import obtener_productos
import firebase_admin
from firebase_admin import firestore

# Cliente Firestore
db = firestore.client()

# ------------------------------------------------------------
# CONFIG SERVIDOR
# ------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

VERIFY_TOKEN = "freres_verificacion"
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")

if not PAGE_ACCESS_TOKEN:
    print("❌ ERROR: No se encontró PAGE_ACCESS_TOKEN en Render.")
else:
    print("✅ PAGE_ACCESS_TOKEN cargado correctamente.")

# Estados de usuario en memoria
user_state = {}


# ------------------------------------------------------------
# NORMALIZACIÓN DE TEXTO
# ------------------------------------------------------------
def normalizar(t):
    if not t:
        return ""
    t = t.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.translate(str.maketrans("", "", string.punctuation))
    t = " ".join(t.split())
    return t


# ------------------------------------------------------------
# ENVÍO DE MENSAJES
# ------------------------------------------------------------
def enviar_mensaje(id_usuario, texto):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": id_usuario},
        "message": {"text": texto}
    }
    requests.post(url, json=payload)


def enviar_imagen(id_usuario, url_img):
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
    requests.post(url, json=payload)


# ------------------------------------------------------------
# AUXILIARES (CATEGORÍAS, PRODUCTOS, CARRITO)
# ------------------------------------------------------------
def construir_categorias(sender_id):
    productos = obtener_productos()
    categorias = {}

    for p in productos.values():
        cat = p.get("categoria", "Sin categoria")
        categorias[cat] = categorias.get(cat, 0) + 1

    lista = list(categorias.keys())

    user_state.setdefault(sender_id, {})
    user_state[sender_id]["estado"] = "elige_categoria"
    user_state[sender_id]["categorias_pendientes"] = lista
    user_state[sender_id].setdefault("carrito", [])

    if not lista:
        return "😕 No hay categorías con productos disponibles."

    msg = "🛍 *Categorías disponibles:*\n\n"
    for i, c in enumerate(lista, 1):
        msg += f"{i}. {c}\n"

    msg += "\n👉 Escribe el número o el nombre de la categoría que quieres ver."
    return msg


def preparar_categoria(sender_id, categoria):
    productos = obtener_productos()
    lista = []

    for idp, datos in productos.items():
        if datos.get("categoria", "").lower() == categoria.lower():
            lista.append({"id": idp, "datos": datos})

    user_state[sender_id]["categoria_actual"] = categoria
    user_state[sender_id]["productos_categoria"] = lista
    user_state[sender_id]["indice_producto"] = 0

    return len(lista) > 0


def mostrar_producto(sender_id):
    estado = user_state.get(sender_id, {})
    productos = estado.get("productos_categoria", [])
    idx = estado.get("indice_producto", 0)

    if idx >= len(productos):
        return fin_categoria(sender_id)

    prod = productos[idx]
    pid = prod["id"]
    datos = prod["datos"]

    nombre = datos.get("nombre", "Sin nombre")
    precio = datos.get("precio", "N/A")
    img = datos.get("imagen_url", "")

    if img:
        enviar_imagen(sender_id, img)

    txt = (
        f"🔹 *{nombre}*\n"
        f"💰 ${precio} MXN\n"
        f"🆔 ID: {pid}\n\n"
        "Para agregarlo al pedido puedes escribir:\n"
        f"• *si {pid}*\n"
        f"• *sí {pid}*\n"
        f"• *pedido {pid}*\n"
        f"• o solo el ID: *{pid}*\n\n"
        "Para pasar al siguiente: *no* o *siguiente*\n"
        "Para terminar: *finalizar pedido*"
    )
    return txt


def fin_categoria(sender_id):
    estado = user_state[sender_id]
    cat_actual = estado.get("categoria_actual")
    pendientes = estado.get("categorias_pendientes", [])
    carrito = estado.get("carrito", [])

    if cat_actual in pendientes:
        pendientes.remove(cat_actual)

    if pendientes:
        estado["estado"] = "elige_categoria"
        msg = f"✔ Ya no hay más productos en *{cat_actual}*.\n\n"
        msg += "Otras categorías disponibles:\n"
        for i, c in enumerate(pendientes, 1):
            msg += f"{i}. {c}\n"
        msg += "\n👉 Escribe la siguiente categoría o *finalizar pedido*."
        return msg
    else:
        if carrito:
            return finalizar_pedido(sender_id)
        else:
            estado["estado"] = "logueado"
            return (
                "No hay más categorías con productos y no agregaste nada al carrito.\n"
                "Escribe *catalogo* para empezar de nuevo."
            )


def agregar_carrito(sender_id, pid):
    productos = obtener_productos()
    if pid not in productos:
        return "❌ Ese ID de producto no existe."

    datos = productos[pid]
    nombre = datos.get("nombre", "Sin nombre")
    precio = datos.get("precio", 0)
    categoria = datos.get("categoria", "Sin categoria")

    user_state[sender_id].setdefault("carrito", [])
    user_state[sender_id]["carrito"].append({
        "id": pid,
        "nombre": nombre,
        "precio": precio,
        "categoria": categoria
    })

    return f"🛒 *{nombre}* agregado a tu pedido."


def finalizar_pedido(sender_id):
    estado = user_state[sender_id]
    carrito = estado.get("carrito", [])

    if not carrito:
        return "🛍 No tienes productos en tu pedido. Escribe *catalogo* para ver productos."

    total = 0
    for item in carrito:
        try:
            total += float(item.get("precio", 0))
        except Exception:
            pass

    pedido = {
        "telefono": estado.get("telefono"),
        "nombre": estado.get("nombre"),
        "fecha": datetime.now(),
        "estado": "pendiente",
        "productos": carrito,
        "total": total
    }

    # Forma segura: crear doc manualmente y hacer set
    doc_ref = db.collection("pedidos").document()
    doc_ref.set(pedido)
    pedido_id = doc_ref.id

    # Guardar en estado para el paso de entrega
    user_state[sender_id]["estado"] = "elige_entrega"
    user_state[sender_id]["ultimo_pedido_id"] = pedido_id

    msg = (
        f"🧾 *Pedido registrado*: {pedido_id}\n\n"
        "📦 ¿Cómo deseas recibirlo?\n"
        "• *Domicilio*\n"
        "• *Recoger en tienda*\n\n"
        "Escribe una opción."
    )
    return msg


# ------------------------------------------------------------
# CONSULTA DE PEDIDO POR ID
# ------------------------------------------------------------
def consultar_pedido_por_id(pid):
    doc = db.collection("pedidos").document(pid).get()
    if not doc.exists:
        return None
    return doc.to_dict()


# ------------------------------------------------------------
# WEBHOOK (VERIFICACIÓN)
# ------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token inválido", 403


# ------------------------------------------------------------
# WEBHOOK (MENSAJES)
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if "message" in event and not event["message"].get("is_echo"):
                sender_id = event["sender"]["id"]
                texto = event["message"].get("text", "")
                msg_norm = normalizar(texto)

                resp = manejar_mensaje(sender_id, msg_norm)
                if resp:
                    enviar_mensaje(sender_id, resp)

    return "OK", 200


# ------------------------------------------------------------
# LÓGICA PRINCIPAL DEL BOT
# ------------------------------------------------------------
def manejar_mensaje(sender_id, msg):
    estado = user_state.get(sender_id, {}).get("estado", "inicio")

    # ---------------- SALUDO ----------------
    if any(x in msg for x in ["hola", "buenas", "hello"]):
        return (
            "👋 Hola, soy Frere’s Collection.\n\n"
            "Puedo ayudarte con:\n"
            "🛍 Catalogo\n"
            "📝 Registrar\n"
            "🔐 Iniciar sesion\n"
            "🕒 Horario\n"
            "📞 Contacto"
        )

    # ---------------- CONTACTO ----------------
    if "contacto" in msg or "whatsapp" in msg:
        return "📱 WhatsApp: *+52 55 1234 5678*"

    # ---------------- HORARIO ----------------
    if "horario" in msg:
        return "🕒 Lunes a sábado: 10 AM – 7 PM."

    # ---------------- REGISTRO ----------------
    if msg in ["registrar", "crear cuenta", "soy nuevo", "soy nueva"]:
        user_state[sender_id] = {"estado": "registrando_nombre"}
        return "📝 ¿Cuál es tu nombre completo?"

    if estado == "registrando_nombre":
        user_state[sender_id]["nombre"] = msg
        user_state[sender_id]["estado"] = "registrando_telefono"
        return "📱 Escribe tu número telefónico (10 dígitos)."

    if estado == "registrando_telefono":
        if not msg.isdigit() or len(msg) != 10:
            return "❌ Escribe un número válido de 10 dígitos."
        user_state[sender_id]["telefono"] = msg
        user_state[sender_id]["estado"] = "registrando_direccion"
        return "📍 Escribe tu dirección completa."

    if estado == "registrando_direccion":
        nombre = user_state[sender_id]["nombre"]
        telefono = user_state[sender_id]["telefono"]

        db.collection("usuarios").document(telefono).set({
            "nombre": nombre,
            "telefono": telefono,
            "direccion": msg
        })

        user_state[sender_id]["estado"] = "logueado"
        user_state[sender_id]["direccion"] = msg
        user_state[sender_id]["nombre"] = nombre

        return (
            f"✨ Registro completado, {nombre}.\n\n" +
            construir_categorias(sender_id)
        )

    # ---------------- LOGIN ----------------
    if msg.startswith("iniciar sesion") or msg == "entrar":
        user_state[sender_id] = {"estado": "login"}
        return "🔐 Escribe tu número telefónico registrado."

    if estado == "login":
        doc = db.collection("usuarios").document(msg).get()
        if not doc.exists:
            return "❌ Ese número no está registrado. Escribe *registrar* para crear cuenta."
        data = doc.to_dict()

        user_state[sender_id] = {
            "estado": "logueado",
            "nombre": data.get("nombre"),
            "telefono": msg,
            "direccion": data.get("direccion")
        }

        return (
            f"✨ Bienvenido de nuevo, {data.get('nombre')}.\n\n" +
            construir_categorias(sender_id)
        )

    # ---------------- CONSULTAR PEDIDO POR ID ----------------
    if msg.startswith("ver pedido") or msg.startswith("consultar") or msg.startswith("estado pedido"):
        tokens = msg.split()
        if len(tokens) < 3:
            return "Escribe: *ver pedido IDPEDIDO*"
        pid = tokens[2]

        ped = consultar_pedido_por_id(pid)
        if not ped:
            return "❌ No encontré ese pedido."

        resp = f"🧾 *Pedido {pid}*\n"
        resp += f"📌 Estado: {ped.get('estado')}\n"
        resp += "📦 Productos:\n"
        for p in ped.get("productos", []):
            resp += f"• {p['nombre']} – ${p['precio']} (ID: {p['id']})\n"
        resp += f"\n💵 Total: ${ped.get('total')}"
        return resp

    # ---------------- CATÁLOGO ----------------
    if "catalogo" in msg:
        if sender_id not in user_state:
            user_state[sender_id] = {"estado": "inicio"}
        return construir_categorias(sender_id)

    # ---------------- ELEGIR CATEGORÍA ----------------
    if estado == "elige_categoria":
        estado_u = user_state[sender_id]

        # FIX: Finalizar pedido también desde categorías
        if (
            "finalizar" in msg
            or "finalizar pedido" in msg
            or "cerrar pedido" in msg
            or "terminar" in msg
            or "fin" in msg
            or "ya" in msg
        ):
            return finalizar_pedido(sender_id)

        categorias = estado_u.get("categorias_pendientes", [])
        cat = None

        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(categorias):
                cat = categorias[idx]
        else:
            for c in categorias:
                if c.lower() in msg:
                    cat = c
                    break

        if not cat:
            return "❌ No reconocí esa categoría."

        if not preparar_categoria(sender_id, cat):
            return "😕 No hay productos en esa categoría."

        user_state[sender_id]["estado"] = "mostrando_producto"
        return mostrar_producto(sender_id)

    # ---------------- MOSTRAR PRODUCTO / CARRITO ----------------
    if estado == "mostrando_producto":

        # FINALIZAR PEDIDO (TODAS LAS VARIANTES)
        if (
            msg in ["finalizar", "finalizar pedido", "cerrar pedido", "terminar", "ya", "fin"]
            or "finalizar" in msg
            or "cerrar pedido" in msg
            or "cerrar" in msg
            or "terminar" in msg
            or "finaliza" in msg
            or "finaliza pedido" in msg
            or "completar" in msg
            or "completar pedido" in msg
            or "listo" in msg
            or "ya esta" in msg
            or "ya es todo" in msg
        ):
            return finalizar_pedido(sender_id)

        # SIGUIENTE PRODUCTO
        if msg in ["no", "siguiente", "next", "n", "skip"]:
            user_state[sender_id]["indice_producto"] += 1
            return mostrar_producto(sender_id)

        # AGREGAR PRODUCTO
        tokens = msg.split()
        pid = None

        # si 123, sí 123
        if tokens and tokens[0] in ["si", "sí", "si,", "si.", "sí,", "sí."]:
            if len(tokens) > 1 and tokens[1].isdigit():
                pid = tokens[1]
            else:
                productos = user_state[sender_id]["productos_categoria"]
                idx = user_state[sender_id]["indice_producto"]
                if idx < len(productos):
                    pid = productos[idx]["id"]

        # pedido 123
        elif tokens and tokens[0] == "pedido" and len(tokens) > 1:
            pid = tokens[1]

        # solo id
        elif msg.isdigit():
            pid = msg

        if pid:
            confirm = agregar_carrito(sender_id, pid)
            user_state[sender_id]["indice_producto"] += 1
            return confirm + "\n\n" + mostrar_producto(sender_id)

        return (
            "🤔 No entendí.\n"
            "Escribe *si*, *sí*, *pedido ID*, el *ID*, o *no* para avanzar."
        )

    # ---------------- ELECCIÓN MÉTODO DE ENTREGA ----------------
    if estado == "elige_entrega":
        pid = user_state[sender_id].get("ultimo_pedido_id")

        if any(x in msg for x in ["domicilio", "casa", "enviar"]):
            db.collection("pedidos").document(pid).update({
                "entrega": "domicilio",
                "direccion": user_state[sender_id].get("direccion", "No registrada")
            })
            user_state[sender_id]["estado"] = "logueado"
            return (
                f"🚚 Tu pedido será enviado a tu domicilio.\n"
                f"🧾 ID del pedido: {pid}"
            )

        if any(x in msg for x in ["recoger", "tienda", "pick"]):
            db.collection("pedidos").document(pid).update({
                "entrega": "tienda"
            })
            user_state[sender_id]["estado"] = "logueado"
            return (
                f"🏬 Puedes recoger tu pedido en la tienda.\n"
                f"🧾 ID del pedido: {pid}"
            )

        return "❌ Escribe *domicilio* o *recoger en tienda*."

    # ---------------- FALLBACK ----------------
    return (
        "🤔 No entendí.\n\n"
        "Puedo ayudarte con:\n"
        "🛍 Catalogo\n"
        "📝 Registrar\n"
        "🔐 Iniciar sesion\n"
        "🕒 Horario\n"
        "📞 Contacto"
    )


# ------------------------------------------------------------
# EJECUCIÓN DEL SERVIDOR
# ------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🔥 Servidor ejecutándose en {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
