
from flask import Flask, request
import requests
import logging
import os
import unicodedata
import string
from datetime import datetime, timedelta

# Firebase
from conexion_firebase import obtener_productos, db
import firebase_admin
from firebase_admin import firestore

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

# Caché de productos con TTL
productos_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 300  # 5 minutos en segundos
}

# Timeout de sesión (30 minutos)
SESSION_TIMEOUT = 1800  # 30 minutos en segundos

# Rate limiting (10 mensajes por minuto)
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW = 60  # segundos
user_message_count = {}  # {sender_id: [(timestamp1, timestamp2, ...)]}


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
# SEGURIDAD Y RATE LIMITING
# ------------------------------------------------------------
def verificar_rate_limit(sender_id):
    """Verifica si el usuario ha excedido el límite de mensajes."""
    global user_message_count
    
    ahora = datetime.now()
    
    # Inicializar si no existe
    if sender_id not in user_message_count:
        user_message_count[sender_id] = []
    
    # Limpiar mensajes antiguos (fuera de la ventana)
    user_message_count[sender_id] = [
        ts for ts in user_message_count[sender_id]
        if (ahora - ts).total_seconds() < RATE_LIMIT_WINDOW
    ]
    
    # Verificar límite
    if len(user_message_count[sender_id]) >= RATE_LIMIT_MESSAGES:
        return False
    
    # Agregar timestamp actual
    user_message_count[sender_id].append(ahora)
    return True


def sanitizar_input(texto):
    """Sanitiza el input del usuario para prevenir inyecciones."""
    if not texto:
        return ""
    
    # Limitar longitud
    texto = texto[:500]
    
    # Remover caracteres peligrosos
    caracteres_peligrosos = ['<', '>', '{', '}', '$', '`']
    for char in caracteres_peligrosos:
        texto = texto.replace(char, '')
    
    return texto.strip()


def validar_pertenencia_pedido(sender_id, pedido_id):
    """Valida que un pedido pertenezca al usuario."""
    try:
        estado = user_state.get(sender_id, {})
        telefono = estado.get("telefono")
        
        if not telefono:
            return False
        
        doc = db.collection("pedidos").document(pedido_id).get()
        if not doc.exists:
            return False
        
        pedido = doc.to_dict()
        return pedido.get("telefono") == telefono
    except Exception as e:
        print(f"🔥 Error al validar pertenencia: {type(e).__name__} - {e}")
        return False


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
# PERSISTENCIA Y CACHÉ
# ------------------------------------------------------------
def guardar_sesion(sender_id):
    """Guarda el estado del usuario en Firestore."""
    try:
        estado = user_state.get(sender_id, {})
        if not estado:
            return
        
        estado["ultima_actividad"] = datetime.now()
        
        db.collection("sesiones").document(sender_id).set(estado)
    except Exception as e:
        print(f"🔥 Error al guardar sesión {sender_id}: {type(e).__name__} - {e}")


def cargar_sesion(sender_id):
    """Carga el estado del usuario desde Firestore."""
    try:
        doc = db.collection("sesiones").document(sender_id).get()
        if not doc.exists:
            return None
        
        estado = doc.to_dict()
        
        # Verificar timeout de sesión
        ultima_actividad = estado.get("ultima_actividad")
        if ultima_actividad:
            if isinstance(ultima_actividad, str):
                ultima_actividad = datetime.fromisoformat(ultima_actividad)
            
            tiempo_inactivo = (datetime.now() - ultima_actividad).total_seconds()
            
            if tiempo_inactivo > SESSION_TIMEOUT:
                # Sesión expirada, eliminar
                db.collection("sesiones").document(sender_id).delete()
                return None
        
        return estado
    except Exception as e:
        print(f"🔥 Error al cargar sesión {sender_id}: {type(e).__name__} - {e}")
        return None


def obtener_productos_con_cache():
    """Obtiene productos con caché de 5 minutos."""
    global productos_cache
    
    ahora = datetime.now()
    
    # Verificar si el caché es válido
    if productos_cache["data"] is not None and productos_cache["timestamp"] is not None:
        tiempo_transcurrido = (ahora - productos_cache["timestamp"]).total_seconds()
        
        if tiempo_transcurrido < productos_cache["ttl"]:
            print(f"✅ Usando caché de productos ({int(tiempo_transcurrido)}s)")
            return productos_cache["data"]
    
    # Caché inválido o expirado, obtener de Firebase
    print("🔄 Actualizando caché de productos desde Firebase")
    productos = obtener_productos_con_cache()
    
    # Actualizar caché
    productos_cache["data"] = productos
    productos_cache["timestamp"] = ahora
    
    return productos


