import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text, pool

# ─────────────────────────────────────────────
# 1. CONFIGURACIÓN Y CONSTANTES (Reglas de Negocio)
# ─────────────────────────────────────────────
st.set_page_config(page_title="Monitor de Crédito Pro", page_icon="📊", layout="wide")

# Grupos de condiciones según reglas
CONDICIONES_CREDITO = {"CP008", "CP15", "CP20", "CP25", "CP30", "CP45"}
CONDICION_EXCEPCION = "CP45"
COND_ANTICIPADO = "CP00"
COND_RECLAMACION = "MG05"
COND_INACTIVO = {"MG01", "MG02", "MG03", "MG04", "MG06", "0001"}
COND_CRA = "CRA"

# ─────────────────────────────────────────────
# 2. CONEXIÓN Y DATOS
# ─────────────────────────────────────────────
@st.cache_resource
def get_engine():
    return create_engine(
        st.secrets["DATABASE_URL"],
        poolclass=pool.NullPool,
        connect_args={"sslmode": "require"}
    )

@st.cache_data(ttl=600)
def cargar_snapshot_actual():
    """
    Regla: Si hay múltiples cargas en un día, se toma el PRIMER registro.
    Usamos DISTINCT ON con ORDER BY ASC en el tiempo para capturar el primer snapshot.
    """
    query = """
    SELECT DISTINCT ON (cliente, destinatario_mercancia, fecha::date)
        cliente, destinatario_mercancia, nombre_1 as nombre, condiciones_pago,
        fecha, saldo_vencido, saldo_por_vencer, anticipos,
        depositos_sap, limite_credito
    FROM historico_monitor
    ORDER BY cliente, destinatario_mercancia, fecha::date DESC, fecha ASC
    """
    engine = get_engine()
    with engine.connect() as con:
        df = pd.read_sql(text(query), con)
    
    # Nos quedamos solo con la fecha más reciente disponible en el dataset para el Dashboard
    ultima_fecha = df['fecha'].max().date()
    return df[df['fecha'].dt.date == ultima_fecha]

@st.cache_data(ttl=300)
def cargar_historico_cliente(cliente):
    """Carga todos los días (primer registro de cada día) para un cliente."""
    query = text("""
        SELECT DISTINCT ON (destinatario_mercancia, fecha::date)
            cliente, destinatario_mercancia, nombre_1 as nombre, saldo_vencido, 
            saldo_por_vencer, anticipos, depositos_sap, limite_credito, fecha, condiciones_pago
        FROM historico_monitor
        WHERE cliente = :c
        ORDER BY destinatario_mercancia, fecha::date ASC, fecha ASC
    """)
    engine = get_engine()
    with engine.connect() as con:
        return pd.read_sql(query, con, params={"c": cliente})

# ─────────────────────────────────────────────
# 3. MOTOR DE CÁLCULOS Y ESTATUS (Reglas de Negocio)
# ─────────────────────────────────────────────
def aplicar_reglas_negocio(df):
    df = df.copy()
    
    # A. Cálculos Financieros
    # Sobregiro = (Saldo vencido + Saldo por vencer) – (Anticipos + Depósitos SAP)
    df["Sobregiro"] = (df["saldo_vencido"] + df["saldo_por_vencer"]) - (df["anticipos"] + df["depositos_sap"])
    
    # Incumplimiento = Saldo vencido – (Anticipos + Depósitos SAP)
    df["Incumplimiento"] = df["saldo_vencido"] - (df["anticipos"] + df["depositos_sap"])
    
    # % Uso de crédito = (Saldo vencido + Saldo por vencer) – Límite de crédito
    df["Uso_Credito_Monto"] = (df["saldo_vencido"] + df["saldo_por_vencer"]) - df["limite_credito"]

    # B. Clasificación de Estatus (Lógica de Semáforo)
    def clasificar(row):
        cond = str(row["condiciones_pago"]).strip()
        sob = row["Sobregiro"]
        inc = row["Incumplimiento"]
        
        # 1. Otros Estatus (No operativos)
        if cond == COND_RECLAMACION: return "🟣 Reclamación"
        if cond == COND_CRA: return "🔵 CRA"
        if cond in COND_INACTIVO: return "⚫ Inactivo"
        
        # 2. Excepciones
        if cond == COND_EXCEPCION: return "🟠 Activo (Excepción)"
        
        # 3. Operativos (Crédito y Anticipado)
        if cond in CONDICIONES_CREDITO or cond == COND_ANTICIPADO:
            if sob > 0.01 or inc > 0.01:
                return "🔴 Suspendido"
            return "🟢 Activo"
        
        return "⚪ Stand By / Otro"

    df["Estatus_Operativo"] = df.apply(clasificar, axis=1)
    return df

