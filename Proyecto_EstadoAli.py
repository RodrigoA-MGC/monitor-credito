import streamlit as st
import pandas as pd
import io
import psycopg2

conn = psycopg2.connect(
    host=st.secrets["DB_HOST"],
    user=st.secrets["DB_USER"],
    password=st.secrets["DB_PASSWORD"],
    database=st.secretrs["DB_NAME"],
    port=st.secrets["DB_PORT"]
)
# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de credito",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  CONSTANTES DE NEGOCIO
# ─────────────────────────────────────────────
CONDICIONES_CREDITO  = {"CP008", "CP15", "CP20", "CP25", "CP30", "CP45"}
CONDICION_EXCEPCION  = {"CP45"}       # Opera aunque tenga sobregiro/incumplimiento
CONDICION_ANTICIPADO = {"CP00"}
CONDICIONES_INACTIVO = {"MG01", "MG02", "MG03", "MG04", "MG06", "0001"}
CONDICION_CRA        = {"CRA"}
CONDICION_RECLAMACION = {"MG05"}

# Prioridad para determinar el "peor estatus" de un cliente central
# (cuando tiene varios destinatarios con distintos estatus)
PRIORIDAD_ESTATUS = {
    "🔴 Suspendido":         0,
    "🟣 Reclamación":        1,
    "🟠 Activo (Excepción)": 2,
    "🔵 CRA":                3,
    "⚫ Inactivo":           4,
    "🟢 Activo":             5,
    "⚪ Sin clasificar":     6,
}

# ─────────────────────────────────────────────
#  CONECTORES
# ─────────────────────────────────────────────

def cargar_desde_csv(archivo) -> pd.DataFrame:
    return pd.read_csv(archivo)


def cargar_desde_excel(archivo, hoja) -> pd.DataFrame:
    return pd.read_excel(archivo, sheet_name=hoja)


def cargar_desde_access(ruta: str, query: str) -> pd.DataFrame:
    """
    Lee Access usando el cursor directamente.
    - Evita el UserWarning de pandas con conexiones DBAPI2.
    - Los nombres de columna se toman tal cual están en Access;
      normalizar_columnas() se encarga de mapearlos al estándar.
    """
    try:
        import pyodbc
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={ruta};"
        con = pyodbc.connect(conn_str)
        try:
            cursor = con.cursor()
            cursor.execute(query)
            columnas = [desc[0] for desc in cursor.description]
            filas    = cursor.fetchall()
            return pd.DataFrame.from_records(filas, columns=columnas)
        finally:
            con.close()
    except ImportError:
        raise RuntimeError("pyodbc no está instalado. Ejecuta: pip install pyodbc")
    except Exception as e:
        raise RuntimeError(f"Error al conectar con Access: {e}")


def cargar_desde_postgresql(host, puerto, bd, usuario, password, query) -> pd.DataFrame:
    try:
        from sqlalchemy import create_engine, text
        url = f"postgresql+psycopg2://{usuario}:{password}@{host}:{puerto}/{bd}"
        engine = create_engine(url)
        with engine.connect() as con:
            return pd.read_sql(text(query), con)
    except ImportError:
        raise RuntimeError("Instala: pip install psycopg2-binary sqlalchemy")
    except Exception as e:
        raise RuntimeError(f"Error PostgreSQL: {e}")


# ─────────────────────────────────────────────
#  TRANSFORMACIÓN — PIPELINE DE NEGOCIO
# ─────────────────────────────────────────────

COLUMNAS_REQUERIDAS = {
    "Cliente",
    "Destinatario mercancia",
    "Condiciones de pago",
    "Nombre 1",
    "fecha",
    "Saldo vencido",
    "Saldo por vencer",
    "Anticipos",
    "Depósitos SAP",
    "Límite de credito",
}

COLS_NUM = [
    "Saldo vencido", "Saldo por vencer",
    "Anticipos", "Depósitos SAP", "Límite de credito",
]


