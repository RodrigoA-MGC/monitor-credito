import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text, pool

# ─────────────────────────────────────────────
# CONFIG (PRIMERO SIEMPRE)
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de Crédito",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Monitor de Crédito")

# ─────────────────────────────────────────────
# CONEXIÓN
# ─────────────────────────────────────────────
@st.cache_resource
def get_engine():
    return create_engine(
        st.secrets["DATABASE_URL"],
        poolclass=pool.NullPool,
        connect_args={"sslmode": "require"}
    )

# ─────────────────────────────────────────────
# SNAPSHOT (rápido)
# ─────────────────────────────────────────────
@st.cache_data(ttl=600)
def cargar_snapshot():
    query = """
    SELECT DISTINCT ON (cliente, destinatario_mercancia)
        cliente,
        destinatario_mercancia,
        nombre,
        condiciones_pago,
        fecha,
        saldo_vencido,
        saldo_por_vencer,
        anticipos,
        depositos_sap,
        limite_credito
    FROM historico_monitor
    ORDER BY cliente, destinatario_mercancia, fecha DESC
    """
    engine = get_engine()
    with engine.connect() as con:
        df = pd.read_sql(text(query), con)
    return df

# ─────────────────────────────────────────────
# HISTÓRICO POR CLIENTE (Optimizado)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300)
def cargar_historico_cliente(cliente):
    query = text("""
        SELECT 
            cliente, 
            destinatario_mercancia, 
            nombre, 
            saldo_vencido, 
            saldo_por_vencer, 
            anticipos, 
            depositos_sap, 
            limite_credito, 
            fecha
        FROM historico_monitor
        WHERE cliente = :c
        ORDER BY fecha DESC
    """)
    engine = get_engine()
    with engine.connect() as con:
        df = pd.read_sql(query, con, params={"c": cliente})
    return df

# ─────────────────────────────────────────────
# LÓGICA DE NEGOCIO (ligera)
# ─────────────────────────────────────────────
def calcular_indicadores(df):
    df = df.copy()
    
    # Saldo total bruto
    df["Saldo Total"] = df["saldo_vencido"] + df["saldo_por_vencer"]
    
    # Cobertura de pagos
    pagos = df["anticipos"] + df["depositos_sap"]
    
    df["Sobregiro"] = df["Saldo Total"] - pagos
    df["Incumplimiento"] = df["saldo_vencido"] - pagos
    
    return df

# ─────────────────────────────────────────────
# CARGA INICIAL
# ─────────────────────────────────────────────
with st.spinner("Cargando datos..."):
    df_raw = cargar_snapshot()
    df = calcular_indicadores(df_raw)

# ─────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────
k1, k2, k3 = st.columns(3)

with k1:
    st.metric("Clientes Únicos", df["cliente"].nunique())
with k2:
    st.metric("Sobregiro Total", f"${df['Sobregiro'].sum():,.2f}")
with k3:
    st.metric("Registros en Snapshot", len(df))

# ─────────────────────────────────────────────
# TABLA PRINCIPAL
# ─────────────────────────────────────────────
st.subheader("📋 Resumen por Cliente")

# CORRECCIÓN: Se eliminó el espacio en "nombre "
df_clientes = (
    df.groupby("cliente")
    .agg(
        nombre=("nombre", "first"),
        sobregiro=("Sobregiro", "sum"),
        saldo_vencido=("saldo_vencido", "sum")
    )
    .reset_index()
)

st.dataframe(
    df_clientes, 
    use_container_width=True,
    hide_index=True, # Limpia la vista
    column_config={
        "cliente": "ID Cliente",
        "nombre": "Nombre del Cliente",
        "sobregiro": st.column_config.NumberColumn("Sobregiro Total", format="$%.2f"),
        "saldo_vencido": st.column_config.NumberColumn("Saldo Vencido", format="$%.2f")
    }
)

# ─────────────────────────────────────────────
# SELECTOR Y DETALLE
# ─────────────────────────────────────────────
st.divider()
st.subheader("👤 Detalle Histórico")

cliente_sel = st.selectbox(
    "Busca o selecciona un cliente para ver su historial:",
    options=df_clientes["cliente"],
    format_func=lambda x: f"{x} - {df_clientes[df_clientes['cliente']==x]['nombre'].values[0]}"
)

if cliente_sel:
    with st.spinner(f"Consultando historial de {cliente_sel}..."):
        hist = cargar_historico_cliente(cliente_sel)
        hist = calcular_indicadores(hist)

    st.write(f"Mostrando registros históricos para el cliente **{cliente_sel}**")
    
    # Formateo de la tabla de detalle
    st.dataframe(
        hist, 
        use_container_width=True,
        hide_index=True,
        column_config={
            "fecha": st.column_config.DateColumn("Fecha Corte"),
            "Saldo Total": st.column_config.NumberColumn(format="$%.2f"),
            "Sobregiro": st.column_config.NumberColumn(format="$%.2f"),
            "Incumplimiento": st.column_config.NumberColumn(format="$%.2f"),
            "limite_credito": st.column_config.NumberColumn("Límite", format="$%.2f")
        }
    )

    # Función de descarga optimizada
    @st.cache_data
    def to_excel(df):
        output = io.BytesIO()
        # Usamos context manager para asegurar que se guarde el archivo
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Historial')
        return output.getvalue()

    st.download_button(
        label="📥 Descargar historial en Excel",
        data=to_excel(hist),
        file_name=f"Historial_Cliente_{cliente_sel}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )