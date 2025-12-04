
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
# ⚠️ PEGA TU TOKEN AQUÍ (Si usas IA)
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
    .img-container { border-radius: 10px; overflow: hidden; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. CONEXIÓN A FIREBASE
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
# 3. FUNCIONES DE DATOS
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
        docs = db.collection("pedidos").order_by("creado_en", direction=firestore.Query.DESCENDING).stream()
        return [{**doc.to_dict(), 'id_firebase': doc.id} for doc in docs]
    except:
        # Fallback si no hay índice o fecha
        try:
            docs = db.collection("pedidos").stream()
            return [{**doc.to_dict(), 'id_firebase': doc.id} for doc in docs]
        except: return []

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

# --- FUNCIÓN AUXILIAR: IMAGEN SEGURA ---
def mostrar_imagen_segura(url):
    """Muestra una imagen o un placeholder si la URL está rota"""
    placeholder = "https://placehold.co/400x300?text=Sin+Imagen"
    
    if not url or not isinstance(url, str) or len(url) < 5:
        st.image(placeholder, use_container_width=True)
        return

    try:
        st.image(url, use_container_width=True)
    except Exception:
        st.image("https://placehold.co/400x300?text=Error+URL", use_container_width=True)
        st.caption("URL inválida")

# ==========================================
# 4. INTERFAZ GRÁFICA
# ==========================================

with st.sidebar:
    st.title("🛍️ Panel Admin")
    st.write("---")
    opcion = st.radio("Menú", ["Vista General", "Agregar Producto", "Editar / Borrar", "Usuarios", "Pedidos", "Asistente IA"])
    st.write("---")
    if st.button("🔄 Recargar Datos"):
        st.cache_data.clear()
        st.rerun()

# --- VISTA GENERAL ---
if opcion == "Vista General":
    st.header("📊 Inventario Visual")
    prods = obtener_todos_productos()
    
    if prods:
        df = pd.DataFrame(prods)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Productos", len(df))
        c2.metric("Valor Inventario", f"${(df['precio']*df['stock']).sum():,.0f}")
        c3.metric("En Oferta", len(df[df['oferta']==True]) if 'oferta' in df else 0)
        
        st.write("---")
        
        # Filtros
        cat_filtro = st.multiselect("Filtrar por Categoría", df['categoria'].unique())
        if cat_filtro:
            df = df[df['categoria'].isin(cat_filtro)]
            prods = df.to_dict('records')

        st.subheader("🖼️ Galería")
        cols = st.columns(4)
        for i, row in enumerate(prods):
            with cols[i % 4]:
                with st.container(border=True):
                    mostrar_imagen_segura(row.get('imagen_url'))
                    st.markdown(f"**{row.get('nombre')}**")
                    st.caption(f"{row.get('categoria')} | Stock: {row.get('stock')}")
                    if row.get('oferta'):
                        st.markdown(f"🔥 **${row.get('precio')}**")
                    else:
                        st.markdown(f"💰 **${row.get('precio')}**")

    else: st.info("Cargando datos...")

# --- AGREGAR ---
elif opcion == "Agregar Producto":
    st.header("➕ Nuevo Producto")
    with st.form("add"):
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre")
        precio = c1.number_input("Precio", min_value=0.0)
        stock = c2.number_input("Stock", min_value=0)
        cat = c2.selectbox("Categoría", ["Ropa", "Calzado", "Accesorios", "Tecnología", "Hogar", "Deportes", "Otros"])
        url = st.text_input("Imagen URL (Ej: https://i.imgur.com/foto.jpg)")
        st.caption("Tip: Usa 'Copy Image Address' en Google o sube tu foto a imgur.com")
        
        if st.form_submit_button("Guardar"):
            guardar_producto({"nombre": nombre, "precio": precio, "stock": stock, "categoria": cat, "imagen_url": url, "oferta": False})
            st.success("Guardado")
            st.rerun()

# --- EDITAR ---
elif opcion == "Editar / Borrar":
    st.header("✏️ Editar Producto")
    prods = obtener_todos_productos()
    if prods:
        sel = st.selectbox("Producto:", [f"{p['nombre']} ({p['id_firebase']})" for p in prods])
        idx = [f"{p['nombre']} ({p['id_firebase']})" for p in prods].index(sel)
        p = prods[idx]
        
        col_form, col_prev = st.columns([2,1])
        with col_form:
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
        with col_prev:
            st.write("Vista Previa:")
            mostrar_imagen_segura(p.get('imagen_url'))
            st.error("Zona de Peligro")
            if st.button("🗑️ Borrar Producto"):
                eliminar_producto(p['id_firebase'])
                st.warning("Eliminado")
                st.rerun()

# --- USUARIOS ---
elif opcion == "Usuarios":
    st.header("👥 Clientes")
    users = obtener_usuarios()
    if users:
        st.dataframe(pd.DataFrame(users), use_container_width=True)
        st.write("---")
        user_list = [f"{u.get('nombre')} ({u.get('id_firebase')})" for u in users]
        sel_user = st.selectbox("Editar Usuario:", user_list)
        if sel_user:
            idx = user_list.index(sel_user)
            u = users[idx]
            with st.form("edit_user"):
                new_name = st.text_input("Nombre", u.get('nombre'))
                new_addr = st.text_input("Dirección", u.get('Direccion', u.get('direccion', '')))
                if st.form_submit_button("Guardar Cambios"):
                    u['nombre'] = new_name
                    u['Direccion'] = new_addr
                    del u['id_firebase']
                    guardar_usuario(u, users[idx]['id_firebase'])
                    st.success("Actualizado")
                    st.rerun()
            if st.button("🗑️ Eliminar Usuario"):
                eliminar_usuario(users[idx]['id_firebase'])
                st.rerun()
    else: st.info("Sin usuarios.")

# --- PEDIDOS ---
elif opcion == "Pedidos":
    st.header("📦 Pedidos")
    orders = obtener_pedidos()
    if orders:
        st.dataframe(pd.DataFrame(orders), use_container_width=True)
        st.write("---")
        opts = [f"Pedido {o['id_firebase']} (${o.get('total',0)})" for o in orders]
        sel = st.selectbox("Ver detalle:", opts)
        if sel:
            o = orders[opts.index(sel)]
            c1, c2, c3 = st.columns(3)
            c1.metric("Cliente", o.get('nombre', 'N/A'))
            c2.metric("Total", f"${o.get('total',0)}")
            c3.metric("Teléfono", o.get('telefono', 'N/A'))
            
            st.write("Productos:")
            items = o.get('productos', [])
            if isinstance(items, list): st.table(pd.DataFrame(items))
            else: st.json(items)
            
            if st.button("🗑️ Eliminar Pedido"):
                eliminar_pedido(o['id_firebase'])
                st.success("Pedido eliminado")
                st.rerun()
    else: st.info("Sin pedidos.")

# --- IA ---
elif opcion == "Asistente IA":
    st.header("🤖 Analista")
    if "messages" not in st.session_state: st.session_state.messages = []
    for m in st.session_state.messages:
        with st.chat_message(m["role"]): st.markdown(m["content"])
        
    if p := st.chat_input("Pregunta..."):
        if "PEGAR_TU_TOKEN" in HF_TOKEN: st.error("Falta Token en código")
        else:
            st.session_state.messages.append({"role": "user", "content": p})
            with st.chat_message("user"): st.markdown(p)
            with st.spinner("..."):
                try:
                    prods = obtener_todos_productos()
                    usrs = obtener_usuarios()
                    # Resumen muy breve para no saturar token limit
                    contexto = f"Productos: {len(prods)}. Clientes: {len(usrs)}."
                    client = InferenceClient(token=HF_TOKEN)
                    resp = client.chat_completion(
                        messages=[{"role":"system","content":f"Eres asistente administrativo. Datos: {contexto}"}, 
                                  {"role":"user","content":p}],
                        model="Qwen/Qwen2.5-7B-Instruct", max_tokens=300
                    )
                    rta = resp.choices[0].message.content
                    st.session_state.messages.append({"role": "assistant", "content": rta})
                    with st.chat_message("assistant"): st.markdown(rta)
                except Exception as e: st.error(str(e))