def validar_columnas(df: pd.DataFrame) -> list[str]:
    """Devuelve lista de columnas faltantes (lista vacía = OK)."""
    return [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia nombres de columna y tipos.
    Maneja variaciones de acentos que pueden venir de Access/Excel.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Mapa de nombres normalizados (sin acentos) → nombre estándar
    import re

    _norm = lambda s: re.sub(
        r'[^a-z0-9]+', '_',  # 🔥 convierte TODO a formato tipo postgres
        s.lower()
        .replace("á","a").replace("é","e").replace("í","i")
        .replace("ó","o").replace("ú","u").replace("ñ","n")
    ).strip('_')
    
    mapeo_normalizado = {
    "destinatario_mercancia": "Destinatario mercancia",
    "condiciones_pago": "Condiciones de pago",
    "nombre": "Nombre 1",
    "saldo_vencido": "Saldo vencido",
    "saldo_por_vencer": "Saldo por vencer",
    "anticipos": "Anticipos",
    "depositos_sap": "Depósitos SAP",
    "limite_credito": "Límite de credito",
    "fecha": "fecha",
    "cliente": "Cliente",
}

    rename = {}
    for col in df.columns:
        k = _norm(col)
        if k in mapeo_normalizado and col != mapeo_normalizado[k]:
            rename[col] = mapeo_normalizado[k]
    df = df.rename(columns=rename)

    # Tipos numéricos
    for c in COLS_NUM:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Texto limpio en columnas clave
    for c in ["Cliente", "Destinatario mercancia", "Condiciones de pago", "Nombre 1"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    return df


def calcular_snapshot_diario(df: pd.DataFrame) -> pd.DataFrame:
    """
    REGLA: de cada día tomar el PRIMER registro (carga de la mañana).
    Cuando hay múltiples cargas en el día, descartamos las posteriores.
    La clave de unicidad es (Cliente, Destinatario, día).
    """
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])

    # Separar la parte de la hora para ordenar intradía
    df["_dia"] = df["fecha"].dt.normalize()

    # Ordenar ascendente: el primer registro del día quedará arriba
    df = df.sort_values(["Cliente", "Destinatario mercancia", "fecha"], ascending=True)

    # Deduplicar: keep="first" toma la carga más temprana del día
    df = df.drop_duplicates(
        subset=["Cliente", "Destinatario mercancia", "_dia"],
        keep="first",
    )

    # Usar fecha sin hora (más limpia para visualizar)
    df["fecha"] = df["_dia"]
    df = df.drop(columns=["_dia"])

    return df.sort_values(["Cliente", "Destinatario mercancia", "fecha"])


def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica las fórmulas de negocio según el grupo de condición de pago.

    credito (CP008/CP15/CP20/CP25/CP30/CP45):
        Sobregiro    = (Vencido + Por vencer) - (Anticipos + Depósitos SAP)
        Incumplimiento = Vencido - (Anticipos + Depósitos SAP)
        Uso vs Límite = (Vencido + Por vencer) - Límite de credito

    Anticipado (CP00):
        Solo Incumplimiento = Vencido - (Anticipos + Depósitos SAP)

    Stand-by / inactivos: sin cálculo (quedan en 0)
    """
    df = df.copy()
    cond = df["Condiciones de pago"].astype(str).str.strip()
    activos = df["Anticipos"] + df["Depósitos SAP"]

    df["Sobregiro"]     = 0.0
    df["Incumplimiento"]= 0.0
    df["Uso_vs_Límite"] = 0.0

    mask_credito     = cond.isin(CONDICIONES_CREDITO)
    mask_anticipado  = cond.isin(CONDICION_ANTICIPADO)
    mask_con_formula = mask_credito | mask_anticipado

    # Sobregiro: solo credito
    df.loc[mask_credito, "Sobregiro"] = (
        (df.loc[mask_credito, "Saldo vencido"] + df.loc[mask_credito, "Saldo por vencer"])
        - activos[mask_credito]
    )

    # Incumplimiento: credito + anticipado
    df.loc[mask_con_formula, "Incumplimiento"] = (
        df.loc[mask_con_formula, "Saldo vencido"] - activos[mask_con_formula]
    )

    # Uso vs Límite: solo credito
    df.loc[mask_credito, "Uso_vs_Límite"] = (
        (df.loc[mask_credito, "Saldo vencido"] + df.loc[mask_credito, "Saldo por vencer"])
        - df.loc[mask_credito, "Límite de credito"]
    )

    return df


def calcular_estatus(df: pd.DataFrame) -> pd.DataFrame:
    """
    Determina el estatus operativo de cada destinatario.
    Se evalúa fila por fila (cada snapshot).
    """
    def _estatus(row):
        c = str(row["Condiciones de pago"]).strip()

        if c in CONDICIONES_INACTIVO:   return "⚫ Inactivo"
        if c in CONDICION_CRA:          return "🔵 CRA"
        if c in CONDICION_RECLAMACION:  return "🟣 Reclamación"

        # CP45: excepción operativa — opera aunque tenga incumplimiento
        if c in CONDICION_EXCEPCION:
            return "🟠 Activo (Excepción)"

        if c in CONDICIONES_CREDITO or c in CONDICION_ANTICIPADO:
            if row["Sobregiro"] > 0 or row["Incumplimiento"] > 0:
                return "🔴 Suspendido"
            return "🟢 Activo"

        return "⚪ Sin clasificar"

    df = df.copy()
    df["Estatus"] = df.apply(_estatus, axis=1)
    return df


def obtener_snapshot_actual(historico: pd.DataFrame) -> pd.DataFrame:
    """
    Extrae la 'foto actual' del historial:
    para cada (Cliente, Destinatario) toma su registro más reciente.

    Nota: no forzamos que todos compartan la misma fecha máxima global,
    por si algún destinatario dejó de aparecer en las últimas cargas.
    """
    return (
        historico
        .sort_values("fecha")
        .groupby(["Cliente", "Destinatario mercancia"], as_index=False)
        .last()
    )


def transformar(df_raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pipeline completo:
      1. Normalizar columnas y tipos
      2. Snapshot diario (primer registro del día por destinatario)
      3. Calcular indicadores financieros
      4. Calcular estatus operativo
      5. Separar snapshot actual vs. historial completo

    Retorna:
      snapshot_actual : una fila por (Cliente, Destinatario) con datos del día más reciente
      historico       : todo el historial limpio
    """
    df = normalizar_columnas(df_raw)
    df = calcular_snapshot_diario(df)
    df = calcular_indicadores(df)
    df = calcular_estatus(df)

    snapshot_actual = obtener_snapshot_actual(df)

    return snapshot_actual, df


