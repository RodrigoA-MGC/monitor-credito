import pandas as pd
import streamlit as st
from sqlalchemy import text


def render_calculadora(get_engine):

    st.subheader("🧮 Calculadora de Riesgo")

    engine = get_engine()

    # ─────────────────────────────────────────
    # CARGAR CLIENTES (CATÁLOGO)
    # ─────────────────────────────────────────
    query_clientes = text("""
        SELECT DISTINCT cliente, nombre
        FROM historico_monitor
        ORDER BY cliente
    """)

    with engine.connect() as con:
        df_clientes = pd.read_sql(query_clientes, con)

    # ─────────────────────────────────────────
    # BUSCADOR
    # ─────────────────────────────────────────
    busqueda = st.text_input("🔍 Escribe cliente o nombre:")

    if busqueda:
        opciones = df_clientes[
            df_clientes["cliente"].str.contains(busqueda, case=False, na=False) |
            df_clientes["nombre"].str.contains(busqueda, case=False, na=False)
        ]
    else:
        opciones = df_clientes.head(20)

    if opciones.empty:
        st.warning("No hay coincidencias")
        return

    seleccion = st.selectbox(
        "Selecciona un cliente:",
        options=opciones["cliente"],
        format_func=lambda x: f"{x} - {opciones[opciones['cliente']==x]['nombre'].values[0]}"
    )

    # ─────────────────────────────────────────
    # HISTORICO MONITOR
    # ─────────────────────────────────────────
    query_hist = text("""
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
            df = pd.read_sql(query_hist, con, params={"cliente": seleccion})

    if df.empty:
        st.warning("No hay datos para este cliente")
        return

    # ─────────────────────────────────────────
    # CÁLCULOS
    # ─────────────────────────────────────────
    df["saldo_total"] = df["saldo_vencido"] + df["saldo_por_vencer"]

    activos = df["anticipos"] + df["depositos_sap"]

    df["sobregiro"] = (df["saldo_total"] - activos).clip(lower=0)
    df["incumplimiento"] = (df["saldo_vencido"] - activos).clip(lower=0)

    df["pct_uso"] = (
        df["saldo_total"] / df["limite_credito"].replace(0, 1)
    ) * 100

    # SNAPSHOT (último día)
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

    hist = (
        df.groupby("fecha")
        .agg(
            saldo_total=("saldo_total", "sum"),
            sobregiro=("sobregiro", "sum"),
            incumplimiento=("incumplimiento", "sum")
        )
        .sort_index()
    )

    st.line_chart(hist)

    # ─────────────────────────────────────────
    # FACTURAS RESUMEN (POR CENTRAL)
    # ─────────────────────────────────────────
    query_fact = text("""
        SELECT
            cliente,
            central,
            condiciones_pago,
            promedio_uso_dias,
            facturas_vencidas,
            monto_vencido
        FROM facturas
        WHERE cliente = :cliente
    """)

    try:
        with engine.connect() as con:
            df_fact = pd.read_sql(query_fact, con, params={"cliente": seleccion})
    except Exception:
        df_fact = pd.DataFrame()

    if not df_fact.empty:

        st.subheader("🏢 Resumen por Central")

        resumen = df_fact.groupby("central").agg({
            "monto_vencido": "sum",
            "facturas_vencidas": "sum",
            "promedio_uso_dias": "mean"
        }).reset_index()

        st.dataframe(resumen, use_container_width=True)

        # KPIs adicionales
        c5, c6, c7 = st.columns(3)

        c5.metric("Monto Vencido Total", f"${df_fact['monto_vencido'].sum():,.0f}")
        c6.metric("Facturas Vencidas", int(df_fact["facturas_vencidas"].sum()))
        c7.metric("Promedio Uso Días", f"{df_fact['promedio_uso_dias'].mean():.1f}")

    else:
        st.info("No hay datos de facturación resumen")

    # ─────────────────────────────────────────
    # DETALLE
    # ─────────────────────────────────────────
    st.subheader("📦 Detalle")

    st.dataframe(
        snap[
            [
                "destinatario_mercancia",
                "saldo_vencido",
                "saldo_por_vencer",
                "limite_credito",
                "sobregiro",
                "incumplimiento",
                "pct_uso",
            ]
        ],
        use_container_width=True
    )