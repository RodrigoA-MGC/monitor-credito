import io
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text, pool
from calculadora_riesgo import render_calculadora

# ─────────────────────────────────────────────
# 1. CONFIGURACIÓN Y ESTILO
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de Crédito Pro",
    page_icon="📊",
    layout="wide",
)
CONDICIONES_CREDITO = {"CP008", "CP15", "CP20", "CP25", "CP30", "CP45"}
CONDICION_EXCEPCION = "CP45"
COND_ANTICIPADO = "CP00"
COND_RECLAMACION = "MG05"
COND_INACTIVO = {"MG01", "MG02", "MG03", "MG04", "MG06", "0001"}
COND_CRA = "CRA"
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
    query = """
    WITH ultimo_dia AS (
        -- Paso 1: Identificar cuál es el último día con datos en la tabla
        SELECT MAX(fecha::date) as max_fecha FROM historico_monitor
    )
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
    WHERE 
        -- Paso 2: Solo traer registros del último día detectado
        fecha::date = (SELECT max_fecha FROM ultimo_dia)
        -- Paso 3: Regla de negocio - Solo registros antes de las 10:00 AM
        AND hora::time < '10:00:00'
    ORDER BY 
        cliente, 
        destinatario_mercancia, 
        hora ASC -- Trae el primer registro de la mañana
    """
    engine = get_engine()
    with engine.connect() as con:
        df = pd.read_sql(text(query), con)
    return df

@st.cache_data(ttl=300)
def cargar_historico_cliente(cliente):
    query = text("""
        SELECT cliente, destinatario_mercancia, nombre, 
               condiciones_pago,                        
               saldo_vencido, saldo_por_vencer, 
               anticipos, depositos_sap, limite_credito, fecha
        FROM historico_monitor
        WHERE cliente = :c
        ORDER BY fecha ASC
    """)
    engine = get_engine()
    with engine.connect() as con:
        return pd.read_sql(query, con, params={"c": cliente})

    
def calcular_indicadores(df):
    df = df.copy()
    
    # Calculamos la Deuda Total (Lo que el cliente tiene en su cuenta)
    df["Deuda_Total"] = (df["saldo_vencido"] + df["saldo_por_vencer"]) - (df["anticipos"] + df["depositos_sap"])
    
    # Sobregiro: Solo lo que excede el límite de crédito
    # Si el resultado es negativo, .clip(lower=0) lo vuelve 0.
    df["Sobregiro"] = (df["Deuda_Total"] - df["limite_credito"]).clip(lower=0)
    
    # Incumplimiento: Saldo vencido que no está cubierto por pagos
    df["Incumplimiento"] = (df["saldo_vencido"] - (df["anticipos"] + df["depositos_sap"])).clip(lower=0)
    
    
    # 3. % Uso de Crédito: (Vencido + Por Vencer) - Límite
    df["Uso_Credito_Monto"] = (df["saldo_vencido"] + df["saldo_por_vencer"]) - df["limite_credito"]
    
    df["Saldo_Total"] = (df["saldo_vencido"] + df["saldo_por_vencer"])
     
    #3. Utilización Individual (Opcional, para ver por fila en la tabla)
    # Evitamos división por cero si el límite es 0
    df["%_Utilización_Ind"] = (df["Deuda_Total"] / df["limite_credito"].replace(0, 1)) * 100
    return df

def clasificar_estatus(row):
    cond = str(row["condiciones_pago"]).strip()
    sob = row["Sobregiro"]
    inc = row["Incumplimiento"]
    
    # --- Otros Estatus (No operativos) ---
    if cond == "MG05": return "🟣 Reclamación"
    if cond == "CRA": return "🔵 CRA"
    if cond in ["MG01", "MG02", "MG03", "MG04", "MG06", "0001"]: return "⚫ Inactivo"
    
    # --- Excepciones ---
    if cond == "CP45": return "🟠 Activo (Excepción)"
    
    # --- Operativos (Crédito y Anticipado) ---
    # Crédito: CP008, CP15, CP20, CP25, CP30 | Anticipado: CP00
    if cond in ["CP008", "CP15", "CP20", "CP25", "CP30", "CP00"]:
        if sob > 0.01 or inc > 0.01:
            return "🔴 Suspendido"
        return "🟢 Activo"
    
    return "⚪ Stand By / Otro"
    
