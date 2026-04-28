import pandas as pd
import streamlit as st
from sqlalchemy import text

def render_calculadora(get_engine):

    st.subheader("🧮 Calculadora de Riesgo")

    cliente = st.text_input("🔍 Cliente", key="cliente_calc")

    if not cliente:
        st.info("Ingresa un cliente")
        return

    engine = get_engine()

    query = text("""
        SELECT
            cliente,
            destinatario_mercancia,
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

    with st.spinner("Cargando datos..."):
        with engine.connect() as con:
            df = pd.read_sql(query, con, params={"cliente": cliente})

    if df.empty:
        st.warning("No hay datos para ese cliente")
        return

    df["saldo_total"] = df["saldo_vencido"] + df["saldo_por_vencer"]

    activos = df["anticipos"] + df["depositos_sap"]

    df["sobregiro"] = (df["saldo_total"] - activos).clip(lower=0)
    df["incumplimiento"] = (df["saldo_vencido"] - activos).clip(lower=0)

    df["pct_uso"] = (
        df["saldo_total"] / df["limite_credito"].replace(0, 1)
    ) * 100

    ultima_fecha = df["fecha"].max()
    snap = df[df["fecha"] == ultima_fecha]

    st.subheader("📊 KPIs")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Saldo Total", f"${snap['saldo_total'].sum():,.0f}")
    c2.metric("Vencido", f"${snap['saldo_vencido'].sum():,.0f}")
    c3.metric("Sobregiro", f"${snap['sobregiro'].sum():,.0f}")
    c4.metric("Incumplimiento", f"${snap['incumplimiento'].sum():,.0f}")

    st.subheader("📈 Historial")

    hist = df.groupby("fecha").agg({
        "saldo_total": "sum",
        "sobregiro": "sum",
        "incumplimiento": "sum"
    })

    st.line_chart(hist)

    st.subheader("📦 Detalle")

    st.dataframe(
        snap[[
            "destinatario_mercancia",
            "saldo_vencido",
            "saldo_por_vencer",
            "limite_credito",
            "sobregiro",
            "incumplimiento",
            "pct_uso"
        ]],
        use_container_width=True
    )