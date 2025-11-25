# registro_usuario.py
from datetime import datetime
from conexion_firebase import db  # 🔥 Importa la conexión ya inicializada

def registrar_usuario(telefono: str, nombre: str, direccion: str = "") -> str:
    """
    Registra un nuevo usuario en la colección 'usuarios' de Firestore.
    Si el número ya existe, devuelve un mensaje indicando que ya está registrado.
    """
    usuarios_ref = db.collection("usuarios")
    usuario_doc = usuarios_ref.document(telefono)
    
    if usuario_doc.get().exists:
        return f"Ya estás registrado, {nombre}."

    datos = {
        "nombre": nombre,
        "telefono": telefono,
        "rol": "Cliente",
        "Direccion": direccion,
        "Fecha_registro": datetime.now().strftime("%d/%m/%y")
    }

    try:
        usuario_doc.set(datos)
        return f"✅ ¡Registro exitoso, {nombre}! Ahora puedes realizar pedidos."
    except Exception as e:
        print("🔥 Error en registrar_usuario():", e)
        return "Hubo un error al registrarte. Intenta más tarde."
