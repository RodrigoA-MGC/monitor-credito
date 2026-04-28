import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text, pool

# ─────────────────────────────────────────────
# 1. CONFIGURACIÓN Y ESTILO
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de Crédito Pro",
    page_icon="📊",
    layout="wide",
)

# Inyectar un poco de CSS para mejorar la estética de las métricas
st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.8rem; color: #1f77b4; }
    [data-testid="stMetricDelta"] { font-size: 1rem; }
    </style>
    """, unsafe_allow_html=True)

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
    """Trae solo la foto más reciente de cada cliente/sucursal"""
    query = """
    SELECT DISTINCT ON (cliente, destinatario_mercancia)
        cliente, destinatario_mercancia, nombre, condiciones_pago,
        fecha, saldo_vencido, saldo_por_vencer, anticipos,
        depositos_sap, limite_credito
    FROM historico_monitor
    ORDER BY cliente, destinatario_mercancia, fecha DESC
    """
    engine = get_engine()
    with engine.connect() as con:
        return pd.read_sql(text(query), con)

@st.cache_data(ttl=300)
def cargar_historico_cliente(cliente):
    """Trae la evolución completa de un cliente específico"""
    query = text("""
        SELECT cliente, destinatario_mercancia, nombre, saldo_vencido, 
               saldo_por_vencer, anticipos, depositos_sap, limite_credito, fecha
        FROM historico_monitor
        WHERE cliente = :c
        ORDER BY fecha ASC
    """)
    engine = get_engine()
    with engine.connect() as con:
        return pd.read_sql(query, con, params={"c": cliente})

def calcular_indicadores(df):
    df = df.copy()
    df["Saldo_Total"] = df["saldo_vencido"] + df["saldo_por_vencer"]
    pagos = df["anticipos"] + df["depositos_sap"]
    df["Sobregiro"] = df["Saldo_Total"] - pagos
    df["Incumplimiento"] = df["saldo_vencido"] - pagos
    # Calidad de datos: evitar negativos por errores de captura
    df["Sobregiro"] = df["Sobregiro"].clip(lower=0)
    return df

# ─────────────────────────────────────────────
# 3. LÓGICA DE CARGA
# ─────────────────────────────────────────────
with st.spinner("Sincronizando con base de datos..."):
    df_actual_raw = cargar_snapshot_actual()
    df_actual = calcular_indicadores(df_actual_raw)

# ─────────────────────────────────────────────
# 4. INTERFAZ: DASHBOARD EJECUTIVO (HOY)
# ─────────────────────────────────────────────
st.title("📊 Monitor de Control de Crédito")
fecha_corte = df_actual["fecha"].max()
st.caption(f"Última actualización de datos: {fecha_corte}")

# KPIs Superiores
m1, m2, m3, m4 = st.columns(4)
total_vencido = df_actual["saldo_vencido"].sum()
total_sobregiro = df_actual["Sobregiro"].sum()
utilizacion_media = (df_actual["Saldo_Total"].sum() / df_actual["limite_credito"].replace(0, 1).sum()) * 100

m1.metric("Cartera Total", f"${df_actual['Saldo_Total'].sum():,.0f}")
m2.metric("Saldo Vencido", f"${total_vencido:,.0f}", delta="Exposición", delta_color="inverse")
m3.metric("Sobregiro Real", f"${total_sobregiro:,.0f}", delta="Crítico", delta_color="off")
m4.metric("% Utilización", f"{utilizacion_media:.1f}%")

st.divider()

# PESTAÑAS
tab1, tab2 = st.tabs(["📋 Resumen de Clientes (Hoy)", "📈 Detalle Histórico"])

with tab1:
    st.subheader("Estado Actual de Clientes")
    
    # Agrupamos por cliente (consolidando sus sucursales/destinatarios)
    df_cli_resumen = df_actual.groupby("cliente").agg({
        "nombre": "first",
        "saldo_vencido": "sum",
        "Saldo_Total": "sum",
        "Sobregiro": "sum",
        "limite_credito": "sum"
    }).reset_index()

    st.dataframe(
        df_cli_resumen,
        use_container_width=True,
        hide_index=True,
        column_config={
            "cliente": "ID",
            "nombre": "Razón Social",
            "saldo_vencido": st.column_config.NumberColumn("Vencido", format="$%.2f"),
            "Saldo_Total": st.column_config.NumberColumn("Cartera Total", format="$%.2f"),
            "Sobregiro": st.column_config.ProgressColumn("Nivel Sobregiro", format="$%.2f", 
                                                        min_value=0, 
                                                        max_value=float(df_cli_resumen["Sobregiro"].max() if not df_cli_resumen.empty else 1)),
            "limite_credito": st.column_config.NumberColumn("Límite", format="$%.2f")
        }
    )

with tab2:
    st.subheader("Análisis de Tendencias")
    cliente_sel = st.selectbox(
        "Busque un cliente para ver su evolución:",
        options=df_cli_resumen["cliente"],
        format_func=lambda x: f"{x} - {df_cli_resumen[df_cli_resumen['cliente']==x]['nombre'].values[0]}"
    )

    if cliente_sel:
        hist = cargar_historico_cliente(cliente_sel)
        hist = calcular_indicadores(hist)

        # Gráfico de Tendencia
        st.write(f"### Evolución de Sobregiro e Incumplimiento")
        # Preparamos datos para el gráfico
        chart_data = hist.groupby("fecha")[["Sobregiro", "saldo_vencido"]].sum()
        st.line_chart(chart_data)

        col_left, col_right = st.columns([2, 1])
        
        with col_left:
            st.write("**Registros históricos detallados:**")
            st.dataframe(
                hist[["fecha", "destinatario_mercancia", "saldo_vencido", "Saldo_Total", "Sobregiro"]].sort_values("fecha", ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={"fecha": "Fecha", "saldo_vencido": "$ Vencido", "Sobregiro": "$ Sobregiro"}
            )
        
        with col_right:
            st.write("**Acciones:**")
            # Descarga de Excel
            def to_excel(df):
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Historico')
                return output.getvalue()

            st.download_button(
                label="📥 Descargar Reporte Histórico",
                data=to_excel(hist),
                file_name=f"Reporte_{cliente_sel}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            
            st.info(f"El cliente {cliente_sel} ha tenido {len(hist['fecha'].unique())} actualizaciones en el periodo consultado.")

# ─────────────────────────────────────────────
# 5. FOOTER
# ─────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.write("🏷️ **Filtros Globales**")
filtro_pago = st.sidebar.multiselect("Condiciones de Pago", options=df_actual["condiciones_pago"].unique())

if filtro_pago:
    st.warning("⚠️ Nota: Los filtros del sidebar no están conectados a los KPIs superiores en esta versión básica.")