import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import os
import logging
import warnings
from huggingface_hub import InferenceClient

# ==========================================
# 1. CONFIGURACIÓN RÁPIDA
# ==========================================
# ⚠️ PEGA TU TOKEN AQUÍ PARA NO ESCRIBIRLO CADA VEZ
HF_TOKEN = "PEGAR_TU_TOKEN_AQUI" 

os.environ["STREAMLIT_LOG_LEVEL"] = "error"
logging.getLogger("streamlit").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="Admin Frere's Collection", 
    layout="wide",
    page_icon="🛍️"
)

st.markdown("""
    <style>
    .stButton>button { width: 100%; }
    .reportview-container { margin-top: -2em; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. CONEXIÓN A FIREBASE (CACHEADA)
# ==========================================
ARCHIVO_CREDENCIALES = "credenciales.json"

@st.cache_resource
def conectar_firebase():
    try:
        if firebase_admin._apps:
            return firestore.client()
        if os.path.exists(ARCHIVO_CREDENCIALES):
            cred = credentials.Certificate(ARCHIVO_CREDENCIALES)
            firebase_admin.initialize_app(cred)
            return firestore.client()
        else:
            st.error(f"⚠️ No encuentro '{ARCHIVO_CREDENCIALES}'")
            return None
    except Exception as e:
        st.error(f"Error de conexión: {e}")
        return None

db = conectar_firebase()

# ==========================================
# 3. FUNCIONES DE DATOS (OPTIMIZADAS)
# ==========================================

@st.cache_data(ttl=300) 
def obtener_todos_productos():
    if not db: return []
    try:
        docs = db.collection("productos").stream()
        return [{**doc.to_dict(), 'id_firebase': doc.id} for doc in docs]
    except: return []

@st.cache_data(ttl=300)
def obtener_usuarios():
    if not db: return []
    try:
        docs = db.collection("usuarios").stream()
        return [{**doc.to_dict(), 'id_firebase': doc.id} for doc in docs]
    except: return []

@st.cache_data(ttl=300)
def obtener_pedidos():
    if not db: return []
    try:
        # Ordenamos por fecha descendente si es posible, si no, traerá por defecto
        docs = db.collection("pedidos").stream()
        return [{**doc.to_dict(), 'id_firebase': doc.id} for doc in docs]
    except: return []

# Funciones de escritura (Sin caché)
def guardar_producto(datos, doc_id=None):
    if not db: return
    col = db.collection("productos")
    if doc_id: col.document(str(doc_id)).set(datos)
    else: col.add(datos)
    obtener_todos_productos.clear()

def eliminar_producto(doc_id):
    if not db: return
    db.collection("productos").document(doc_id).delete()
    obtener_todos_productos.clear()

def guardar_usuario(datos, doc_id):
    if not db: return
    # Usamos merge=True para actualizar solo los campos cambiados si fuera necesario, 
    # pero aquí sobreescribimos con los datos del formulario.
    db.collection("usuarios").document(str(doc_id)).set(datos)
    obtener_usuarios.clear()

def eliminar_usuario(doc_id):
    if not db: return
    db.collection("usuarios").document(doc_id).delete()
    obtener_usuarios.clear()

def eliminar_pedido(doc_id):
    if not db: return
    db.collection("pedidos").document(doc_id).delete()
    obtener_pedidos.clear()

# ==========================================
# 4. INTERFAZ GRÁFICA
# ==========================================

with st.sidebar:
    st.title("🛍️ Panel Admin")
    st.write("---")
    # Agregamos "Pedidos" al menú
    opcion = st.radio("Menú", ["Vista General", "Agregar Producto", "Editar / Borrar", "Usuarios", "Pedidos", "Asistente IA"])
    st.write("---")
    
    if st.button("🔄 Recargar Datos (Limpiar Caché)"):
        st.cache_data.clear()
        st.rerun()

# --- PÁGINA 1: VISTA GENERAL ---
if opcion == "Vista General":
    st.header("📊 Inventario General")
    prods = obtener_todos_productos()
    
    if prods:
        df = pd.DataFrame(prods)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Productos", len(df))
        c2.metric("Valor Inventario", f"${(df['precio']*df['stock']).sum():,.0f}")
        c3.metric("En Oferta", len(df[df['oferta']==True]) if 'oferta' in df else 0)
        st.dataframe(df, use_container_width=True)
    else: st.info("Cargando datos o base de datos vacía...")

# --- PÁGINA 2: AGREGAR ---
elif opcion == "Agregar Producto":
    st.header("➕ Nuevo Producto")
    with st.form("add"):
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre")
        precio = c1.number_input("Precio", min_value=0.0)
        stock = c2.number_input("Stock", min_value=0)
        cat = c2.selectbox("Categoría", ["Ropa", "Calzado", "Accesorios", "Tecnología", "Hogar", "Deportes", "Otros"])
        url = st.text_input("Imagen URL")
        if st.form_submit_button("Guardar"):
            guardar_producto({"nombre": nombre, "precio": precio, "stock": stock, "categoria": cat, "imagen_url": url, "oferta": False})
            st.success("Guardado")
            st.rerun()

# --- PÁGINA 3: EDITAR ---
elif opcion == "Editar / Borrar":
    st.header("✏️ Editar Producto")
    prods = obtener_todos_productos()
    if prods:
        sel = st.selectbox("Selecciona Producto:", [f"{p['nombre']} ({p['id_firebase']})" for p in prods])
        idx = [f"{p['nombre']} ({p['id_firebase']})" for p in prods].index(sel)
        p = prods[idx]
        with st.form("edit"):
            nom = st.text_input("Nombre", p.get('nombre'))
            c1, c2 = st.columns(2)
            pre = c1.number_input("Precio", value=float(p.get('precio', 0)))
            sto = c2.number_input("Stock", value=int(p.get('stock', 0)))
            img = st.text_input("Imagen URL", p.get('imagen_url', ''))
            
            if st.form_submit_button("Actualizar"):
                p.update({"nombre": nom, "precio": pre, "stock": sto, "imagen_url": img})
                guardar_producto(p, p['id_firebase'])
                st.success("Listo")
                st.rerun()
        
        st.write("---")
        if st.button("🗑️ Borrar Producto de forma permanente"):
            eliminar_producto(p['id_firebase'])
            st.warning("Eliminado")
            st.rerun()

# --- PÁGINA 4: USUARIOS (MEJORADA) ---
elif opcion == "Usuarios":
    st.header("👥 Gestión de Clientes")
    users = obtener_usuarios()
    if users:
        # Tabla general
        st.dataframe(pd.DataFrame(users), use_container_width=True)
        st.write("---")
        
        # Selector de edición
        st.subheader("🛠️ Modificar / Eliminar Usuario")
        user_list = [f"{u.get('nombre', 'Sin Nombre')} (Tel: {u.get('id_firebase')})" for u in users]
        sel_user = st.selectbox("Selecciona un usuario para editar:", user_list)
        
        if sel_user:
            idx = user_list.index(sel_user)
            u = users[idx]
            doc_id = u['id_firebase']
            
            with st.form("form_user_edit"):
                c1, c2 = st.columns(2)
                # Permitimos editar nombre y dirección. El teléfono es el ID, mejor no tocarlo.
                new_name = c1.text_input("Nombre Completo", u.get('nombre', ''))
                new_address = c2.text_input("Dirección", u.get('Direccion', u.get('direccion', '')))
                new_role = st.selectbox("Rol", ["Cliente", "Admin"], index=0 if u.get('rol') != "Admin" else 1)
                
                if st.form_submit_button("💾 Guardar Cambios"):
                    # Actualizamos el diccionario del usuario
                    u_update = u.copy()
                    u_update['nombre'] = new_name
                    u_update['Direccion'] = new_address # Normalizamos la key
                    u_update['rol'] = new_role
                    # Quitamos el ID interno antes de guardar
                    if 'id_firebase' in u_update: del u_update['id_firebase']
                    
                    guardar_usuario(u_update, doc_id)
                    st.success("Datos del usuario actualizados.")
                    st.rerun()
            
            st.write("---")
            if st.button("🗑️ Eliminar Usuario Definitivamente"):
                eliminar_usuario(doc_id)
                st.error("Usuario eliminado.")
                st.rerun()
    else: st.info("No hay usuarios registrados.")

# --- PÁGINA 5: PEDIDOS (NUEVA) ---
elif opcion == "Pedidos":
    st.header("📦 Historial de Pedidos")
    orders = obtener_pedidos()
    
    if orders:
        # Convertimos a DataFrame para visualización rápida
        df_orders = pd.DataFrame(orders)
        
        # Mostramos columnas clave si existen
        cols_clave = ['id_firebase', 'nombre', 'total', 'estado', 'fecha']
        cols_existentes = [c for c in cols_clave if c in df_orders.columns]
        
        if cols_existentes:
            st.dataframe(df_orders[cols_existentes], use_container_width=True)
        else:
            st.dataframe(df_orders, use_container_width=True)
            
        st.write("---")
        st.subheader("🔎 Detalle del Pedido")
        
        # Selector de pedido
        order_opts = [f"Pedido {o['id_firebase']} - {o.get('nombre', 'Cliente')} (${o.get('total', 0)})" for o in orders]
        sel_order = st.selectbox("Selecciona un pedido para ver detalles:", order_opts)
        
        if sel_order:
            oidx = order_opts.index(sel_order)
            o = orders[oidx]
            
            # Tarjetas de detalle
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cliente", o.get('nombre', 'Desconocido'))
            c2.metric("Total Pagado", f"${o.get('total', 0)}")
            c3.metric("Estado", o.get('estado', 'Pendiente'))
            c4.metric("Teléfono", o.get('telefono', 'N/A'))
            
            st.markdown("##### 🛒 Productos en este pedido:")
            items = o.get('productos', []) # Asumiendo que guardas una lista de items
            if items and isinstance(items, list):
                # Convertimos la lista de productos a una tabla pequeña
                df_items = pd.DataFrame(items)
                st.table(df_items)
            else:
                st.json(items) # Si el formato es distinto, lo mostramos crudo
            
            with st.expander("Ver datos técnicos (JSON)"):
                st.json(o)
            
            # --- BOTÓN DE ELIMINAR PEDIDO ---
            st.write("---")
            if st.button("🗑️ Eliminar este Pedido"):
                eliminar_pedido(o['id_firebase'])
                st.warning("Pedido eliminado correctamente de la base de datos.")
                st.rerun()

    else:
        st.info("Aún no hay pedidos en la base de datos.")

# --- PÁGINA 6: ASISTENTE IA ---
elif opcion == "Asistente IA":
    st.header("🤖 Analista Virtual")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Pregunta sobre tu negocio..."):
        if "PEGAR_TU_TOKEN" in HF_TOKEN:
            st.error("⚠️ Configura tu token en la línea 19 del código.")
        else:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.spinner("Analizando datos..."):
                try:
                    # Contexto enriquecido con pedidos, usuarios y productos
                    prods = obtener_todos_productos()
                    usrs = obtener_usuarios()
                    ords = obtener_pedidos()
                    
                    contexto = f"""
                    Resumen del Negocio:
                    - Productos totales: {len(prods)}
                    - Usuarios registrados: {len(usrs)}
                    - Pedidos realizados: {len(ords)}
                    
                    Ejemplos de productos: {[p.get('nombre') for p in prods[:5]]}
                    """
                    
                    client = InferenceClient(token=HF_TOKEN)
                    sistema = f"Eres un asistente administrativo. Usa estos datos para responder: {contexto}"

                    resp = client.chat_completion(
                        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": prompt}],
                        model="Qwen/Qwen2.5-7B-Instruct",
                        max_tokens=500
                    )
                    
                    rta = resp.choices[0].message.content
                    st.session_state.messages.append({"role": "assistant", "content": rta})
                    with st.chat_message("assistant"):
                        st.markdown(rta)
                        
                except Exception as e:
                    st.error(f"Error IA: {e}")