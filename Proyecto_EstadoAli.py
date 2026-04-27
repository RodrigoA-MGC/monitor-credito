import io
import pandas as pd
import streamlit as st
import urllib.parse
from sqlalchemy import create_engine, text, pool

# ─────────────────────────────────────────────
#  CONFIG (debe ser la PRIMERA llamada Streamlit)
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor de Crédito",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  CONSTANTES Y CONFIGURACIÓN
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
    "Cliente", "Destinatario mercancia", "Condiciones de pago",
    "Nombre 1", "fecha", "Saldo vencido", "Saldo por vencer",
    "Anticipos", "Depósitos SAP", "Límite de credito"
}

# ─────────────────────────────────────────────
#  CONECTORES (CORREGIDOS)
# ─────────────────────────────────────────────

def cargar_desde_postgresql(host, puerto, bd, usuario, password, query) -> pd.DataFrame:
    try:
        user_encoded = urllib.parse.quote_plus(usuario)
        pass_encoded = urllib.parse.quote_plus(password)
        url = f"postgresql+psycopg2://{user_encoded}:{pass_encoded}@{host}:{puerto}/{bd}?sslmode=require"
        
        # NullPool es vital para el Transaction Mode de Supabase
        engine = create_engine(url, poolclass=pool.NullPool)
        
        with engine.connect() as con:
            df = pd.read_sql(text(query), con)
            
            # Mapeo inmediato de Postgres (minúsculas) a lógica de negocio
            column_map = {
                'cliente': 'Cliente',
                'destinatario_mercancia': 'Destinatario mercancia',
                'condiciones_de_pago': 'Condiciones de pago',
                'nombre_1': 'Nombre 1',
                'fecha': 'fecha',
                'saldo_vencido': 'Saldo vencido',
                'saldo_por_vencer': 'Saldo por vencer',
                'anticipos': 'Anticipos',
                'depositos_sap': 'Depósitos SAP',
                'limite_de_credito': 'Límite de credito'
            }
            df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})
            return df
    except Exception as e:
        raise RuntimeError(f"Error en PostgreSQL: {e}")

def cargar_desde_csv(archivo):
    return pd.read_csv(archivo)

def cargar_desde_excel(archivo, hoja):
    return pd.read_excel(archivo, sheet_name=hoja)

def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Limpiar espacios en nombres de columnas
    df.columns = [str(c).strip() for c in df.columns]
    
    # Convertir a numérico las columnas de dinero
    for c in COLS_NUM:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
            
    # Limpiar strings
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
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["_dia"] = df["fecha"].dt.normalize()
    df = df.sort_values(["Cliente", "Destinatario mercancia", "fecha"], ascending=True)
    df = df.drop_duplicates(subset=["Cliente", "Destinatario mercancia", "_dia"], keep="first")
    df["fecha"] = df["_dia"]
    return df.drop(columns=["_dia"])

def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cond = df["Condiciones de pago"].astype(str).str.strip()
    activos = df["Anticipos"] + df["Depósitos SAP"]
    df["Sobregiro"] = 0.0
    df["Incumplimiento"] = 0.0
    df["Uso_vs_Limite"] = 0.0

    mask_cred = cond.isin(CONDICIONES_CREDITO)
    mask_ant = cond.isin(CONDICION_ANTICIPADO)
    mask_form = mask_cred | mask_ant

    df.loc[mask_cred, "Sobregiro"] = (df.loc[mask_cred, "Saldo vencido"] + df.loc[mask_cred, "Saldo por vencer"]) - activos[mask_cred]
    df.loc[mask_form, "Incumplimiento"] = df.loc[mask_form, "Saldo vencido"] - activos[mask_form]
    df.loc[mask_cred, "Uso_vs_Limite"] = (df.loc[mask_cred, "Saldo vencido"] + df.loc[mask_cred, "Saldo por vencer"]) - df.loc[mask_cred, "Límite de credito"]
    return df