def mostrar_ficha_cliente(cliente_id, snapshot, historico):
    # Filtrar datos del cliente
    # Usamos .lower() o nombres exactos según tu DB
    snap_cli = snapshot[snapshot["cliente"] == cliente_id].copy()
    hist_cli = historico[historico["cliente"] == cliente_id].copy()
    
    if snap_cli.empty:
        st.warning("No se encontraron datos para este cliente.")
        return

    nombre = snap_cli.iloc[0].get("nombre", "—")
    dests = sorted(snap_cli["destinatario_mercancia"].unique())

    st.markdown(f"## {nombre} <br><small>ID Central: `{cliente_id}`</small>", unsafe_allow_html=True)

    # ── Tabs ────────────────────────────────
    tabs = st.tabs(["📊 Consolidado"] + [f"📦 {d}" for d in dests])

    # ── Tab 0: Consolidado (Suma de todos sus destinatarios) ──
    with tabs[0]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Destinatarios", len(dests))
        c2.metric("Vencido Total", f"${snap_cli['saldo_vencido'].sum():,.2f}")
        c3.metric("Sobregiro Total", f"${snap_cli['Sobregiro'].sum():,.2f}")
        c4.metric("Incumplimiento", f"${snap_cli['Incumplimiento'].sum():,.2f}")

        st.write("### Resumen por Sucursal")
        st.dataframe(
            snap_cli[[
                "destinatario_mercancia", "condiciones_pago", "Estatus",
                "saldo_vencido", "saldo_por_vencer", "limite_credito", "Sobregiro"
            ]].rename(columns={
                "destinatario_mercancia": "Destinatario",
                "condiciones_pago": "Condición",
                "Estatus": "Estatus"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "saldo_vencido": st.column_config.NumberColumn(format="$%.2f"),
                "saldo_por_vencer": st.column_config.NumberColumn(format="$%.2f"),
                "limite_credito": st.column_config.NumberColumn(format="$%.2f"),
                "Sobregiro": st.column_config.NumberColumn(format="$%.2f")
            }
        )

    # ── Tabs 1 a N: Detalle Individual ──
    for i, dest in enumerate(dests):
        with tabs[i + 1]:
            snap_d = snap_cli[snap_cli["destinatario_mercancia"] == dest].iloc[0]
            hist_d = hist_cli[hist_cli["destinatario_mercancia"] == dest].sort_values("fecha", ascending=False)
            
            # Fila 1 de Métricas
            m1, m2, m3, m4 = st.columns(4)
            m1.write(f"**Estatus:** {snap_d['Estatus']}")
            m2.write(f"**Condición:** {snap_d['condiciones_pago']}")
            m3.metric("Vencido", f"${snap_d['saldo_vencido']:,.2f}")
            m4.metric("Sobregiro", f"${snap_d['Sobregiro']:,.2f}")

            # Gráfico de tendencia por destinatario
            st.line_chart(hist_d.set_index("fecha")[["Sobregiro", "saldo_vencido"]])

            # Historial detallado
            with st.expander("Ver historial completo de este destinatario"):
                st.dataframe(
                    hist_d[["fecha", "saldo_vencido", "saldo_por_vencer", "Sobregiro", "Incumplimiento", "condiciones_pago"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={"fecha": st.column_config.DateColumn("Fecha Corte")}
                )

# Para aplicarlo, solo añade esta línea después de calcular indicadores:
# df["Estatus"] = df.apply(clasificar_estatus, axis=1)
# ─────────────────────────────────────────────
# 3. LÓGICA DE CARGA
# ─────────────────────────────────────────────
with st.spinner("Sincronizando con base de datos..."):
    df_actual_raw = cargar_snapshot_actual()
    df_actual = calcular_indicadores(df_actual_raw)
    df_actual["Estatus"] = df_actual.apply(clasificar_estatus, axis=1)  # ← agregar
# ─────────────────────────────────────────────
# 4. INTERFAZ: DASHBOARD EJECUTIVO (HOY)
# ─────────────────────────────────────────────
st.title("📊 Monitor de Control de Crédito")
fecha_corte = df_actual["fecha"].max()
st.caption(f"Última actualización de datos: {fecha_corte}")
# 1. Definimos el grupo de condiciones que entran en el cálculo de utilización
condiciones_credito = ['CP008', 'CP15', 'CP20', 'CP25', 'CP30']

# 2. Creamos un DataFrame filtrado solo con esos clientes
df_solo_credito = df_actual[df_actual['condiciones_pago'].isin(condiciones_credito)]

# 3. Calculamos totales sobre ese grupo específico
total_deuda_bruta_cred = df_solo_credito["Deuda_Total"].sum()
total_garantias_cred = df_solo_credito["limite_credito"].sum()

# 4. Cálculo del % Global (Garantías vs Deuda)
if total_garantias_cred > 0:
    utilizacion_global = (total_deuda_bruta_cred / total_garantias_cred) * 100
else:
    utilizacion_global = 0


# KPIs Superiores
m1, m2, m3, m4 = st.columns(4)
total_vencido = df_actual["saldo_vencido"].sum()
total_sobregiro = df_actual["Sobregiro"].sum()

utilizacion_media = (df_actual["Saldo_Total"].sum() / df_actual["limite_credito"].replace(0, 1).sum()) * 100

m1.metric("Saldo Total", f"${df_actual['Saldo_Total'].sum():,.0f}",help="El monto total de Saldo Vencido y por Vencer")
m2.metric("Saldo Vencido", f"${total_vencido:,.0f}", delta="Exposición", delta_color="inverse",help="El monto total del saldo vencido")
m3.metric("Sobregiro Real", f"${total_sobregiro:,.0f}", delta="Crítico", delta_color="off",help="Suma de los montos que exceden el límite de crédito autorizado por cliente.")
m4.metric(
    label="% Utilización (Crédito)",
    value=f"{utilizacion_global:.1f}%",
    help="Suma de saldos vs Límites solo para condiciones CP08 a CP30."
)

st.divider()

# PESTAÑAS
tab1, tab2, tab3 = st.tabs(["📋 Resumen de Clientes (Hoy)", "📈 Detalle Histórico", "🧮 Calculadora de Riesgo"])

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
            "Saldo_Total": st.column_config.NumberColumn("Saldo Total", format="$%.2f"),
            #"Sobregiro": st.column_config.ProgressColumn("Nivel Sobregiro", format="$%.2f", 
            #                                            min_value=0, 
            #                                            max_value=float(df_cli_resumen["Sobregiro"].max() if not df_cli_resumen.empty else 1)),
            "limite_credito": st.column_config.NumberColumn("Límite", format="$%.2f")
        }
    )

with tab2:
    st.subheader("Buscador de Clientes")
    opciones = df_actual[["cliente", "nombre"]].drop_duplicates()
    seleccion = st.selectbox(
        "Seleccione un cliente para ver su ficha técnica:",
        options=opciones["cliente"],
        format_func=lambda x: f"{x} - {opciones[opciones['cliente']==x]['nombre'].values[0]}"
    )
    
    if seleccion:
        hist = cargar_historico_cliente(seleccion)
        hist = calcular_indicadores(hist)
        hist["Estatus"] = hist.apply(clasificar_estatus, axis=1)  # ← agregar
        mostrar_ficha_cliente(seleccion, df_actual, hist)
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
                file_name=f"Reporte_{seleccion}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            
            st.info(f"El cliente {seleccion} ha tenido {len(hist['fecha'].unique())} actualizaciones en el periodo consultado.")
with tab3:
    render_calculadora(get_engine)
# ─────────────────────────────────────────────
# 5. FOOTER
# ─────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.write("🏷️ **Filtros Globales**")
filtro_pago = st.sidebar.multiselect("Condiciones de Pago", options=df_actual["condiciones_pago"].unique())

if filtro_pago:
    st.warning("⚠️ Nota: Los filtros del sidebar no están conectados a los KPIs superiores en esta versión básica.")