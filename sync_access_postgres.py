"""
sync_access_postgres.py
═══════════════════════════════════════════════════════════════════
Espejo Access → PostgreSQL para Monitor de Crédito
Versión Corregida: Manejo de errores de codificación y rutas
═══════════════════════════════════════════════════════════════════
"""

import argparse
import logging
import os
import sys
import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyodbc
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("sync_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  CARGA DE CONFIGURACIÓN (ANTIBALAS)
# ─────────────────────────────────────────────
env_path = Path('.') / '.env'

if env_path.exists():
    try:
        # Leemos el archivo ignorando errores de codificación (adiós byte 0xab)
        with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            # Cargamos las variables desde el flujo de texto limpio
            load_dotenv(stream=io.StringIO(content), override=True)
        log.info("✅ Archivo .env cargado exitosamente")
    except Exception as e:
        log.warning(f"⚠️ No se pudo leer el archivo .env manualmente: {e}")
else:
    log.warning("⚠️ No se encontró el archivo .env, se usarán valores por defecto")

# ── Access ───────────────────────────────────
# Usamos r'' para que las barras invertidas de Windows no den problemas
ACCESS_RUTA  = os.getenv("ACCESS_RUTA", r'C:\Users\rodrigo.vazquez\Desktop\Ali\Versiones Access\Credito361 Ali A3.accdb')
ACCESS_TABLA = os.getenv("ACCESS_TABLA", "Historico_Monitor")

# ── PostgreSQL ───────────────────────────────
PG_HOST     = os.getenv("PG_HOST",     "localhost")
PG_PUERTO   = os.getenv("PG_PUERTO",   "5432")
PG_BD       = os.getenv("PG_BD",       "monitor_credito")
PG_USUARIO  = os.getenv("PG_USUARIO",  "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "Rayman123$")
PG_TABLA    = os.getenv("PG_TABLA",    "historico_monitor")
COLUMNA_FECHA = "fecha"

# ─────────────────────────────────────────────
#  CONECTORES
# ─────────────────────────────────────────────

def conectar_access() -> pyodbc.Connection:
    # Agregamos comillas a la ruta por si tiene espacios
    conn_str = (
        f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
        f"DBQ={ACCESS_RUTA};"
    )
    return pyodbc.connect(conn_str)

def leer_access(query: str) -> pd.DataFrame:
    con = conectar_access()
    try:
        cursor = con.cursor()
        cursor.execute(query)
        columnas = [desc[0] for desc in cursor.description]
        filas    = cursor.fetchall()
        # Convertir filas de pyodbc a lista de tuplas para Pandas
        df = pd.DataFrame.from_records([list(f) for f in filas], columns=columnas)
        log.info(f"Access → {len(df):,} filas leídas")
        return df
    finally:
        con.close()

def engine_postgres():
    from urllib.parse import quote_plus
    # quote_plus es vital para el símbolo $ en tu contraseña
    password_safe = quote_plus(PG_PASSWORD)
    url = (
        f"postgresql+psycopg2://{PG_USUARIO}:{password_safe}"
        f"@{PG_HOST}:{PG_PUERTO}/{PG_BD}"
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        # 'utf8' sin guion suele ser más compatible en Windows
        connect_args={"client_encoding": "utf8"},
    )

# ─────────────────────────────────────────────
#  NORMALIZACIÓN
# ─────────────────────────────────────────────

MAPEO_COLUMNAS = {
    "destinatario mercancia":  "destinatario_mercancia",
    "condiciones de pago":     "condiciones_pago",
    "nombre 1":                "nombre",
    "saldo vencido":           "saldo_vencido",
    "saldo por vencer":        "saldo_por_vencer",
    "anticipos":               "anticipos",
    "depositos sap":           "depositos_sap",
    "limite de credito":       "limite_credito",
    "cldocumfinanciero":       "cldocumfinanciero",
    "fecha":                   "fecha",
    "cliente":                 "cliente",
    "estatus":                 "estatus",
}

def _norm(s: str) -> str:
    return (
        str(s).strip().lower()
        .replace("á","a").replace("é","e").replace("í","i")
        .replace("ó","o").replace("ú","u").replace("ñ","n")
        .replace("_", " ")
    )

def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rename = {}
    for col in df.columns:
        k = _norm(col)
        if k in MAPEO_COLUMNAS:
            rename[col] = MAPEO_COLUMNAS[k]
        else:
            rename[col] = _norm(col).replace(" ", "_")
    df = df.rename(columns=rename)

    cols_num = ["saldo_vencido", "saldo_por_vencer", "anticipos", "depositos_sap", "limite_credito"]
    for c in cols_num:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")

    df["_sync_ts"] = datetime.utcnow()
    return df

# ─────────────────────────────────────────────
#  ACCIONES (Migrar, Sync, Verificar)
# ─────────────────────────────────────────────

def migrar():
    log.info("═══ MODO MIGRACIÓN COMPLETA ═══")
    df = leer_access(f"SELECT * FROM [{ACCESS_TABLA}]")
    df = normalizar(df)
    engine = engine_postgres()
    with engine.begin() as con:
        df.to_sql(PG_TABLA, con, if_exists="replace", index=False, chunksize=5000, method="multi")
    _crear_indices(engine)
    log.info(f"✅ Migración completa: {len(df):,} registros")

def sync_incremental():
    log.info("═══ MODO SYNC INCREMENTAL ═══")
    engine = engine_postgres()
    inspector = inspect(engine)
    if not inspector.has_table(PG_TABLA):
        log.warning("La tabla no existe. Migrando...")
        migrar()
        return

    with engine.connect() as con:
        fecha_max = con.execute(text(f"SELECT MAX({COLUMNA_FECHA}) FROM {PG_TABLA}")).scalar()

    if fecha_max is None:
        migrar()
        return

    fecha_str = pd.Timestamp(fecha_max).strftime("%Y-%m-%d")
    query = f"SELECT * FROM [{ACCESS_TABLA}] WHERE [{COLUMNA_FECHA}] > #{fecha_str}#"
    df_new = leer_access(query)

    if df_new.empty:
        log.info("✅ PG ya está al día.")
        return

    df_new = normalizar(df_new)
    with engine.begin() as con:
        df_new.to_sql(PG_TABLA, con, if_exists="append", index=False, chunksize=5000, method="multi")
    log.info(f"✅ Sync completo: +{len(df_new):,} registros")

def _crear_indices(engine):
    indices = [
        f"CREATE INDEX IF NOT EXISTS idx_{PG_TABLA}_fecha ON {PG_TABLA} (fecha);",
        f"CREATE INDEX IF NOT EXISTS idx_{PG_TABLA}_cliente ON {PG_TABLA} (cliente);",
    ]
    with engine.begin() as con:
        for sql in indices: con.execute(text(sql))

def verificar_conexiones():
    log.info("── Verificando Access ──")
    try:
        con = conectar_access()
        con.close()
        log.info("  ✅ Access OK")
    except Exception as e:
        log.error(f"  ❌ Access: {e}")

    log.info("── Verificando PostgreSQL ──")
    try:
        engine = engine_postgres()
        with engine.connect() as con:
            con.execute(text("SELECT 1"))
        log.info("  ✅ PostgreSQL OK")
    except Exception as e:
        log.error(f"  ❌ PostgreSQL Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=["migracion", "sync", "auto", "verificar"], default="verificar")
    args = parser.parse_args()

    if args.modo == "migracion": migrar()
    elif args.modo == "sync": sync_incremental()
    elif args.modo == "verificar": verificar_conexiones()
    else: log.info("Usa --modo verificar para probar conexiones.")