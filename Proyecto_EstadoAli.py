import io
import urllib.parse

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# ─────────────────────────────────────────────
#  CONFIG  (debe ser la PRIMERA llamada Streamlit)
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de Crédito",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  CONSTANTES DE NEGOCIO
# ─────────────────────────────────────────────
CONDICIONES_CREDITO   = {"CP008", "CP15", "CP20", "CP25", "CP30", "CP45"}
CONDICION_EXCEPCION   = {"CP45"}
CONDICION_ANTICIPADO  = {"CP00"}
CONDICIONES_INACTIVO  = {"MG01", "MG02", "MG03", "MG04", "MG06", "0001"}
CONDICION_CRA         = {"CRA"}
CONDICION_RECLAMACION = {"MG05"}

PRIORIDAD_ESTATUS = {
    "🔴 Suspendido":         0,
    "🟣 Reclamación":        1,
    "🟠 Activo (Excepción)": 2,
    "🔵 CRA":                3,
    "⚫ Inactivo":           4,
    "🟢 Activo":             5,
    "⚪ Sin clasificar":     6,
}

COLS_NUM = [
    "Saldo vencido", "Saldo por vencer",
    "Anticipos", "Depósitos SAP", "Límite de credito",
]

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

# ─────────────────────────────────────────────
#  CONECTORES
# ─────────────────────────────────────────────

def cargar_desde_postgresql(host, puerto, bd, usuario, password, query) -> pd.DataFrame:
    import psycopg2
    conn = psycopg2.connect(
        host=host,
        port=int(puerto),
        dbname=bd,
        user=usuario,
        password=password,
        sslmode="require",
        connect_timeout=15,
    )
    try:
        df = pd.read_sql(query, conn)
    finally:
        conn.close()
    return df


def cargar_desde_csv(archivo) -> pd.DataFrame:
    return pd.read_csv(archivo)


def cargar_desde_excel(archivo, hoja) -> pd.DataFrame:
    return pd.read_excel(archivo, sheet_name=hoja)


# ─────────────────────────────────────────────
#  NORMALIZACIÓN DE COLUMNAS
#  Mapea nombres que vienen de PostgreSQL (snake_case)
#  al nombre estándar que usa el resto del código.
# ─────────────────────────────────────────────
MAPEO_COLUMNAS = {
    # snake_case de PostgreSQL
    "destinatario_mercancia": "Destinatario mercancia",
    "condiciones_pago":       "Condiciones de pago",
    "nombre":                 "Nombre 1",
    "nombre_1":               "Nombre 1",
    "saldo_vencido":          "Saldo vencido",
    "saldo_por_vencer":       "Saldo por vencer",
    "anticipos":              "Anticipos",
    "depositos_sap":          "Depósitos SAP",
    "limite_credito":         "Límite de credito",
    "limite_de_credito":      "Límite de credito",
    "fecha":                  "fecha",
    "cliente":                "Cliente",
    # por si vienen con espacios
    "destinatario mercancia": "Destinatario mercancia",
    "condiciones de pago":    "Condiciones de pago",
    "saldo vencido":          "Saldo vencido",
    "saldo por vencer":       "Saldo por vencer",
    "depositos sap":          "Depósitos SAP",
    "limite de credito":      "Límite de credito",
}


