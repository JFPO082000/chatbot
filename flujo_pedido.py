# archivo: flujo_pedido.py
import datetime
from conexion_firebase import db, obtener_productos

# =============================
# FUNCIONES DE PEDIDOS
# =============================
def crear_pedido(telefono_usuario, productos_solicitados, metodo_entrega="envio_domicilio"):
    """
    Registra un pedido en la colección 'pedidos'.
    productos_solicitados debe ser una lista de IDs como ['P001', 'P002']
    """
    productos_disponibles = obtener_productos()
    items = []
    monto_total = 0

    for pid in productos_solicitados:
        producto = productos_disponibles.get(pid)
        if not producto:
            continue  # producto no encontrado

        stock_disponible = int(producto.get("stock", {}).get("Piezas", 0))
        if stock_disponible <= 0:
            continue  # sin stock

        # Agregamos al pedido
        items.append({
            "producto_id": pid,
            "nombre": producto.get("nombre"),
            "precio": producto.get("precio"),
            "imagen": producto.get("imagen_url"),
            "cantidad": stock_disponible
        })
        monto_total += producto.get("precio", 0)

    if not items:
        return "⚠️ No se pudo crear el pedido. Los productos están agotados o no existen."

    # Crear el documento del pedido
    nuevo_pedido = {
        "telefono": telefono_usuario,
        "estado": "pendiente_pago",
        "comprobante_url": "pendiente",
        "preferencia_entrega": metodo_entrega,
        "monto_total": monto_total,
        "creado_en": datetime.datetime.utcnow().isoformat() + "Z",
        "items": items,
        "expira_en": ""
    }

    try:
        pedido_ref = db.collection("pedidos").document()
        pedido_ref.set(nuevo_pedido)
        return f"✅ Tu pedido fue registrado correctamente. Total a pagar: ${monto_total}. Pronto te contactaremos."
    except Exception as e:
        print("Error al guardar el pedido:", e)
        return "❌ Ocurrió un error al guardar tu pedido. Intenta más tarde."

# =============================
# FUNCIONES OPCIONALES
# =============================
def formatear_productos_para_usuario():
    productos = obtener_productos()
    mensajes = []

    if not productos:
        return [{"text": {"text": ["No hay productos disponibles en este momento."]}}]

    for pid, p in productos.items():
        nombre = p.get("nombre", "Producto")
        precio = p.get("precio", "N/A")
        stock = p.get("stock", {}).get("Piezas", "0")
        imagen = p.get("imagen_url", "")

        # 🧾 Mensaje de texto
        texto = f"🧸 {nombre}\n💵 ${precio} MXN\n📦 Stock: {stock} unidades"
        mensajes.append({"text": {"text": [texto]}})

        # 🖼 Imagen enriquecida (solo si hay URL válida)
        if imagen.startswith("http"):
            mensajes.append({
                "payload": {
                    "facebook": {
                        "attachment": {
                            "type": "image",
                            "payload": {
                                "url": imagen,
                                "is_reusable": True
                            }
                        }
                    }
                }
            })

    return mensajes