def calcular_estatus(df: pd.DataFrame) -> pd.DataFrame:
    def _estatus(row):
        c = str(row["Condiciones de pago"]).strip()
        if c in CONDICIONES_INACTIVO: return "⚫ Inactivo"
        if c in CONDICION_CRA: return "🔵 CRA"
        if c in CONDICION_RECLAMACION: return "🟣 Reclamación"
        if c in CONDICION_EXCEPCION: return "🟠 Activo (Excepción)"
        if c in CONDICIONES_CREDITO or c in CONDICION_ANTICIPADO:
            if row["Sobregiro"] > 0.01 or row["Incumplimiento"] > 0.01:
                return "🔴 Suspendido"
            return "🟢 Activo"
        return "⚪ Sin clasificar"
    df["Estatus"] = df.apply(_estatus, axis=1)
    return df

def transformar(df_normalizado: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = calcular_snapshot_diario(df_normalizado)
    df = calcular_indicadores(df)
    df = calcular_estatus(df)
    snapshot_actual = df.sort_values("fecha").groupby(["Cliente", "Destinatario mercancia"], as_index=False).last()
    return snapshot_actual, df

# ─────────────────────────────────────────────
#  UI HELPERS
# ─────────────────────────────────────────────

def generar_excel_bytes(df: pd.DataFrame, sheet_name: str = "Datos") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()

def enriquecer_con_tipo_cliente(df: pd.DataFrame, df_tipo: pd.DataFrame | None) -> pd.DataFrame:
    if df is None: return None
    df = df.copy()
    if df_tipo is None or df_tipo.empty:
        df["Tipo de Cliente"] = "—"
        return df
    return df.merge(df_tipo, on="Destinatario mercancia", how="left").assign(**{"Tipo de Cliente": lambda d: d["Tipo de Cliente"].fillna("—")})

def resumen_por_cliente(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot is None or snapshot.empty: return pd.DataFrame()
    agg = snapshot.groupby("Cliente").agg(
        Nombre=("Nombre 1", "first"),
        Num_Destinatarios=("Destinatario mercancia", "nunique"),
        Saldo_Vencido=("Saldo vencido", "sum"),
        Saldo_Por_Vencer=("Saldo por vencer", "sum"),
        Sobregiro_Total=("Sobregiro", "sum"),
        Incumplimiento_Total=("Incumplimiento", "sum"),
        Fecha_Corte=("fecha", "max"),
        Tipo_Cliente=("Tipo de Cliente", "first"),
    ).reset_index()
    worst = snapshot.groupby("Cliente")["Estatus"].apply(lambda s: min(s, key=lambda x: PRIORIDAD_ESTATUS.get(x, 99))).reset_index().rename(columns={"Estatus": "Estatus_Cliente"})
    return agg.merge(worst, on="Cliente")

# ─────────────────────────────────────────────
#  LÓGICA DE CARGA Y APP
# ─────────────────────────────────────────────

if "snapshot" not in st.session_state:
    st.session_state.update({"snapshot": None, "historico": None, "fuente_activa": None, "tipo_cliente_df": None})

# CARGA INICIAL
if st.session_state.snapshot is None:
    with st.spinner("⏳ Conectando a PostgreSQL..."):
        try:
            df_raw = cargar_desde_postgresql(
                st.secrets["PG_HOST"], str(st.secrets.get("PG_PORT", "6543")),
                st.secrets["PG_DATABASE"], st.secrets["PG_USER"], 
                st.secrets["PG_PASSWORD"], f'SELECT * FROM "{st.secrets.get("PG_TABLA", "historico_monitor")}"'
            )
            df_raw = normalizar_columnas(df_raw)
            faltantes = validar_columnas(df_raw)
            if faltantes:
                st.error(f"❌ Columnas faltantes: {faltantes}")
                st.stop()
            snap, hist = transformar(df_raw)
            st.session_state.snapshot, st.session_state.historico = snap, hist
            st.session_state.fuente_activa = "PostgreSQL / Supabase"
        except Exception as e:
            st.error(f"❌ Error de inicio: {e}")
            st.stop()

# --- EL RESTO DE TU CÓDIGO DE INTERFAZ SIGUE IGUAL (SIDEBAR Y MAIN) ---
# (He mantenido la lógica de búsqueda y visualización intacta)
