import io
iimport io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text, pool

# 👇 PRIMERO SIEMPRE
st.set_page_config(
    page_title="Monitor de Crédito",
    page_icon="📊",
    layout="wide",
)

# 👇 DESPUÉS ya puedes usar st.*
st.write("🚀 App iniciando...")
# ─────────────────────────────────────────────
# 2. CONSTANTES
# ─────────────────────────────────────────────
CONDICIONES_CREDITO   = {"CP008", "CP15", "CP20", "CP25", "CP30", "CP45"}
CONDICION_EXCEPCION   = {"CP45"}
CONDICION_ANTICIPADO  = {"CP00"}
CONDICIONES_INACTIVO  = {"MG01", "MG02", "MG03", "MG04", "MG06", "0001"}
CONDICION_CRA         = {"CRA"}
CONDICION_RECLAMACION = {"MG05"}

PRIORIDAD_ESTATUS = {
    "🔴 Suspendido": 0, "🟣 Reclamación": 1, "🟠 Activo (Excepción)": 2,
    "🔵 CRA": 3, "⚫ Inactivo": 4, "🟢 Activo": 5, "⚪ Sin clasificar": 6,
}

COLS_NUM = ["Saldo vencido", "Saldo por vencer", "Anticipos", "Depósitos SAP", "Límite de credito"]

COLUMNAS_REQUERIDAS = [
    "Cliente", "Destinatario mercancia", "Condiciones de pago",
    "Nombre 1", "fecha"
] + COLS_NUM

# ─────────────────────────────────────────────
# 3. CONEXIÓN A POSTGRESQL (SUPABASE)
# ─────────────────────────────────────────────
@st.cache_resource
def get_engine():
    return create_engine(
        st.secrets["DATABASE_URL"],
        poolclass=pool.NullPool,
        connect_args={"sslmode": "require"}
    )

def cargar_desde_postgresql(query) -> pd.DataFrame:
    try:
        engine = get_engine()
        with engine.connect() as con:
            df = pd.read_sql(text(query), con)

            # Mapear nombres DB → App
            column_map = {
                'cliente': 'Cliente',
                'destinatario_mercancia': 'Destinatario mercancia',
                'condiciones_pago': 'Condiciones de pago',
                'nombre': 'Nombre 1',
                'fecha': 'fecha',
                'saldo_vencido': 'Saldo vencido',
                'saldo_por_vencer': 'Saldo por vencer',
                'anticipos': 'Anticipos',
                'depositos_sap': 'Depósitos SAP',
                'limite_credito': 'Límite de credito'
            }

            return df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

    except Exception as e:
        raise RuntimeError(f"Error en PostgreSQL: {e}")

# ─────────────────────────────────────────────
# 4. LIMPIEZA DE DATOS
# ─────────────────────────────────────────────
def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    for c in COLS_NUM:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    for c in ["Cliente", "Destinatario mercancia", "Condiciones de pago", "Nombre 1"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    return df

# ─────────────────────────────────────────────
# 5. LÓGICA DE NEGOCIO
# ─────────────────────────────────────────────
def transformar(df: pd.DataFrame):
    df = df.copy()

    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"]).sort_values(
        ["Cliente", "Destinatario mercancia", "fecha"]
    )

    activos = df["Anticipos"] + df["Depósitos SAP"]

    df["Sobregiro"] = 0.0
    df["Incumplimiento"] = 0.0

    mask_cred = df["Condiciones de pago"].isin(CONDICIONES_CREDITO)

    df.loc[mask_cred, "Sobregiro"] = (
        df["Saldo vencido"] + df["Saldo por vencer"]
    ) - activos

    df.loc[mask_cred, "Incumplimiento"] = (
        df["Saldo vencido"] - activos
    )

    df["Uso_vs_Limite"] = (
        df["Saldo vencido"] + df["Saldo por vencer"]
    ) - df["Límite de credito"]

    def _est(row):
        c = row["Condiciones de pago"]

        if c in CONDICIONES_INACTIVO:
            return "⚫ Inactivo"
        if c in CONDICION_RECLAMACION:
            return "🟣 Reclamación"
        if row["Sobregiro"] > 0.01:
            return "🔴 Suspendido"

        return "🟢 Activo"

    df["Estatus"] = df.apply(_est, axis=1)

    snapshot = (
        df.groupby(["Cliente", "Destinatario mercancia"])
        .last()
        .reset_index()
    )

    return snapshot, df

def resumen_por_cliente(snapshot):
    return (
        snapshot.groupby("Cliente")
        .agg({
            "Nombre 1": "first",
            "Saldo vencido": "sum",
            "Sobregiro": "sum",
            "Estatus": "last"
        })
        .reset_index()
    )

# ─────────────────────────────────────────────
# 6. MAIN
# ─────────────────────────────────────────────
if "snapshot" not in st.session_state:
    st.session_state.update({"snapshot": None, "historico": None})

# Carga automática
if st.session_state.snapshot is None:
    try:
        raw = cargar_desde_postgresql("SELECT * FROM historico_monitor")
        st.session_state.snapshot, st.session_state.historico = transformar(
            normalizar_columnas(raw)
        )
    except Exception as e:
        st.error(f"❌ Error de conexión: {e}")
        st.stop()

# ─────────────────────────────────────────────
# 7. UI
# ─────────────────────────────────────────────
st.title("📊 Monitor de Crédito")

df_cli = resumen_por_cliente(st.session_state.snapshot)

k1, k2, k3 = st.columns(3)

k1.metric("Clientes", len(df_cli))
k2.metric("Sobregiro Total", f"${df_cli['Sobregiro'].sum():,.2f}")
k3.metric("Estatus", "Conectado ✅")

st.subheader("Listado de Clientes")
st.dataframe(df_cli, use_container_width=True)

# Sidebar
with st.sidebar:
    st.header("Opciones")

    if st.button("🔄 Recargar Datos"):
        st.session_state.snapshot = None
        st.cache_resource.clear()

        st.rerun()