def _norm_key(s: str) -> str:
    s = str(s).strip().lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        s = s.replace(a, b)
    return s


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for col in df.columns:
        k = _norm_key(col)
        if k in MAPEO_COLUMNAS and col != MAPEO_COLUMNAS[k]:
            rename[col] = MAPEO_COLUMNAS[k]
    df = df.rename(columns=rename)
    for c in COLS_NUM:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    for c in ["Cliente", "Destinatario mercancia", "Condiciones de pago", "Nombre 1"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    return df


def validar_columnas(df: pd.DataFrame) -> list[str]:
    return [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]


# ─────────────────────────────────────────────
#  PIPELINE DE NEGOCIO
# ─────────────────────────────────────────────

def calcular_snapshot_diario(df: pd.DataFrame) -> pd.DataFrame:
    """Primer registro de cada (Cliente, Destinatario, día)."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["_dia"] = df["fecha"].dt.normalize()
    df = df.sort_values(["Cliente", "Destinatario mercancia", "fecha"], ascending=True)
    df = df.drop_duplicates(
        subset=["Cliente", "Destinatario mercancia", "_dia"], keep="first"
    )
    df["fecha"] = df["_dia"]
    df = df.drop(columns=["_dia"])
    return df.sort_values(["Cliente", "Destinatario mercancia", "fecha"])


def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cond    = df["Condiciones de pago"].astype(str).str.strip()
    activos = df["Anticipos"] + df["Depósitos SAP"]

    df["Sobregiro"]      = 0.0
    df["Incumplimiento"] = 0.0
    df["Uso_vs_Limite"]  = 0.0

    mask_cred = cond.isin(CONDICIONES_CREDITO)
    mask_ant  = cond.isin(CONDICION_ANTICIPADO)
    mask_form = mask_cred | mask_ant

    df.loc[mask_cred, "Sobregiro"] = (
        (df.loc[mask_cred, "Saldo vencido"] + df.loc[mask_cred, "Saldo por vencer"])
        - activos[mask_cred]
    )
    df.loc[mask_form, "Incumplimiento"] = (
        df.loc[mask_form, "Saldo vencido"] - activos[mask_form]
    )
    df.loc[mask_cred, "Uso_vs_Limite"] = (
        (df.loc[mask_cred, "Saldo vencido"] + df.loc[mask_cred, "Saldo por vencer"])
        - df.loc[mask_cred, "Límite de credito"]
    )
    return df


def calcular_estatus(df: pd.DataFrame) -> pd.DataFrame:
    def _estatus(row):
        c = str(row["Condiciones de pago"]).strip()
        if c in CONDICIONES_INACTIVO:  return "⚫ Inactivo"
        if c in CONDICION_CRA:         return "🔵 CRA"
        if c in CONDICION_RECLAMACION: return "🟣 Reclamación"
        if c in CONDICION_EXCEPCION:   return "🟠 Activo (Excepción)"
        if c in CONDICIONES_CREDITO or c in CONDICION_ANTICIPADO:
            if row["Sobregiro"] > 0 or row["Incumplimiento"] > 0:
                return "🔴 Suspendido"
            return "🟢 Activo"
        return "⚪ Sin clasificar"

    df = df.copy()
    df["Estatus"] = df.apply(_estatus, axis=1)
    return df


def obtener_snapshot_actual(historico: pd.DataFrame) -> pd.DataFrame:
    """Último registro por (Cliente, Destinatario)."""
    return (
        historico
        .sort_values("fecha")
        .groupby(["Cliente", "Destinatario mercancia"], as_index=False)
        .last()
    )


def transformar(df_normalizado: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pipeline completo. Recibe df YA normalizado.
    Retorna (snapshot_actual, historico_completo).
    """
    df = calcular_snapshot_diario(df_normalizado)
    df = calcular_indicadores(df)
    df = calcular_estatus(df)
    return obtener_snapshot_actual(df), df


# ─────────────────────────────────────────────
#  HELPERS UI
# ─────────────────────────────────────────────

def generar_excel_bytes(df: pd.DataFrame, sheet_name: str = "Datos") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def enriquecer_con_tipo_cliente(
    df: pd.DataFrame, df_tipo: pd.DataFrame | None
) -> pd.DataFrame:
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
    agg = snapshot.groupby("Cliente").agg(
        Nombre               = ("Nombre 1",               "first"),
        Num_Destinatarios    = ("Destinatario mercancia",  "nunique"),
        Saldo_Vencido        = ("Saldo vencido",           "sum"),
        Saldo_Por_Vencer     = ("Saldo por vencer",        "sum"),
        Anticipos_Total      = ("Anticipos",               "sum"),
        Depositos_Total      = ("Depósitos SAP",           "sum"),
        Sobregiro_Total      = ("Sobregiro",               "sum"),
        Incumplimiento_Total = ("Incumplimiento",          "sum"),
        Fecha_Corte          = ("fecha",                   "max"),
        Tipo_Cliente         = ("Tipo de Cliente",         "first"),
    ).reset_index()

    worst = (
        snapshot.groupby("Cliente")["Estatus"]
        .apply(lambda s: min(s, key=lambda x: PRIORIDAD_ESTATUS.get(x, 99)))
        .reset_index()
        .rename(columns={"Estatus": "Estatus_Cliente"})
    )
    return agg.merge(worst, on="Cliente")


# ─────────────────────────────────────────────
#  FICHA DE CLIENTE
# ─────────────────────────────────────────────

def mostrar_ficha_cliente(
    cliente: str,
    snapshot: pd.DataFrame,
    historico: pd.DataFrame,
):
    snap_cli = snapshot[snapshot["Cliente"] == cliente].copy()
    hist_cli = historico[historico["Cliente"] == cliente].copy()
    nombre   = snap_cli.iloc[0].get("Nombre 1", "—")
    dests    = sorted(snap_cli["Destinatario mercancia"].unique())

    st.markdown(f"### {nombre} &nbsp; `{cliente}`")
    tabs = st.tabs(["📊 Consolidado"] + [f"📦 {d}" for d in dests])

    with tabs[0]:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Destinatarios",   len(dests))
        c2.metric("Saldo Vencido",   f"${snap_cli['Saldo vencido'].sum():,.2f}")
        c3.metric("Por Vencer",      f"${snap_cli['Saldo por vencer'].sum():,.2f}")
        c4.metric("Sobregiro Total", f"${snap_cli['Sobregiro'].sum():,.2f}")
        c5.metric("Incumplimiento",  f"${snap_cli['Incumplimiento'].sum():,.2f}")

        st.dataframe(
            snap_cli[[
                "Destinatario mercancia", "Tipo de Cliente", "Condiciones de pago",
                "Saldo vencido", "Saldo por vencer", "Anticipos", "Depósitos SAP",
                "Límite de credito", "Sobregiro", "Incumplimiento",
                "Uso_vs_Limite", "Estatus",
            ]].rename(columns={
                "Destinatario mercancia": "Destinatario",
                "Tipo de Cliente":        "Tipo",
                "Condiciones de pago":    "Condición",
                "Saldo vencido":          "Vencido",
                "Saldo por vencer":       "Por Vencer",
                "Límite de credito":      "Límite",
                "Uso_vs_Limite":          "Excedente Límite",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={c: st.column_config.NumberColumn(format="$%.2f")
                           for c in ["Vencido", "Por Vencer", "Anticipos",
                                     "Depósitos SAP", "Límite", "Sobregiro",
                                     "Incumplimiento", "Excedente Límite"]},
        )

    for i, dest in enumerate(dests):
        with tabs[i + 1]:
            snap_d = snap_cli[snap_cli["Destinatario mercancia"] == dest].iloc[0]
            hist_d = hist_cli[hist_cli["Destinatario mercancia"] == dest].sort_values("fecha")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Estatus",           snap_d["Estatus"])
            m2.metric("Condición de Pago", snap_d["Condiciones de pago"])
            m3.metric("Saldo Vencido",     f"${snap_d['Saldo vencido']:,.2f}")
            m4.metric("Por Vencer",        f"${snap_d['Saldo por vencer']:,.2f}")

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Sobregiro",         f"${snap_d['Sobregiro']:,.2f}")
            m6.metric("Incumplimiento",    f"${snap_d['Incumplimiento']:,.2f}")
            m7.metric("Límite de Crédito", f"${snap_d['Límite de credito']:,.2f}")
            m8.metric("Excedente Límite",  f"${snap_d['Uso_vs_Limite']:,.2f}")

            tipo_c = snap_d.get("Tipo de Cliente", "—")
            st.caption(
                f"Última fecha de corte: **{snap_d['fecha'].strftime('%d/%m/%Y')}**"
                f"  ·  Tipo de Cliente: **{tipo_c}**"
            )

            with st.expander(f"📋 Historial — {dest}", expanded=False):
                cols_hist = [
                    "fecha", "Saldo vencido", "Saldo por vencer",
                    "Anticipos", "Depósitos SAP",
                    "Sobregiro", "Incumplimiento", "Uso_vs_Limite", "Estatus",
                ]
                st.dataframe(
                    hist_d[cols_hist].sort_values("fecha", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "fecha": st.column_config.DateColumn("Fecha"),
                        **{c: st.column_config.NumberColumn(format="$%.2f")
                           for c in ["Saldo vencido", "Saldo por vencer",
                                     "Anticipos", "Depósitos SAP",
                                     "Sobregiro", "Incumplimiento", "Uso_vs_Limite"]},
                    },
                )

            edo = hist_d[[
                "fecha", "Condiciones de pago",
                "Saldo vencido", "Saldo por vencer",
                "Anticipos", "Depósitos SAP",
                "Sobregiro", "Incumplimiento", "Uso_vs_Limite", "Estatus",
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
#  SESSION STATE
# ─────────────────────────────────────────────
for key in ("snapshot", "historico", "fuente_activa", "tipo_cliente_df"):
    if key not in st.session_state:
        st.session_state[key] = None


# ─────────────────────────────────────────────
#  CARGA AUTOMÁTICA AL INICIAR
#  Se ejecuta solo cuando snapshot es None
#  (primer arranque o después de un refresco).
# ─────────────────────────────────────────────
if st.session_state.snapshot is None:
    _host  = st.secrets["PG_HOST"]
    _port  = str(st.secrets.get("PG_PORT", "6543"))
    _bd    = st.secrets["PG_DATABASE"]
    _user  = st.secrets["PG_USER"]
    _pass  = st.secrets["PG_PASSWORD"]
    _tabla = st.secrets.get("PG_TABLA", "historico_monitor")

    with st.spinner("⏳ Conectando a PostgreSQL y cargando datos..."):
        try:
            df_raw = cargar_desde_postgresql(
                _host, _port, _bd, _user, _pass,
                f'SELECT * FROM "{_tabla}"',
            )
            df_raw = normalizar_columnas(df_raw)
            faltantes = validar_columnas(df_raw)
            if faltantes:
                st.error(
                    f"❌ Columnas faltantes en la tabla: **{faltantes}**\n\n"
                    f"Columnas que llegaron desde PostgreSQL: `{list(df_raw.columns)}`"
                )
                st.stop()
            snap, hist = transformar(df_raw)
            st.session_state.snapshot      = snap
            st.session_state.historico     = hist
            st.session_state.fuente_activa = "PostgreSQL / Supabase"
        except Exception as e:
            st.error(f"❌ Error al cargar datos:\n\n`{e}`")
            st.stop()


# ─────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Opciones")

    # ── Refresco ──────────────────────────────
    st.markdown("#### 🔄 Actualizar datos")
    if st.button("Recargar desde PostgreSQL", use_container_width=True, key="btn_refresh"):
        st.session_state.snapshot      = None
        st.session_state.historico     = None
        st.session_state.fuente_activa = None
        st.rerun()

    if st.session_state.fuente_activa:
        st.caption(f"🟢 Fuente: {st.session_state.fuente_activa}")

    st.divider()

    # ── Carga manual opcional ─────────────────
    st.markdown("#### 📂 Cargar desde archivo")
    fuente_manual = st.radio(
        "Fuente manual:",
        ["— ninguna —", "📂 CSV", "📗 Excel"],
        index=0,
    )

    if fuente_manual == "📂 CSV":
        archivo = st.file_uploader("Sube tu CSV", type=["csv"])
        if st.button("Cargar CSV", use_container_width=True):
            if not archivo:
                st.error("Sube un archivo primero.")
            else:
                try:
                    df_raw = normalizar_columnas(cargar_desde_csv(archivo))
                    faltantes = validar_columnas(df_raw)
                    if faltantes:
                        st.error(f"Columnas faltantes: {faltantes}")
                    else:
                        st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                        st.session_state.fuente_activa = f"CSV: {archivo.name}"
                        st.rerun()
                except Exception as e:
                    st.error(str(e))

    elif fuente_manual == "📗 Excel":
        archivo = st.file_uploader("Sube tu Excel", type=["xlsx", "xls"])
        hoja = st.text_input("Hoja:", value="Sheet1")
        if st.button("Cargar Excel", use_container_width=True):
            if not archivo:
                st.error("Sube un archivo primero.")
            else:
                try:
                    df_raw = normalizar_columnas(cargar_desde_excel(archivo, hoja))
                    faltantes = validar_columnas(df_raw)
                    if faltantes:
                        st.error(f"Columnas faltantes: {faltantes}")
                    else:
                        st.session_state.snapshot, st.session_state.historico = transformar(df_raw)
                        st.session_state.fuente_activa = f"Excel: {archivo.name}/{hoja}"
                        st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.divider()

    # ── Tipo de Cliente complementario ────────
    st.markdown("#### 📎 Tipo de Cliente")
    st.caption("Excel con columnas CENTRAL y Tipo de Cliente")

    archivo_tc = st.file_uploader(
        "Sube el Excel de tipos",
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
                col_central = next(
                    (c for c in df_tc.columns if c.upper() == "CENTRAL"), None
                )
                col_tipo = next(
                    (c for c in df_tc.columns
                     if "tipo" in c.lower() and "cliente" in c.lower()), None
                )
                if not col_central or not col_tipo:
                    st.error(
                        f"No encontré CENTRAL / Tipo de Cliente.\n"
                        f"Columnas detectadas: {list(df_tc.columns)}"
                    )
                else:
                    df_tc = (
                        df_tc[[col_central, col_tipo]]
                        .rename(columns={
                            col_central: "Destinatario mercancia",
                            col_tipo:    "Tipo de Cliente",
                        })
                        .drop_duplicates(subset=["Destinatario mercancia"])
                    )
                    st.session_state.tipo_cliente_df = df_tc
                    st.success(f"✅ {len(df_tc)} registros cargados")
            except Exception as e:
                st.error(str(e))

    if col_b.button("Limpiar", use_container_width=True, key="btn_limpiar_tc"):
        st.session_state.tipo_cliente_df = None
        st.rerun()

    if st.session_state.tipo_cliente_df is not None:
        st.caption(f"🟢 Tipos cargados: {len(st.session_state.tipo_cliente_df)}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
st.title("📊 Monitor de Crédito")

snapshot  = st.session_state.snapshot
historico = st.session_state.historico

snapshot  = enriquecer_con_tipo_cliente(snapshot,  st.session_state.tipo_cliente_df)
historico = enriquecer_con_tipo_cliente(historico, st.session_state.tipo_cliente_df)

df_clientes = resumen_por_cliente(snapshot)

# ── KPIs ──────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Clientes totales",   len(df_clientes))
k2.metric("🟢 Activos",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🟢 Activo"]))
k3.metric("🔴 Suspendidos",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🔴 Suspendido"]))
k4.metric("🟠 Excepción CP45",
          len(df_clientes[df_clientes["Estatus_Cliente"] == "🟠 Activo (Excepción)"]))
k5.metric("Sobregiro total",
          f"${df_clientes['Sobregiro_Total'].sum():,.2f}")
k6.metric("Incumplimiento total",
          f"${df_clientes['Incumplimiento_Total'].sum():,.2f}")

# ── Monitor ────────────────────────────────────
st.divider()
st.subheader("🔍 Monitor de Estatus Actual")
fecha_max = snapshot["fecha"].max()
st.caption(
    f"Datos al: **{fecha_max.strftime('%d/%m/%Y')}** · "
    f"{len(snapshot)} destinatarios"
)

busqueda = st.text_input(
    "Buscar por código o nombre:",
    placeholder="Ej: FF1095 o Empresa SA",
    key="buscador_principal",
)

mask = (
    df_clientes["Cliente"].str.contains(busqueda, case=False, na=False)
    | df_clientes["Nombre"].str.contains(busqueda, case=False, na=False)
) if busqueda else pd.Series([True] * len(df_clientes))

st.dataframe(
    df_clientes[mask][[
        "Cliente", "Nombre", "Tipo_Cliente", "Num_Destinatarios",
        "Saldo_Vencido", "Saldo_Por_Vencer",
        "Sobregiro_Total", "Incumplimiento_Total",
        "Estatus_Cliente", "Fecha_Corte",
    ]].rename(columns={
        "Tipo_Cliente":        "Tipo de Cliente",
        "Num_Destinatarios":   "# Dest.",
        "Saldo_Vencido":       "Vencido",
        "Saldo_Por_Vencer":    "Por Vencer",
        "Sobregiro_Total":     "Sobregiro",
        "Incumplimiento_Total":"Incumplimiento",
        "Estatus_Cliente":     "Estatus",
        "Fecha_Corte":         "Fecha Corte",
    }),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Vencido":        st.column_config.NumberColumn(format="$%.2f"),
        "Por Vencer":     st.column_config.NumberColumn(format="$%.2f"),
        "Sobregiro":      st.column_config.NumberColumn(format="$%.2f"),
        "Incumplimiento": st.column_config.NumberColumn(format="$%.2f"),
        "Fecha Corte":    st.column_config.DateColumn(),
    },
)

# ── Ficha individual ───────────────────────────
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

# ── Descargas ──────────────────────────────────
st.divider()
col_dl1, col_dl2 = st.columns(2)
with col_dl1:
    st.download_button(
        label="📥 Resumen por cliente (Excel)",
        data=generar_excel_bytes(df_clientes, sheet_name="Resumen"),
        file_name="Monitor_Credito_Resumen.xlsx",
        mime="application/vnd.ms-excel",
        use_container_width=True,
    )
with col_dl2:
    st.download_button(
        label="📥 Snapshot completo (Excel)",
        data=generar_excel_bytes(snapshot, sheet_name="Snapshot"),
        file_name="Monitor_Credito_Snapshot.xlsx",
        mime="application/vnd.ms-excel",
        use_container_width=True,
    )