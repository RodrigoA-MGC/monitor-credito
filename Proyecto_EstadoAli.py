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
# HISTÓRICO POR CLIENTE (on demand)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300)
# Cámbialo por esto para que sea más seguro:
def cargar_historico_cliente(cliente):
    query = text("SELECT * FROM historico_monitor WHERE cliente = :c ORDER BY fecha DESC")
    engine = get_engine()
    with engine.connect() as con:
        df = pd.read_sql(query, con, params={"c": cliente})
    return df

# ─────────────────────────────────────────────
# LÓGICA DE NEGOCIO (ligera)
# ─────────────────────────────────────────────
def calcular_indicadores(df):
    df = df.copy()

    df["Sobregiro"] = (
        (df["saldo_vencido"] + df["saldo_por_vencer"])
        - (df["anticipos"] + df["depositos_sap"])
    )

    df["Incumplimiento"] = (
        df["saldo_vencido"]
        - (df["anticipos"] + df["depositos_sap"])
    )

    return df

# ─────────────────────────────────────────────
# CARGA INICIAL
# ─────────────────────────────────────────────
with st.spinner("Cargando datos..."):
    df = cargar_snapshot()
    df = calcular_indicadores(df)

# ─────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────
k1, k2, k3 = st.columns(3)

k1.metric("Clientes", df["cliente"].nunique())
k2.metric("Sobregiro Total", f"${df['Sobregiro'].sum():,.2f}")
k3.metric("Registros", len(df))

# ─────────────────────────────────────────────
# TABLA PRINCIPAL
# ─────────────────────────────────────────────
st.subheader("📋 Clientes")

df_clientes = (
    df.groupby("cliente")
    .agg(
        nombre=("nombre", "first"),
        sobregiro=("Sobregiro", "sum"),
        saldo=("saldo_vencido", "sum")
    )
    .reset_index()
)

st.dataframe(
    df_clientes, 
    use_container_width=True,
    column_config={
        "sobregiro": st.column_config.NumberColumn(format="$%.2f"),
        "saldo": st.column_config.NumberColumn(format="$%.2f")
    }
)

# ─────────────────────────────────────────────
# SELECTOR CLIENTE
# ─────────────────────────────────────────────
st.subheader("👤 Detalle Cliente")

cliente_sel = st.selectbox(
    "Selecciona cliente",
    df_clientes["cliente"]
)

# ─────────────────────────────────────────────
# HISTÓRICO DINÁMICO
# ─────────────────────────────────────────────
if cliente_sel:
    with st.spinner("Cargando histórico..."):
        hist = cargar_historico_cliente(cliente_sel)
        hist = calcular_indicadores(hist)

    st.write(f"Histórico de cliente: {cliente_sel}")
    st.dataframe(hist, use_container_width=True)

    # descarga
    def to_excel(df):
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        return buffer.getvalue()

    st.download_button(
        "📥 Descargar histórico",
        data=to_excel(hist),
        file_name=f"{cliente_sel}_historico.xlsx"
    )