# ─────────────────────────────────────────────
#  HELPERS UI
# ─────────────────────────────────────────────

def generar_excel_bytes(df: pd.DataFrame, sheet_name: str = "Datos") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def enriquecer_con_tipo_cliente(df: pd.DataFrame, df_tipo: pd.DataFrame | None) -> pd.DataFrame:
    """
    Hace LEFT JOIN del snapshot/historico con la tabla de Tipo de Cliente.
    Si df_tipo es None (no se cargó), simplemente agrega columna vacía.
    La unión es: Destinatario mercancia == CENTRAL
    """
    if df_tipo is None or df_tipo.empty:
        df = df.copy()
        df["Tipo de Cliente"] = "—"
        return df
    return df.merge(
        df_tipo[["Destinatario mercancia", "Tipo de Cliente"]],
        on="Destinatario mercancia",
        how="left",
    ).assign(**{"Tipo de Cliente": lambda d: d["Tipo de Cliente"].fillna("—")})


def resumen_por_cliente(snapshot: pd.DataFrame) -> pd.DataFrame:
    """
    Consolida el snapshot a nivel de Cliente central.
    El estatus del cliente es el 'peor' estatus entre todos sus destinatarios.
    """
    agg = snapshot.groupby("Cliente").agg(
        Nombre              =("Nombre 1",                "first"),
        Num_Destinatarios   =("Destinatario mercancia", "nunique"),
        Saldo_Vencido       =("Saldo vencido",           "sum"),
        Saldo_Por_Vencer    =("Saldo por vencer",        "sum"),
        Anticipos_Total     =("Anticipos",               "sum"),
        Depositos_Total     =("Depósitos SAP",           "sum"),
        Sobregiro_Total     =("Sobregiro",               "sum"),
        Incumplimiento_Total=("Incumplimiento",          "sum"),
        Fecha_Corte         =("fecha",                   "max"),
        Tipo_Cliente        =("Tipo de Cliente",          "first"),
    ).reset_index()

    # Estatus del cliente central = el de mayor prioridad (peor) entre destinatarios
    worst = (
        snapshot.groupby("Cliente")["Estatus"]
        .apply(lambda s: min(s, key=lambda x: PRIORIDAD_ESTATUS.get(x, 99)))
        .reset_index()
        .rename(columns={"Estatus": "Estatus_Cliente"})
    )
    agg = agg.merge(worst, on="Cliente")
    return agg


# ─────────────────────────────────────────────
#  VISTA: FICHA DE CLIENTE
# ─────────────────────────────────────────────

