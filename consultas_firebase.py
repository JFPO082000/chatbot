# consultas_firebase.py
from conexion_firebase import db

def obtener_categorias_con_productos():
    """
    Devuelve las categorías únicas de la colección 'productos'
    que tienen al menos un documento con campo 'categoria'.
    """
    productos_ref = db.collection("productos").stream()
    categorias = {}

    for doc in productos_ref:
        data = doc.to_dict()
        categoria = data.get("categoria", "").strip()
        if categoria:
            categorias[categoria] = categorias.get(categoria, 0) + 1

    return list(categorias.items())  # [(categoria, total), ...]

def obtener_productos_por_categoria(nombre_categoria):
    """
    Devuelve los productos que coincidan con una categoría.
    """
    if not nombre_categoria:
        return []
    productos_ref = db.collection("productos").where("categoria", "==", nombre_categoria).stream()
    productos = [doc.to_dict() for doc in productos_ref]
    return productos
