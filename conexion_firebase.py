import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Leer las credenciales desde la variable de entorno
firebase_config = os.getenv("FIREBASE_CREDENTIALS")

if not firebase_config:
    raise ValueError("❌ No se encontró la variable FIREBASE_CREDENTIALS en Render")

# Convertir el texto JSON en diccionario Python
cred_dict = json.loads(firebase_config)
cred = credentials.Certificate(cred_dict)

# Inicializar Firebase solo si no está activo
if not firebase_admin._apps:
    default_app = firebase_admin.initialize_app(cred)
else:
    default_app = firebase_admin.get_app()

# Inicializar Firestore con la app explícitamente
db = firestore.client(app=default_app)

# --- Función para obtener productos ---
def obtener_productos():
    """Devuelve todos los productos de la colección 'productos'."""
    productos = {}
    try:
        docs = db.collection("productos").stream()
        for doc in docs:
            productos[doc.id] = doc.to_dict()
    except Exception as e:
        print("🔥 Error en obtener_productos():", e)
    return productos