# ─────────────────────────────────────────────
# 4. INTERFAZ (UI)
# ─────────────────────────────────────────────
st.markdown("<style>[data-testid='stMetricValue'] { font-size: 1.8rem; }</style>", unsafe_allow_html=True)

with st.spinner("Aplicando reglas de negocio..."):
    df_actual = aplicar_reglas_negocio(cargar_snapshot_actual())

st.title("📊 Monitor de Control de Crédito")
st.caption(f"Corte de información: {df_actual['fecha'].max()}")

# KPIs Principales
m1, m2, m3, m4 = st.columns(4)
m1.metric("Cartera Total", f"${(df_actual['saldo_vencido'] + df_actual['saldo_por_vencer']).sum():,.2f}")
m2.metric("Sobregiro Real", f"${df_actual[df_actual['Sobregiro'] > 0]['Sobregiro'].sum():,.2f}", delta_color="inverse")
m3.metric("Incumplimiento", f"${df_actual[df_actual['Incumplimiento'] > 0]['Incumplimiento'].sum():,.2f}")
m4.metric("Clientes Suspendidos", len(df_actual[df_actual['Estatus_Operativo'] == "🔴 Suspendido"]))

tab1, tab2 = st.tabs(["📋 Resumen Operativo", "📈 Detalle por Cliente"])

with tab1:
    # Agrupamos por Cliente Central para el resumen ejecutivo
    df_resumen = df_actual.groupby("cliente").agg({
        "nombre": "first",
        "Sobregiro": "sum",
        "Incumplimiento": "sum",
        "Estatus_Operativo": lambda x: x.iloc[0] if len(x.unique()) == 1 else "⚠️ Mixto"
    }).reset_index()

    st.dataframe(
        df_resumen,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Sobregiro": st.column_config.NumberColumn(format="$%.2f"),
            "Incumplimiento": st.column_config.NumberColumn(format="$%.2f"),
            "Estatus_Operativo": "Estatus Actual"
        }
    )

with tab2:
    cliente_sel = st.selectbox("Seleccione un Cliente Central:", options=df_actual["cliente"].unique())
    
    if cliente_sel:
        # Cargamos el historial aplicando las mismas reglas a cada punto en el tiempo
        hist = aplicar_reglas_negocio(cargar_historico_cliente(cliente_sel))
        
        # Gráfico de evolución de Sobregiro
        st.write("### Evolución Financiera (Histórico)")
        grafico_data = hist.groupby("fecha")[["Sobregiro", "Incumplimiento"]].sum()
        st.line_chart(grafico_data)
        
        # Tabla detallada por Destinatario
        st.write("### Desglose por Destinatario de Mercancía")
        st.dataframe(
            hist[["fecha", "destinatario_mercancia", "condiciones_pago", "Sobregiro", "Incumplimiento", "Estatus_Operativo"]].sort_values("fecha", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Sobregiro": st.column_config.NumberColumn(format="$%.2f"),
                "Incumplimiento": st.column_config.NumberColumn(format="$%.2f"),
            }
        )