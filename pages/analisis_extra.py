import pandas as pd
import streamlit as st
from sqlalchemy import text
from datetime import date, timedelta

st.set_page_config(page_title="Análisis Extra", page_icon="📈")

st.title("📊 Análisis Profundo de Crédito")
st.write("Esta es una página independiente para ver detalles específicos.")

# Aquí podrías poner una consulta a tu nueva tabla de Postgres
# para ver, por ejemplo, los clientes con más deuda.


# ─────────────────────────────────────────────
# QUERY BASE (solo tu tabla actual)
# ─────────────────────────────────────────────
SQL_HISTORICO = text("""
    SELECT
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
    WHERE cliente = :cliente
    ORDER BY fecha DESC
""")

# ─────────────────────────────────────────────
# LÓGICA
# ─────────────────────────────────────────────
def clasificar_estatus(cond, sob, inc):
    if sob > 0 or inc > 0:
        return "🔴 Riesgo"
    return "🟢 Sano"

def calcular_metricas(df):
    df = df.copy()

    activos = df["anticipos"] + df["depositos_sap"]

    df["saldo_total"] = df["saldo_vencido"] + df["saldo_por_vencer"]
    df["sobregiro"] = (df["saldo_total"] - activos).clip(lower=0)
    df["incumplimiento"] = (df["saldo_vencido"] - activos).clip(lower=0)

    df["pct_uso"] = (
        df["saldo_total"] / df["limite_credito"].replace(0, 1)
    ) * 100

    df["estatus"] = df.apply(
        lambda r: clasificar_estatus(
            r["condiciones_pago"],
            r["sobregiro"],
            r["incumplimiento"]
        ),
        axis=1
    )

    return df

# ─────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────
def render(get_engine):

    st.title("🧮 Calculadora de Riesgo (Versión Simple)")
    st.caption("Basado únicamente en historico_monitor")

    engine = get_engine()

    cliente = st.text_input("🔍 Cliente")

    if not cliente:
        st.info("Ingresa un cliente")
        return

    # ─────────────────────────────────────────
    # CARGA
    # ─────────────────────────────────────────
    with st.spinner("Cargando..."):
        with engine.connect() as con:
            df = pd.read_sql(SQL_HISTORICO, con, params={"cliente": cliente})

    if df.empty:
        st.warning("No hay datos para este cliente")
        return

    df = calcular_metricas(df)

    # ─────────────────────────────────────────
    # SNAPSHOT ACTUAL (última fecha)
    # ─────────────────────────────────────────
    ultima_fecha = df["fecha"].max()
    snap = df[df["fecha"] == ultima_fecha]

    # ─────────────────────────────────────────
    # KPIs
    # ─────────────────────────────────────────
    st.subheader("📊 KPIs")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Saldo Total", f"${snap['saldo_total'].sum():,.0f}")
    c2.metric("Vencido", f"${snap['saldo_vencido'].sum():,.0f}")
    c3.metric("Sobregiro", f"${snap['sobregiro'].sum():,.0f}")
    c4.metric("Incumplimiento", f"${snap['incumplimiento'].sum():,.0f}")

    # ─────────────────────────────────────────
    # HISTORIAL
    # ─────────────────────────────────────────
    st.subheader("📈 Historial")

    hist = df.groupby("fecha").agg({
        "saldo_total": "sum",
        "sobregiro": "sum",
        "incumplimiento": "sum"
    }).reset_index()

    st.line_chart(hist.set_index("fecha"))

    # ─────────────────────────────────────────
    # DETALLE
    # ─────────────────────────────────────────
    st.subheader("📦 Detalle por Destinatario")

    st.dataframe(
        snap[[
            "destinatario_mercancia",
            "saldo_vencido",
            "saldo_por_vencer",
            "limite_credito",
            "sobregiro",
            "incumplimiento",
            "pct_uso",
            "estatus"
        ]],
        use_container_width=True
    )

    # ─────────────────────────────────────────
    # ALERTAS
    # ─────────────────────────────────────────
    riesgo = snap[snap["estatus"] == "🔴 Riesgo"]

    if not riesgo.empty:
        st.error(f"⚠️ {len(riesgo)} destinatarios en riesgo")
    else:
        st.success("✅ Cliente sano")