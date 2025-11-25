import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Leer las credenciales desde la variable de entorno
firebase_config = os.getenv("FIREBASE_CREDENTIALS")

if not firebase_config:
    raise ValueError("❌ No se encontró la variable FIREBASE_CREDENTIALS en Render")

# Convertir el texto JSON en diccionario Python
try:
    cred_dict = json.loads(firebase_config)
    cred = credentials.Certificate(cred_dict)
except json.JSONDecodeError as e:
    raise ValueError(f"❌ Error al parsear FIREBASE_CREDENTIALS: {e}")
except Exception as e:
    raise ValueError(f"❌ Error al crear credenciales de Firebase: {e}")

# Inicializar Firebase solo si no está activo
try:
    if not firebase_admin._apps:
        default_app = firebase_admin.initialize_app(cred)
        print("✅ Firebase inicializado correctamente")
    else:
        default_app = firebase_admin.get_app()
        print("✅ Firebase ya estaba inicializado")
except Exception as e:
    raise ValueError(f"❌ Error al inicializar Firebase: {e}")

# Inicializar Firestore con la app explícitamente
try:
    db = firestore.client(app=default_app)
    print("✅ Cliente Firestore creado correctamente")
except Exception as e:
    raise ValueError(f"❌ Error al crear cliente Firestore: {e}")

# --- Función para obtener productos ---
def obtener_productos():
    """Devuelve todos los productos de la colección 'productos'."""
    productos = {}
    try:
        docs = db.collection("productos").stream()
        for doc in docs:
            productos[doc.id] = doc.to_dict()
        print(f"✅ Se obtuvieron {len(productos)} productos de Firebase")
    except Exception as e:
        print(f"🔥 Error en obtener_productos(): {type(e).__name__} - {e}")
    return productos