def mostrar_ficha_cliente(
    cliente: str,
    snapshot: pd.DataFrame,
    historico: pd.DataFrame,
):
    """
    Muestra la ficha completa de un cliente.
    - Tab "Consolidado": vista agregada de todos sus destinatarios.
    - Tab por destinatario: detalle individual + historial + descarga.
    """
    snap_cli = snapshot[snapshot["Cliente"] == cliente].copy()
    hist_cli = historico[historico["Cliente"] == cliente].copy()
    nombre   = snap_cli.iloc[0].get("Nombre 1", "—")
    dests    = sorted(snap_cli["Destinatario mercancia"].unique())

    st.markdown(f"### {nombre} &nbsp; `{cliente}`")

    # ── Tabs ────────────────────────────────
    tabs = st.tabs(["📊 Consolidado"] + [f"📦 {d}" for d in dests])

    # ── Tab Consolidado ──────────────────────
    with tabs[0]:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Destinatarios",    len(dests))
        c2.metric("Saldo Vencido",    f"${snap_cli['Saldo vencido'].sum():,.2f}")
        c3.metric("Por Vencer",       f"${snap_cli['Saldo por vencer'].sum():,.2f}")
        c4.metric("Sobregiro Total",  f"${snap_cli['Sobregiro'].sum():,.2f}")
        c5.metric("Incumplimiento",   f"${snap_cli['Incumplimiento'].sum():,.2f}")

        st.dataframe(
            snap_cli[[
                "Destinatario mercancia", "Tipo de Cliente", "Condiciones de pago",
                "Saldo vencido", "Saldo por vencer",
                "Anticipos", "Depósitos SAP", "Límite de credito",
                "Sobregiro", "Incumplimiento", "Uso_vs_Límite", "Estatus",
            ]].rename(columns={
                "Destinatario mercancia": "Destinatario",
                "Tipo de Cliente":        "Tipo",
                "Condiciones de pago":       "Condición",
                "Saldo vencido":             "Vencido",
                "Saldo por vencer":          "Por Vencer",
                "Límite de credito":         "Límite",
                "Uso_vs_Límite":             "Excedente Límite",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={c: st.column_config.NumberColumn(format="$%.2f")
                           for c in ["Vencido","Por Vencer","Anticipos",
                                     "Depósitos SAP","Límite","Sobregiro",
                                     "Incumplimiento","Excedente Límite"]},
        )

    # ── Tab por Destinatario ─────────────────
    for i, dest in enumerate(dests):
        with tabs[i + 1]:
            snap_d = snap_cli[snap_cli["Destinatario mercancia"] == dest].iloc[0]
            hist_d = hist_cli[hist_cli["Destinatario mercancia"] == dest].sort_values("fecha")
            cond   = snap_d["Condiciones de pago"]

            # Métricas
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Estatus",           snap_d["Estatus"])
            m2.metric("Condición de Pago", cond)
            m3.metric("Saldo Vencido",     f"${snap_d['Saldo vencido']:,.2f}")
            m4.metric("Por Vencer",        f"${snap_d['Saldo por vencer']:,.2f}")

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Sobregiro",         f"${snap_d['Sobregiro']:,.2f}")
            m6.metric("Incumplimiento",    f"${snap_d['Incumplimiento']:,.2f}")
            m7.metric("Límite de credito", f"${snap_d['Límite de credito']:,.2f}")
            m8.metric("Excedente Límite",  f"${snap_d['Uso_vs_Límite']:,.2f}")

            tipo_c = snap_d.get("Tipo de Cliente", "—") if "Tipo de Cliente" in snap_d.index else "—"
            st.caption(
                f"Última fecha de corte: **{snap_d['fecha'].strftime('%d/%m/%Y')}**"
                f"  ·  Tipo de Cliente: **{tipo_c}**"
            )

            # Historial completo
            with st.expander(f"📋 Historial de snapshots — {dest}", expanded=False):
                cols_hist = [
                    "fecha", "Saldo vencido", "Saldo por vencer",
                    "Anticipos", "Depósitos SAP",
                    "Sobregiro", "Incumplimiento", "Uso_vs_Límite", "Estatus",
                ]
                st.dataframe(
                    hist_d[cols_hist].sort_values("fecha", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "fecha": st.column_config.DateColumn("Fecha"),
                        **{c: st.column_config.NumberColumn(format="$%.2f")
                           for c in ["Saldo vencido","Saldo por vencer",
                                     "Anticipos","Depósitos SAP",
                                     "Sobregiro","Incumplimiento","Uso_vs_Límite"]},
                    },
                )

            # Descarga estado de cuenta individual
            edo = hist_d[[
                "fecha", "Condiciones de pago",
                "Saldo vencido", "Saldo por vencer",
                "Anticipos", "Depósitos SAP",
                "Sobregiro", "Incumplimiento", "Uso_vs_Límite", "Estatus",
            ]].copy()
            edo.columns = [
                "Fecha", "Condición",
                "Saldo Vencido", "Por Vencer",
                "Anticipos", "Depósitos SAP",
                "Sobregiro", "Incumplimiento", "Excedente Límite", "Estatus",
            ]

            st.download_button(
                label=f"📥 Descargar Estado de Cuenta — {dest}",
                data=generar_excel_bytes(edo, sheet_name="Estado de Cuenta"),
                file_name=f"EdoCuenta_{dest}.xlsx",
                mime="application/vnd.ms-excel",
                use_container_width=True,
                key=f"dl_dest_{dest}_{i}",
            )


# ─────────────────────────────────────────────
#  CONFIGURACION DE CARGA AUTOMATICA
#  Edita estos valores para tu entorno.
#  Si AUTOLOAD_RUTA esta vacio, la carga automatica se desactiva.
# ──────(Access)───────────────────────────────────────
# AUTOLOAD_RUTA  = r'C:\Users\rodrigo.vazquez\Desktop\Ali\Versiones Access\Credito361 Ali A3.accdb'
# AUTOLOAD_QUERY = "SELECT * FROM [Historico_Monitor]"
# ── PostgreSQL (fuente principal ahora) ──────
PG_HOST     = "localhost"        # lo que tienes en tu .env
PG_PUERTO   = "5432"
PG_BD       = "monitor_credito"
PG_USUARIO  = "postgres"
PG_PASSWORD = "Rayman123$"
PG_TABLA    = "historico_monitor"

AUTOLOAD_RUTA  = ""   # <-- vacío apaga el autoload de Access
AUTOLOAD_QUERY = f"SELECT * FROM {PG_TABLA}"
AUTOLOAD_TIPO_CLIENTE = r'C:\Users\rodrigo.vazquez\MGI Asistencia Integral\Analisis de Datos - Documentos\Tipo de Cliente\Tipo de Cliente.xlsx'

# ─────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────
for key in ("snapshot", "historico", "fuente_activa", "tipo_cliente_df"):
    if key not in st.session_state:
        st.session_state[key] = None

# ── Carga automatica al iniciar (solo si aun no hay datos (Access)) ──────────────
# if st.session_state.snapshot is None and AUTOLOAD_RUTA:
#    with st.spinner("Cargando datos desde Access..."):
#        try:
#            df_raw = cargar_desde_access(AUTOLOAD_RUTA, AUTOLOAD_QUERY)
#            faltantes = validar_columnas(df_raw)
#            if not faltantes:
#               st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
#                st.session_state.fuente_activa = "Access (auto)"
#            else:
#                st.warning(f"Autoload: columnas faltantes {faltantes}")
#        except Exception as e:
#            st.warning(f"Autoload Access no disponible: {e}")
if st.session_state.snapshot is None:
    with st.spinner("Cargando datos desde PostgreSQL..."):
        try:
            df_raw = cargar_desde_postgresql(
                PG_HOST, PG_PUERTO, PG_BD, PG_USUARIO, PG_PASSWORD, AUTOLOAD_QUERY
            )
            # 🔥 NORMALIZA PRIMERO
            df_raw = normalizar_columnas(df_raw)

            faltantes = validar_columnas(df_raw)
            if not faltantes:
                st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                st.session_state.fuente_activa = "PostgreSQL (auto)"
            else:
                st.warning(f"Autoload: columnas faltantes {faltantes}")
        except Exception as e:
            st.warning(f"Autoload PostgreSQL no disponible: {e}")
    # Carga automatica del complementario Tipo de Cliente
    if AUTOLOAD_TIPO_CLIENTE and st.session_state.tipo_cliente_df is None:
        try:
            df_tc = pd.read_excel(AUTOLOAD_TIPO_CLIENTE)
            df_tc.columns = [str(c).strip() for c in df_tc.columns]
            col_central = next((c for c in df_tc.columns if c.upper() == "CENTRAL"), None)
            col_tipo    = next((c for c in df_tc.columns if "tipo" in c.lower() and "cliente" in c.lower()), None)
            if col_central and col_tipo:
                df_tc = df_tc[[col_central, col_tipo]].rename(columns={
                    col_central: "Destinatario mercancia",
                    col_tipo:    "Tipo de Cliente",
                }).drop_duplicates(subset=["Destinatario mercancia"])
                st.session_state.tipo_cliente_df = df_tc
        except Exception:
            pass  # Silencioso: el complementario es opcional


# ─────────────────────────────────────────────
#  SIDEBAR — SELECTOR DE FUENTE
# ─────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Fuente de datos")

    fuente = st.radio(
        "¿De dónde cargar los datos?",
        ["📂 Archivo CSV", "📗 Excel (.xlsx)", "🗄️ Access local", "🐘 PostgreSQL"],
    )
    st.divider()

    # ── CSV ──────────────────────────────────
    if fuente == "📂 Archivo CSV":
        archivo = st.file_uploader("Sube tu archivo CSV", type=["csv"])
        if st.button("Cargar CSV", use_container_width=True):
            if not archivo:
                st.error("Sube un archivo primero.")
            else:
                try:
                    df_raw = cargar_desde_csv(archivo)
                    faltantes = validar_columnas(df_raw)
                    if faltantes:
                        st.error(f"Columnas faltantes: {faltantes}")
                    else:
                        st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                        st.session_state.fuente_activa = f"CSV: {archivo.name}"
                        st.success("✅ Listo")
                except Exception as e:
                    st.error(str(e))

    # ── EXCEL ─────────────────────────────────
    elif fuente == "📗 Excel (.xlsx)":
        archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx", "xls"])
        hoja = st.text_input("Nombre de la hoja:", value="Sheet1")
        if st.button("Cargar Excel", use_container_width=True):
            if not archivo:
                st.error("Sube un archivo primero.")
            else:
                try:
                    df_raw = cargar_desde_excel(archivo, hoja)
                    faltantes = validar_columnas(df_raw)
                    if faltantes:
                        st.error(f"Columnas faltantes: {faltantes}")
                    else:
                        st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                        st.session_state.fuente_activa = f"Excel: {archivo.name}/{hoja}"
                        st.success("✅ Listo")
                except Exception as e:
                    st.error(str(e))

    # ── ACCESS ────────────────────────────────
    elif fuente == "🗄️ Access local":
        ruta = st.text_input(
            "Ruta del archivo .accdb:",
            value=r'C:\Users\rodrigo.vazquez\Desktop\Ali\Versiones Access\Credito361 Ali A3.accdb',
        )
        # NO uses AS [Nombre con acento] en Access: el driver ODBC los
        # interpreta como parametros y lanza "Pocos parametros. Se esperaba N".
        # Solucion: SELECT * y Python normaliza los nombres automaticamente.
        query = st.text_area(
            "Query SQL:",
            value="SELECT * FROM [Historico_Monitor]",
            height=80,
        )
        st.caption(
            "Usa SELECT * o lista columnas SIN alias con acentos. "
            "Python mapea los nombres automaticamente."
        )
        if st.button("Cargar Access", use_container_width=True):
            try:
                df_raw = cargar_desde_access(ruta, query)
                faltantes = validar_columnas(df_raw)
                if faltantes:
                    st.error(f"Columnas faltantes: {faltantes}")
                else:
                    st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                    st.session_state.fuente_activa = "Access local"
                    st.success("✅ Listo")
            except Exception as e:
                st.error(str(e))

    # ── POSTGRESQL ────────────────────────────
    elif fuente == "🐘 PostgreSQL":
        pg_host   = st.text_input("Host:",          value="localhost")
        pg_puerto = st.text_input("Puerto:",         value="5432")
        pg_bd     = st.text_input("Base de datos:")
        pg_user   = st.text_input("Usuario:")
        pg_pass   = st.text_input("Contraseña:",     type="password")
        pg_query  = st.text_area("Query SQL:",       height=100,
                                  value="SELECT * FROM historico_monitor")
        if st.button("Conectar a PostgreSQL", use_container_width=True):
            try:
                df_raw = cargar_desde_postgresql(
                    pg_host, pg_puerto, pg_bd, pg_user, pg_pass, pg_query
                )
                faltantes = validar_columnas(df_raw)
                if faltantes:
                    st.error(f"Columnas faltantes: {faltantes}")
                else:
                    st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                    st.session_state.fuente_activa = f"PostgreSQL: {pg_bd}"
                    st.success("✅ Conectado")
            except Exception as e:
                st.error(str(e))

    # ── Boton de recarga manual (Access) ─────
    if st.button("🔄 Recargar desde PostgreSQL", use_container_width=True, key="btn_refresh_pg"):
        st.session_state.snapshot  = None
        st.session_state.historico = None
        st.session_state.fuente_activa = None
        st.rerun()
    if st.session_state.fuente_activa and "Access" in str(st.session_state.fuente_activa):
        if st.button("🔄 Recargar datos ahora", use_container_width=True, key="btn_refresh"):
            st.session_state.snapshot  = None
            st.session_state.historico = None
            st.session_state.fuente_activa = None
            st.rerun()

    # ── DATOS COMPLEMENTARIOS ────────────────
    st.divider()
    st.markdown("#### 📎 Datos complementarios")
    st.caption("Opcional: Excel con columnas CENTRAL y Tipo de Cliente")

    archivo_tc = st.file_uploader(
        "Tipo de Cliente (.xlsx)",
        type=["xlsx", "xls"],
        key="uploader_tipo_cliente",
    )
    hoja_tc = st.text_input("Hoja:", value="Sheet1", key="hoja_tipo_cliente")

    col_a, col_b = st.columns(2)
    if col_a.button("Cargar", use_container_width=True, key="btn_tipo_cliente"):
        if archivo_tc is None:
            st.error("Sube el archivo primero.")
        else:
            try:
                df_tc = pd.read_excel(archivo_tc, sheet_name=hoja_tc)
                df_tc.columns = [str(c).strip() for c in df_tc.columns]
                # Buscar columna CENTRAL (flexible)
                col_central = next(
                    (c for c in df_tc.columns if c.upper() == "CENTRAL"), None
                )
                col_tipo = next(
                    (c for c in df_tc.columns
                     if "tipo" in c.lower() and "cliente" in c.lower()), None
                )
                if not col_central or not col_tipo:
                    st.error(
                        f"No encontré columnas CENTRAL / Tipo de Cliente. "
                        f"Columnas detectadas: {list(df_tc.columns)}"
                    )
                else:
                    df_tc = df_tc[[col_central, col_tipo]].rename(columns={
                        col_central: "Destinatario mercancia",
                        col_tipo:    "Tipo de Cliente",
                    }).drop_duplicates(subset=["Destinatario mercancia"])
                    st.session_state.tipo_cliente_df = df_tc
                    st.success(f"✅ {len(df_tc)} registros cargados")
            except Exception as e:
                st.error(str(e))

    if col_b.button("Limpiar", use_container_width=True, key="btn_limpiar_tc"):
        st.session_state.tipo_cliente_df = None
        st.info("Datos complementarios eliminados.")

    if st.session_state.tipo_cliente_df is not None:
        n = len(st.session_state.tipo_cliente_df)
        st.caption(f"🟢 Tipo de Cliente: **{n}** registros activos")

    if st.session_state.fuente_activa:
        st.divider()
        st.caption(f"🟢 Fuente activa:\n{st.session_state.fuente_activa}")


# ─────────────────────────────────────────────
#  MAIN — PANTALLA PRINCIPAL
# ─────────────────────────────────────────────
st.title("📊 Monitor de credito")

if st.session_state.snapshot is None:
    st.info("👈 Selecciona una fuente de datos en la barra lateral para comenzar.")
    st.markdown("""
    ### Columnas requeridas en los datos
    | Columna | Descripción |
    |---|---|
    | `Cliente` | Identificador central del cliente |
    | `Destinatario mercancia` | Identificador del destinatario (puede ser igual al cliente) |
    | `Condiciones de pago` | CP008, CP15, CP20, CP25, CP30, CP45, CP00, MG01-MG06, CRA, etc. |
    | `Nombre 1` | Razón social o nombre comercial |
    | `fecha` | Fecha del snapshot |
    | `Saldo vencido` | Monto vencido sin cubrir |
    | `Saldo por vencer` | Monto dentro del plazo |
    | `Anticipos` | Activos a favor del cliente |
    | `Depósitos SAP` | Activos adicionales |
    | `Límite de credito` | Tope máximo autorizado |
    """)
    st.stop()

snapshot  = st.session_state.snapshot
historico = st.session_state.historico

# Enriquecer con Tipo de Cliente si se cargo el complementario
_df_tipo = st.session_state.tipo_cliente_df
snapshot  = enriquecer_con_tipo_cliente(snapshot,  _df_tipo)
historico = enriquecer_con_tipo_cliente(historico, _df_tipo)

# Consolidar a nivel Cliente central
df_clientes = resumen_por_cliente(snapshot)

# ── KPIs rápidos ──────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Clientes totales",    len(df_clientes))
k2.metric("🟢 Activos",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🟢 Activo"]))
k3.metric("🔴 Suspendidos",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🔴 Suspendido"]))
k4.metric("🟠 Excepción (CP45)",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🟠 Activo (Excepción)"]))
k5.metric("Sobregiro total",
          f"${df_clientes['Sobregiro_Total'].sum():,.2f}")
k6.metric("Incumplimiento total",
          f"${df_clientes['Incumplimiento_Total'].sum():,.2f}")

# ── Monitor principal ──────────────────────────
st.divider()
st.subheader("🔍 Monitor de Estatus Actual")
fecha_max = snapshot["fecha"].max()
st.caption(f"Datos al: **{fecha_max.strftime('%d/%m/%Y')}** · "
           f"{len(snapshot)} destinatarios activos")

busqueda = st.text_input(
    "Buscar por código o nombre:",
    placeholder="Ej: FF1095 o Empresa SA",
    key="buscador_principal",
)

mask = (
    df_clientes["Cliente"].str.contains(busqueda, case=False, na=False) |
    df_clientes["Nombre"].str.contains(busqueda, case=False, na=False)
) if busqueda else pd.Series([True] * len(df_clientes))

st.dataframe(
    df_clientes[mask][[
        "Cliente", "Nombre", "Tipo_Cliente", "Num_Destinatarios",
        "Saldo_Vencido", "Saldo_Por_Vencer",
        "Sobregiro_Total", "Incumplimiento_Total",
        "Estatus_Cliente", "Fecha_Corte",
    ]].rename(columns={
        "Num_Destinatarios":    "# Dest.",
        "Tipo_Cliente":        "Tipo de Cliente",
        "Saldo_Vencido":        "Vencido",
        "Saldo_Por_Vencer":     "Por Vencer",
        "Sobregiro_Total":      "Sobregiro",
        "Incumplimiento_Total": "Incumplimiento",
        "Estatus_Cliente":      "Estatus",
        "Fecha_Corte":          "Fecha Corte",
    }),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Vencido":       st.column_config.NumberColumn(format="$%.2f"),
        "Por Vencer":    st.column_config.NumberColumn(format="$%.2f"),
        "Sobregiro":     st.column_config.NumberColumn(format="$%.2f"),
        "Incumplimiento":st.column_config.NumberColumn(format="$%.2f"),
        "Fecha Corte":   st.column_config.DateColumn(),
    },
)

# ── Ficha individual ──────────────────────────
st.divider()
st.subheader("👤 Ficha de Cliente")

opciones = (
    df_clientes.sort_values("Cliente")
    .apply(lambda r: f"{r['Cliente']}  —  {r['Nombre']}", axis=1)
    .tolist()
)
sel = st.selectbox("Selecciona un cliente:", options=opciones, key="selector_cliente")

if sel:
    codigo = sel.split("  —  ")[0]
    mostrar_ficha_cliente(codigo, snapshot, historico)

# ── Descarga global ───────────────────────────
st.divider()
col_dl1, col_dl2 = st.columns(2)

with col_dl1:
    st.download_button(
        label="📥 Descargar resumen por cliente (Excel)",
        data=generar_excel_bytes(df_clientes, sheet_name="Resumen Clientes"),
        file_name="Monitor_Credito_Resumen.xlsx",
        mime="application/vnd.ms-excel",
        use_container_width=True,
    )

with col_dl2:
    st.download_button(
        label="📥 Descargar snapshot completo (Excel)",
        data=generar_excel_bytes(snapshot, sheet_name="Snapshot Actual"),
        file_name="Monitor_Credito_Snapshot.xlsx",
        mime="application/vnd.ms-excel",
        use_container_width=True,
    )