def limpiar_sesiones_antiguas():
    """Limpia sesiones inactivas de Firestore (llamar periódicamente)."""
    try:
        limite = datetime.now() - timedelta(seconds=SESSION_TIMEOUT)
        
        sesiones = db.collection("sesiones").where("ultima_actividad", "<", limite).stream()
        
        count = 0
        for sesion in sesiones:
            sesion.reference.delete()
            count += 1
        
        if count > 0:
            print(f"🧹 Limpiadas {count} sesiones antiguas")
    except Exception as e:
        print(f"🔥 Error al limpiar sesiones: {type(e).__name__} - {e}")


# ------------------------------------------------------------
# ANALYTICS Y MÉTRICAS
# ------------------------------------------------------------
def registrar_mensaje(sender_id, mensaje, tipo="recibido"):
    """Registra un mensaje en analytics."""
    try:
        db.collection("analytics").add({
            "tipo": "mensaje",
            "sender_id": sender_id,
            "mensaje": mensaje,
            "direccion": tipo,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"🔥 Error al registrar mensaje: {type(e).__name__} - {e}")


def registrar_producto_visto(sender_id, producto_id, producto_nombre):
    """Registra cuando un usuario ve un producto."""
    try:
        db.collection("analytics").add({
            "tipo": "producto_visto",
            "sender_id": sender_id,
            "producto_id": producto_id,
            "producto_nombre": producto_nombre,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"🔥 Error al registrar producto visto: {type(e).__name__} - {e}")


def registrar_busqueda(sender_id, termino, resultados_count):
    """Registra búsquedas de productos."""
    try:
        db.collection("analytics").add({
            "tipo": "busqueda",
            "sender_id": sender_id,
            "termino": termino,
            "resultados": resultados_count,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"🔥 Error al registrar búsqueda: {type(e).__name__} - {e}")


def registrar_conversion(sender_id, pedido_id, total, productos_count):
    """Registra cuando se completa un pedido (conversión)."""
    try:
        db.collection("analytics").add({
            "tipo": "conversion",
            "sender_id": sender_id,
            "pedido_id": pedido_id,
            "total": total,
            "productos_count": productos_count,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"🔥 Error al registrar conversión: {type(e).__name__} - {e}")


def registrar_error(sender_id, error_tipo, error_mensaje):
    """Registra errores para análisis."""
    try:
        db.collection("analytics").add({
            "tipo": "error",
            "sender_id": sender_id,
            "error_tipo": error_tipo,
            "error_mensaje": error_mensaje,
            "timestamp": datetime.now()
        })
    except Exception as e:
        print(f"🔥 Error al registrar error: {type(e).__name__} - {e}")


def obtener_productos_mas_vistos(limite=10):
    """Obtiene los productos más vistos."""
    try:
        docs = db.collection("analytics").where("tipo", "==", "producto_visto").stream()
        
        conteo = {}
        for doc in docs:
            data = doc.to_dict()
            pid = data.get("producto_id")
            if pid:
                conteo[pid] = conteo.get(pid, 0) + 1
        
        # Ordenar por más vistos
        mas_vistos = sorted(conteo.items(), key=lambda x: x[1], reverse=True)[:limite]
        return mas_vistos
    except Exception as e:
        print(f"🔥 Error al obtener productos más vistos: {type(e).__name__} - {e}")
        return []


# ------------------------------------------------------------
# GESTIÓN DE INVENTARIO
# ------------------------------------------------------------
def reducir_stock(producto_id, cantidad):
    """Reduce el stock de un producto."""
    try:
        producto_ref = db.collection("productos").document(producto_id)
        producto = producto_ref.get()
        
        if not producto.exists:
            return False
        
        datos = producto.to_dict()
        stock_actual = datos.get("stock", 0)
        
        if stock_actual < cantidad:
            return False
        
        nuevo_stock = stock_actual - cantidad
        producto_ref.update({"stock": nuevo_stock})
        
        # Invalidar caché de productos
        global productos_cache
        productos_cache["data"] = None
        
        print(f"✅ Stock reducido: {producto_id} - {cantidad} unidades (quedan {nuevo_stock})")
        return True
    except Exception as e:
        print(f"🔥 Error al reducir stock: {type(e).__name__} - {e}")
        return False


def restaurar_stock(producto_id, cantidad):
    """Restaura el stock de un producto (por cancelación)."""
    try:
        producto_ref = db.collection("productos").document(producto_id)
        producto = producto_ref.get()
        
        if not producto.exists:
            return False
        
        datos = producto.to_dict()
        stock_actual = datos.get("stock", 0)
        nuevo_stock = stock_actual + cantidad
        
        producto_ref.update({"stock": nuevo_stock})
        
        # Invalidar caché
        global productos_cache
        productos_cache["data"] = None
        
        print(f"🔄 Stock restaurado: {producto_id} + {cantidad} unidades (total {nuevo_stock})")
        return True
    except Exception as e:
        print(f"🔥 Error al restaurar stock: {type(e).__name__} - {e}")
        return False


def verificar_stock_bajo(umbral=5):
    """Verifica productos con stock bajo."""
    try:
        productos = obtener_productos_con_cache()
        productos_bajos = []
        
        for pid, datos in productos.items():
            stock = datos.get("stock", 0)
            if 0 < stock <= umbral:
                productos_bajos.append({
                    "id": pid,
                    "nombre": datos.get("nombre", "Sin nombre"),
                    "stock": stock
                })
        
        return productos_bajos
    except Exception as e:
        print(f"🔥 Error al verificar stock bajo: {type(e).__name__} - {e}")
        return []


def procesar_reduccion_stock_pedido(carrito):
    """Reduce el stock de todos los productos en un pedido."""
    productos_procesados = []
    
    try:
        for item in carrito:
            pid = item.get("id")
            cantidad = item.get("cantidad", 1)
            
            if reducir_stock(pid, cantidad):
                productos_procesados.append(pid)
            else:
                # Revertir cambios si falla
                for pid_revertir in productos_procesados:
                    # Buscar cantidad original
                    for i in carrito:
                        if i.get("id") == pid_revertir:
                            restaurar_stock(pid_revertir, i.get("cantidad", 1))
                            break
                return False
        
        return True
    except Exception as e:
        print(f"🔥 Error al procesar reducción de stock: {type(e).__name__} - {e}")
        return False


# ------------------------------------------------------------
# CONSULTAS INTELIGENTES A FIREBASE
# ------------------------------------------------------------
def productos_nuevos(dias=7):
    """Obtiene productos agregados en los últimos N días."""
    try:
        productos = obtener_productos_con_cache()
        nuevos = []
        fecha_actual = datetime.now()
        
        for pid, datos in productos.items():
            fecha_alta = datos.get("fecha_alta")
            if not fecha_alta:
                continue
            
            # Intentar parsear diferentes formatos de fecha
            try:
                if isinstance(fecha_alta, str):
                    # Intentar formato DD/MM/YY
                    if "/" in fecha_alta:
                        fecha_prod = datetime.strptime(fecha_alta, "%d/%m/%y")
                    # Intentar formato ISO
                    elif "T" in fecha_alta:
                        fecha_prod = datetime.fromisoformat(fecha_alta.replace("Z", "+00:00"))
                    else:
                        continue
                else:
                    fecha_prod = fecha_alta
                
                diferencia = (fecha_actual - fecha_prod).days
                if 0 <= diferencia <= dias:
                    nuevos.append({
                        "id": pid,
                        "nombre": datos.get("nombre", "Sin nombre"),
                        "precio": datos.get("precio", 0),
                        "categoria": datos.get("categoria", "Sin categoría"),
                        "dias_antiguo": diferencia
                    })
            except Exception:
                continue
        
        return nuevos
    except Exception as e:
        print(f"🔥 Error en productos_nuevos(): {type(e).__name__} - {e}")
        return []


def productos_en_oferta():
    """Obtiene productos que tienen descuento o están en oferta."""
    try:
        productos = obtener_productos_con_cache()
        ofertas = []
        
        for pid, datos in productos.items():
            descuento = datos.get("descuento", 0)
            en_oferta = datos.get("oferta", False)
            
            if descuento > 0 or en_oferta:
                precio_original = datos.get("precio", 0)
                precio_final = precio_original * (1 - descuento / 100) if descuento > 0 else precio_original
                
                ofertas.append({
                    "id": pid,
                    "nombre": datos.get("nombre", "Sin nombre"),
                    "precio_original": precio_original,
                    "precio_final": precio_final,
                    "descuento": descuento,
                    "categoria": datos.get("categoria", "Sin categoría")
                })
        
        return ofertas
    except Exception as e:
        print(f"🔥 Error en productos_en_oferta(): {type(e).__name__} - {e}")
        return []


def buscar_producto_por_nombre(termino):
    """Busca productos por nombre (búsqueda parcial)."""
    try:
        productos = obtener_productos_con_cache()
        resultados = []
        termino_norm = normalizar(termino)
        
        for pid, datos in productos.items():
            nombre = datos.get("nombre", "")
            nombre_norm = normalizar(nombre)
            
            if termino_norm in nombre_norm:
                resultados.append({
                    "id": pid,
                    "nombre": nombre,
                    "precio": datos.get("precio", 0),
                    "categoria": datos.get("categoria", "Sin categoría"),
                    "stock": datos.get("stock", 0),
                    "imagen_url": datos.get("imagen_url", "")
                })
        
        return resultados
    except Exception as e:
        print(f"🔥 Error en buscar_producto_por_nombre(): {type(e).__name__} - {e}")
        return []


def productos_por_precio(precio_min=0, precio_max=999999):
    """Obtiene productos dentro de un rango de precio."""
    try:
        productos = obtener_productos_con_cache()
        resultados = []
        
        for pid, datos in productos.items():
            precio = datos.get("precio", 0)
            
            if precio_min <= precio <= precio_max:
                resultados.append({
                    "id": pid,
                    "nombre": datos.get("nombre", "Sin nombre"),
                    "precio": precio,
                    "categoria": datos.get("categoria", "Sin categoría")
                })
        
        # Ordenar por precio
        resultados.sort(key=lambda x: x["precio"])
        return resultados
    except Exception as e:
        print(f"🔥 Error en productos_por_precio(): {type(e).__name__} - {e}")
        return []


def verificar_stock(producto_id):
    """Verifica si un producto tiene stock disponible."""
    try:
        productos = obtener_productos_con_cache()
        if producto_id not in productos:
            return None
        
        datos = productos[producto_id]
        stock = datos.get("stock", 0)
        nombre = datos.get("nombre", "Sin nombre")
        
        return {
            "id": producto_id,
            "nombre": nombre,
            "stock": stock,
            "disponible": stock > 0
        }
    except Exception as e:
        print(f"🔥 Error en verificar_stock(): {type(e).__name__} - {e}")
        return None


def mi_ultimo_pedido(telefono):
    """Obtiene el último pedido de un usuario por teléfono."""
    try:
        pedidos_ref = db.collection("pedidos").where("telefono", "==", telefono).order_by("fecha", direction=firestore.Query.DESCENDING).limit(1)
        docs = list(pedidos_ref.stream())
        
        if not docs:
            return None
        
        doc = docs[0]
        pedido = doc.to_dict()
        pedido["id"] = doc.id
        return pedido
    except Exception as e:
        print(f"🔥 Error en mi_ultimo_pedido(): {type(e).__name__} - {e}")
        return None


# ------------------------------------------------------------
# AUXILIARES (CATEGORÍAS, PRODUCTOS, CARRITO)
# ------------------------------------------------------------
def construir_categorias(sender_id):
    productos = obtener_productos_con_cache()
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
    productos = obtener_productos_con_cache()
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


def agregar_carrito(sender_id, pid, cantidad=1):
    """Agrega productos al carrito con validación de stock."""
    productos = obtener_productos_con_cache()
    if pid not in productos:
        return "❌ Ese ID de producto no existe."

    datos = productos[pid]
    nombre = datos.get("nombre", "Sin nombre")
    precio = datos.get("precio", 0)
    categoria = datos.get("categoria", "Sin categoria")
    stock = datos.get("stock", 0)

    # Validar stock disponible
    if stock <= 0:
        return f"❌ *{nombre}* está agotado. No hay stock disponible."
    
    if cantidad > stock:
        return f"❌ Solo hay {stock} unidades de *{nombre}* disponibles."

    user_state[sender_id].setdefault("carrito", [])
    
    # Verificar si ya existe en el carrito
    for item in user_state[sender_id]["carrito"]:
        if item["id"] == pid:
            item["cantidad"] = item.get("cantidad", 1) + cantidad
            return f"🛒 Actualizado: {item['cantidad']}x *{nombre}* en tu carrito."
    
    # Agregar nuevo producto
    user_state[sender_id]["carrito"].append({
        "id": pid,
        "nombre": nombre,
        "precio": precio,
        "categoria": categoria,
        "cantidad": cantidad
    })

    if cantidad > 1:
        return f"🛒 {cantidad}x *{nombre}* agregado a tu pedido."
    return f"🛒 *{nombre}* agregado a tu pedido."


def ver_carrito(sender_id):
    """Muestra el contenido actual del carrito."""
    estado = user_state.get(sender_id, {})
    carrito = estado.get("carrito", [])
    
    if not carrito:
        return "🛒 Tu carrito está vacío.\n\nEscribe *catalogo* para ver productos."
    
    total = 0
    msg = "🛒 *Tu carrito:*\n\n"
    
    for i, item in enumerate(carrito, 1):
        cantidad = item.get("cantidad", 1)
        precio_unitario = item.get("precio", 0)
        subtotal = precio_unitario * cantidad
        total += subtotal
        
        if cantidad > 1:
            msg += f"{i}. {cantidad}x {item['nombre']}\n"
            msg += f"   💰 ${precio_unitario} c/u = ${subtotal} MXN\n"
            msg += f"   🆔 ID: {item['id']}\n\n"
        else:
            msg += f"{i}. {item['nombre']}\n"
            msg += f"   💰 ${precio_unitario} MXN\n"
            msg += f"   🆔 ID: {item['id']}\n\n"
    
    msg += f"━━━━━━━━━━━━━━━━\n"
    msg += f"💵 *Total: ${total} MXN*\n\n"
    msg += "Opciones:\n"
    msg += "• *quitar ID* - Eliminar producto\n"
    msg += "• *vaciar carrito* - Limpiar todo\n"
    msg += "• *finalizar pedido* - Proceder al pago"
    
    return msg


def quitar_del_carrito(sender_id, pid):
    """Elimina un producto del carrito por ID."""
    estado = user_state.get(sender_id, {})
    carrito = estado.get("carrito", [])
    
    if not carrito:
        return "🛒 Tu carrito está vacío."
    
    # Buscar y eliminar el producto
    for i, item in enumerate(carrito):
        if item["id"] == pid:
            nombre = item["nombre"]
            carrito.pop(i)
            user_state[sender_id]["carrito"] = carrito
            
            if carrito:
                return f"✅ *{nombre}* eliminado del carrito.\n\nEscribe *ver carrito* para revisar."
            else:
                return f"✅ *{nombre}* eliminado.\n\n🛒 Tu carrito está vacío ahora."
    
    return f"❌ No encontré el producto con ID {pid} en tu carrito."


def vaciar_carrito(sender_id):
    """Vacía completamente el carrito."""
    estado = user_state.get(sender_id, {})
    carrito = estado.get("carrito", [])
    
    if not carrito:
        return "🛒 Tu carrito ya está vacío."
    
    cantidad_items = len(carrito)
    user_state[sender_id]["carrito"] = []
    
    return f"🗑️ Carrito vaciado ({cantidad_items} producto(s) eliminado(s)).\n\nEscribe *catalogo* para seguir comprando."


def finalizar_pedido(sender_id):
    estado = user_state[sender_id]
    carrito = estado.get("carrito", [])

    if not carrito:
        return "🛍 No tienes productos en tu pedido. Escribe *catalogo* para ver productos."

    total = 0
    for item in carrito:
        try:
            cantidad = item.get("cantidad", 1)
            precio = float(item.get("precio", 0))
            total += precio * cantidad
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
    try:
        # Reducir stock de productos
        if not procesar_reduccion_stock_pedido(carrito):
            return "❌ No hay stock suficiente para completar tu pedido. Algunos productos se agotaron. Por favor revisa tu carrito."
        
        doc_ref = db.collection("pedidos").document()
        doc_ref.set(pedido)
        pedido_id = doc_ref.id
        
        # Registrar conversión en analytics
        registrar_conversion(sender_id, pedido_id, total, len(carrito))
        
        # Limpiar carrito
        user_state[sender_id]["carrito"] = []
        
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
    except Exception as e:
        print(f"🔥 Error al guardar pedido en Firestore: {type(e).__name__} - {e}")
        registrar_error(sender_id, "finalizar_pedido", str(e))
        return "❌ Hubo un error al procesar tu pedido. Por favor intenta de nuevo más tarde."


# ------------------------------------------------------------
# CONSULTA DE PEDIDO POR ID
# ------------------------------------------------------------
def consultar_pedido_por_id(pid):
    try:
        doc = db.collection("pedidos").document(pid).get()
        if not doc.exists:
            return None
        return doc.to_dict()
    except Exception as e:
        print(f"🔥 Error al consultar pedido {pid}: {type(e).__name__} - {e}")
        return None


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
                
                # Verificar rate limiting
                if not verificar_rate_limit(sender_id):
                    enviar_mensaje(sender_id, "⏱️ Por favor espera un momento antes de enviar más mensajes.")
                    continue
                
                # Sanitizar input
                texto = sanitizar_input(texto)
                msg_norm = normalizar(texto)
                
                # Registrar mensaje en analytics
                registrar_mensaje(sender_id, msg_norm, "recibido")

                # Cargar sesión desde Firestore si no está en memoria
                if sender_id not in user_state:
                    sesion_guardada = cargar_sesion(sender_id)
                    if sesion_guardada:
                        user_state[sender_id] = sesion_guardada
                        print(f"📂 Sesión cargada para {sender_id}")

                resp = manejar_mensaje(sender_id, msg_norm)
                if resp:
                    enviar_mensaje(sender_id, resp)
                    registrar_mensaje(sender_id, resp, "enviado")
                
                # Guardar sesión después de procesar mensaje
                guardar_sesion(sender_id)

    return "OK", 200


# ------------------------------------------------------------
# LÓGICA PRINCIPAL DEL BOT
# ------------------------------------------------------------
def manejar_mensaje(sender_id, msg):
    estado = user_state.get(sender_id, {}).get("estado", "inicio")

    # ---------------- SALUDO ----------------
    if any(x in msg for x in ["hola", "buenas", "hello", "hi", "hey"]):
        return (
            "👋 Hola, soy Frere's Collection.\n\n"
            "Puedo ayudarte con:\n"
            "🛍 Catalogo\n"
            "📝 Registrar\n"
            "🔐 Iniciar sesion\n"
            "🆕 Novedades\n"
            "💰 Ofertas\n"
            "🔍 Buscar producto\n"
            "📦 Mi ultimo pedido\n"
            "🕒 Horario\n"
            "📞 Contacto"
        )

    # ---------------- CONTACTO ----------------
    if "contacto" in msg or "whatsapp" in msg:
        return "📱 WhatsApp: *+52 55 1234 5678*"

    # ---------------- HORARIO ----------------
    if "horario" in msg or "abierto" in msg or "horarios" in msg:
        return "🕒 Lunes a sábado: 10 AM – 7 PM."

    # ---------------- PRODUCTOS NUEVOS ----------------
    if any(x in msg for x in ["nuevo", "nuevos", "novedades", "reciente", "recientes", "que hay de nuevo", "ultimos productos"]):
        nuevos = productos_nuevos(dias=30)
        
        if not nuevos:
            return "😕 No hay productos nuevos en este momento. Escribe *catalogo* para ver todos los productos."
        
        msg_resp = f"🆕 *Productos nuevos* (últimos 30 días):\n\n"
        for i, p in enumerate(nuevos[:10], 1):  # Máximo 10
            msg_resp += f"{i}. {p['nombre']}\n💰 ${p['precio']} MXN\n📂 {p['categoria']}\n🆔 ID: {p['id']}\n\n"
        
        msg_resp += "Para agregar al pedido escribe: *si ID* o *pedido ID*"
        return msg_resp

    # ---------------- PRODUCTOS EN OFERTA ----------------
    if any(x in msg for x in ["oferta", "ofertas", "descuento", "descuentos", "promocion", "promociones", "rebaja", "barato"]):
        ofertas = productos_en_oferta()
        
        if not ofertas:
            return "😕 No hay ofertas activas en este momento. Escribe *catalogo* para ver todos los productos."
        
        msg_resp = f"� *Productos en oferta:*\n\n"
        for i, p in enumerate(ofertas[:10], 1):  # Máximo 10
            if p['descuento'] > 0:
                msg_resp += f"{i}. {p['nombre']}\n💵 Antes: ${p['precio_original']} MXN\n🔥 Ahora: ${p['precio_final']:.2f} MXN ({p['descuento']}% OFF)\n🆔 ID: {p['id']}\n\n"
            else:
                msg_resp += f"{i}. {p['nombre']}\n💰 ${p['precio_final']} MXN\n🆔 ID: {p['id']}\n\n"
        
        msg_resp += "Para agregar al pedido escribe: *si ID* o *pedido ID*"
        return msg_resp

    # ---------------- BUSCAR PRODUCTO ----------------
    if msg.startswith("buscar") or msg.startswith("busco") or msg.startswith("buscando"):
        # Extraer término de búsqueda
        termino = msg.replace("buscar", "").replace("busco", "").replace("buscando", "").strip()
        
        if not termino or len(termino) < 2:
            return "🔍 Escribe: *buscar NOMBRE_PRODUCTO*\nEjemplo: *buscar blusa*"
        
        resultados = buscar_producto_por_nombre(termino)
        
        # Registrar búsqueda en analytics
        registrar_busqueda(sender_id, termino, len(resultados))
        
        if not resultados:
            return f"� No encontré productos con '{termino}'. Escribe *catalogo* para ver todos los productos."
        
        msg_resp = f"🔍 Encontré {len(resultados)} producto(s) con '{termino}':\n\n"
        for i, p in enumerate(resultados[:8], 1):  # Máximo 8
            stock_txt = "✅ Disponible" if p['stock'] > 0 else "❌ Agotado"
            msg_resp += f"{i}. {p['nombre']}\n💰 ${p['precio']} MXN\n� {stock_txt}\n🆔 ID: {p['id']}\n\n"
        
        msg_resp += "Para agregar al pedido escribe: *si ID* o *pedido ID*"
        return msg_resp

    # ---------------- PRODUCTOS POR PRECIO ----------------
    if any(x in msg for x in ["precio menor", "precio mayor", "menos de", "mas de", "entre", "rango de precio"]):
        # Intentar extraer números del mensaje
        import re
        numeros = re.findall(r'\d+', msg)
        
        if len(numeros) == 0:
            return "💵 Escribe:\n• *menos de 500*\n• *mas de 200*\n• *entre 100 y 500*"
        
        if "menos de" in msg or "menor" in msg:
            precio_max = int(numeros[0])
            resultados = productos_por_precio(precio_max=precio_max)
            titulo = f"Productos de menos de ${precio_max} MXN"
        elif "mas de" in msg or "mayor" in msg:
            precio_min = int(numeros[0])
            resultados = productos_por_precio(precio_min=precio_min)
            titulo = f"Productos de más de ${precio_min} MXN"
        elif "entre" in msg and len(numeros) >= 2:
            precio_min = int(numeros[0])
            precio_max = int(numeros[1])
            resultados = productos_por_precio(precio_min=precio_min, precio_max=precio_max)
            titulo = f"Productos entre ${precio_min} y ${precio_max} MXN"
        else:
            return "💵 Escribe:\n• *menos de 500*\n• *mas de 200*\n• *entre 100 y 500*"
        
        if not resultados:
            return f"� No encontré productos en ese rango de precio."
        
        msg_resp = f"💵 *{titulo}:*\n\n"
        for i, p in enumerate(resultados[:10], 1):  # Máximo 10
            msg_resp += f"{i}. {p['nombre']}\n💰 ${p['precio']} MXN\n🆔 ID: {p['id']}\n\n"
        
        msg_resp += "Para agregar al pedido escribe: *si ID* o *pedido ID*"
        return msg_resp

    # ---------------- VERIFICAR STOCK ----------------
    if msg.startswith("stock") or msg.startswith("disponible") or msg.startswith("hay"):
        # Extraer ID del producto
        import re
        numeros = re.findall(r'\d+', msg)
        
        if not numeros:
            return "📦 Escribe: *stock ID_PRODUCTO*\nEjemplo: *stock 123*"
        
        producto_id = numeros[0]
        info = verificar_stock(producto_id)
        
        if not info:
            return f"❌ No encontré el producto con ID {producto_id}."
        
        if info['disponible']:
            return f"✅ *{info['nombre']}*\n� Stock disponible: {info['stock']} unidades\n🆔 ID: {info['id']}"
        else:
            return f"❌ *{info['nombre']}*\n😕 Producto agotado\n🆔 ID: {info['id']}"

    # ---------------- MI ÚLTIMO PEDIDO ----------------
    if any(x in msg for x in ["mi pedido", "mi ultimo pedido", "mis pedidos", "ultimo pedido", "estado de mi pedido"]):
        telefono = user_state.get(sender_id, {}).get("telefono")
        
        if not telefono:
            return "❌ Necesitas iniciar sesión primero. Escribe *iniciar sesion*."
        
        pedido = mi_ultimo_pedido(telefono)
        
        if not pedido:
            return "� No tienes pedidos registrados aún."
        
        msg_resp = f"📦 *Tu último pedido:*\n\n"
        msg_resp += f"🆔 ID: {pedido['id']}\n"
        msg_resp += f"📌 Estado: {pedido.get('estado', 'pendiente')}\n"
        msg_resp += f"💵 Total: ${pedido.get('total', 0)} MXN\n"
        msg_resp += f"📅 Fecha: {pedido.get('fecha', 'N/A')}\n\n"
        msg_resp += "📦 Productos:\n"
        for p in pedido.get('productos', []):
            msg_resp += f"• {p.get('nombre', 'Sin nombre')} - ${p.get('precio', 0)}\n"
        
        return msg_resp

    # ---------------- VER CARRITO ----------------
    if any(x in msg for x in ["ver carrito", "mi carrito", "carrito", "que tengo", "mostrar carrito"]):
        return ver_carrito(sender_id)

    # ---------------- QUITAR DEL CARRITO ----------------
    if msg.startswith("quitar") or msg.startswith("eliminar") or msg.startswith("borrar"):
        import re
        numeros = re.findall(r'\d+', msg)
        
        if not numeros:
            return "❌ Escribe: *quitar ID_PRODUCTO*\nEjemplo: *quitar 123*"
        
        producto_id = numeros[0]
        return quitar_del_carrito(sender_id, producto_id)

    # ---------------- VACIAR CARRITO ----------------
    if any(x in msg for x in ["vaciar carrito", "limpiar carrito", "borrar carrito", "eliminar todo"]):
        return vaciar_carrito(sender_id)

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

        try:
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
        except Exception as e:
            print(f"🔥 Error al registrar usuario {telefono}: {type(e).__name__} - {e}")
            return "❌ Hubo un error al completar tu registro. Por favor intenta de nuevo."

    # ---------------- LOGIN ----------------
    if msg.startswith("iniciar sesion") or msg == "entrar":
        user_state[sender_id] = {"estado": "login"}
        return "🔐 Escribe tu número telefónico registrado."

    if estado == "login":
        try:
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
        except Exception as e:
            print(f"🔥 Error al hacer login con {msg}: {type(e).__name__} - {e}")
            return "❌ Hubo un error al iniciar sesión. Por favor intenta de nuevo."

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
        cantidad = 1
        
        # Detectar cantidad: "2x 123", "3 unidades 456", "5 123"
        import re
        # Buscar patrón: número seguido de 'x' o 'unidades' o solo número + ID
        match_cantidad = re.match(r'(\d+)\s*(?:x|unidades?|piezas?)?\s+(\d+)', msg)
        if match_cantidad:
            cantidad = int(match_cantidad.group(1))
            pid = match_cantidad.group(2)
        # si 123, sí 123
        elif tokens and tokens[0] in ["si", "sí", "si,", "si.", "sí,", "sí."]:
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
            confirm = agregar_carrito(sender_id, pid, cantidad)
            user_state[sender_id]["indice_producto"] += 1
            return confirm + "\n\n" + mostrar_producto(sender_id)

        return (
            "🤔 No entendí.\n"
            "Escribe *si*, *sí*, *pedido ID*, el *ID*,\n"
            "*2x ID* para cantidad, o *no* para avanzar."
        )

    # ---------------- ELECCIÓN MÉTODO DE ENTREGA ----------------
    if estado == "elige_entrega":
        pid = user_state[sender_id].get("ultimo_pedido_id")

        if any(x in msg for x in ["domicilio", "casa", "enviar"]):
            try:
                db.collection("pedidos").document(pid).update({
                    "entrega": "domicilio",
                    "direccion": user_state[sender_id].get("direccion", "No registrada")
                })
                user_state[sender_id]["estado"] = "logueado"
                return (
                    f"🚚 Tu pedido será enviado a tu domicilio.\n"
                    f"🧾 ID del pedido: {pid}"
                )
            except Exception as e:
                print(f"🔥 Error al actualizar entrega a domicilio: {type(e).__name__} - {e}")
                return "❌ Hubo un error al procesar tu método de entrega. Por favor contacta con soporte."

        if any(x in msg for x in ["recoger", "tienda", "pick"]):
            try:
                db.collection("pedidos").document(pid).update({
                    "entrega": "tienda"
                })
                user_state[sender_id]["estado"] = "logueado"
                return (
                    f"🏬 Puedes recoger tu pedido en la tienda.\n"
                    f"🧾 ID del pedido: {pid}"
                )
            except Exception as e:
                print(f"🔥 Error al actualizar entrega en tienda: {type(e).__name__} - {e}")
                return "❌ Hubo un error al procesar tu método de entrega. Por favor contacta con soporte."

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
