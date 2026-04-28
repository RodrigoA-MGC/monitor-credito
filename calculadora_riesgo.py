import pandas as pd
import streamlit as st
from sqlalchemy import text


def render_calculadora(get_engine):

    st.subheader("🔍 Buscador de Clientes")

    engine = get_engine()

    # ─────────────────────────────────────────
    # CARGAR CLIENTES (solo catálogo ligero)
    # ─────────────────────────────────────────
    query_clientes = text("""
        SELECT DISTINCT cliente, nombre
        FROM historico_monitor
        ORDER BY cliente
    """)

    with engine.connect() as con:
        df_actual = pd.read_sql(query_clientes, con)

    # ─────────────────────────────────────────
    # INPUT DE BÚSQUEDA
    # ─────────────────────────────────────────
    busqueda = st.text_input("Escribe cliente o nombre:")

    if busqueda:
        opciones_filtradas = df_actual[
            df_actual["cliente"].str.contains(busqueda, case=False, na=False) |
            df_actual["nombre"].str.contains(busqueda, case=False, na=False)
        ][["cliente", "nombre"]].drop_duplicates()
    else:
        opciones_filtradas = df_actual[["cliente", "nombre"]].drop_duplicates().head(20)

    # ─────────────────────────────────────────
    # SELECTBOX
    # ─────────────────────────────────────────
    if opciones_filtradas.empty:
        st.warning("No hay coincidencias")
        return

    seleccion = st.selectbox(
        "Selecciona un cliente:",
        options=opciones_filtradas["cliente"],
        format_func=lambda x: f"{x} - {opciones_filtradas[opciones_filtradas['cliente']==x]['nombre'].values[0]}"
    )

    # ─────────────────────────────────────────
    # CONSULTA PRINCIPAL
    # ─────────────────────────────────────────
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
            df = pd.read_sql(query, con, params={"cliente": seleccion})

    if df.empty:
        st.warning("No hay datos para ese cliente")
        return

    # ─────────────────────────────────────────
    # CÁLCULOS
    # ─────────────────────────────────────────
    df["saldo_total"] = df["saldo_vencido"] + df["saldo_por_vencer"]

    activos = df["anticipos"] + df["depositos_sap"]

    df["sobregiro"] = (df["saldo_total"] - df["limite_credito"]).clip(lower=0)
    df["incumplimiento"] = (df["saldo_vencido"] - (df["anticipos"] + df["depositos_sap"])).clip(lower=0)
    
    df["pct_uso"] = (
        df["saldo_total"] / df["limite_credito"].replace(0, 1)
    ) * 100

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

    # ─────────────────────────────────────────
    # DETALLE
    # ─────────────────────────────────────────
    st.subheader("📦 Detalle")

    st.dataframe(
        snap[
            [
                "cliente",
                "destinatario_mercancia",
                "condiciones_pago",
                "saldo_vencido",
                "saldo_por_vencer",
                "limite_credito",
                "sobregiro",
                "incumplimiento",
                "pct_uso",
            ]
        ],
        use_container_width=True,
        hide_index=True
    )