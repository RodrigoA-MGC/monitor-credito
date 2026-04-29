"""
sync_access_postgres.py
═══════════════════════════════════════════════════════════════
ACCESS → POSTGRES (VERSIÓN ESTABLE REAL)

✔ Limpia columnas inválidas (#, %, espacios, etc.)
✔ Evita errores de sintaxis en PostgreSQL
✔ Sync incremental seguro
✔ Inserción estable con pandas.to_sql
✔ Índice único para evitar duplicados
═══════════════════════════════════════════════════════════════
"""

import argparse
import logging
import os
import sys
import re
from datetime import datetime

import pandas as pd
import pyodbc
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
from pathlib import Path

# ─────────────────────────────────────────────
# LOGGING
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
# ENV
# ─────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
load_dotenv(_env, encoding="utf-8")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
ACCESS_RUTA  = os.getenv("ACCESS_RUTA")
ACCESS_TABLA = os.getenv("ACCESS_TABLA", "Historico_Monitor")

PG_HOST     = os.getenv("PG_HOST", "localhost")
PG_PUERTO   = os.getenv("PG_PUERTO", "5432")
PG_BD       = os.getenv("PG_BD", "monitor_credito")
PG_USUARIO  = os.getenv("PG_USUARIO", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD")
PG_TABLA    = os.getenv("PG_TABLA", "historico_monitor")

COLUMNA_FECHA = "fecha"

# ─────────────────────────────────────────────
# CONEXIONES
# ─────────────────────────────────────────────
def conectar_access():
    return pyodbc.connect(
        f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={ACCESS_RUTA};"
    )

def leer_access(query):
    with conectar_access() as con:
        df = pd.read_sql(query, con)
        log.info(f"Access → {len(df):,} filas")
        return df

def engine_postgres():
    from urllib.parse import quote_plus
    pwd = quote_plus(PG_PASSWORD)

    return create_engine(
        f"postgresql+psycopg2://{PG_USUARIO}:{pwd}@{PG_HOST}:{PG_PUERTO}/{PG_BD}",
        pool_pre_ping=True,
        connect_args={"client_encoding": "utf8"},
    )

# ─────────────────────────────────────────────
# LIMPIEZA CRÍTICA DE COLUMNAS
# ─────────────────────────────────────────────
def limpiar_columnas(df):
    df = df.copy()

    df.columns = [
        re.sub(r"[^a-zA-Z0-9_]", "_", str(c)).lower()
        for c in df.columns
    ]

    df.columns = [
        re.sub(r"_+", "_", c).strip("_")
        for c in df.columns
    ]

    return df

# ─────────────────────────────────────────────
# NORMALIZACIÓN
# ─────────────────────────────────────────────
def normalizar(df):
    df = limpiar_columnas(df)

    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")

    if "cldocumfinanciero" in df.columns:
        df["cldocumfinanciero"] = df["cldocumfinanciero"].fillna("SIN_DOC")

    # numéricos seguros
    num_cols = [
        "saldo_vencido",
        "saldo_por_vencer",
        "anticipos",
        "depositos_sap",
        "limite_credito",
    ]

    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df = df.where(pd.notnull(df), None)

    df["_sync_ts"] = datetime.utcnow()

    return df

# ─────────────────────────────────────────────
# MIGRACIÓN
# ─────────────────────────────────────────────
def migrar():
    log.info("═══ MIGRACIÓN COMPLETA ═══")

    df = leer_access(f"SELECT * FROM [{ACCESS_TABLA}]")
    df = normalizar(df)

    engine = engine_postgres()

    with engine.begin() as con:
        df.to_sql(
            PG_TABLA,
            con,
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=5000,
        )

    # índice único (clave anti-duplicados)
    with engine.begin() as con:
        con.execute(text(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_monitor
            ON {PG_TABLA} (
                cliente,
                fecha,
                destinatario_mercancia,
                cldocumfinanciero
            );
        """))

    log.info(f"✅ Migración completa: {len(df):,} registros")

# ─────────────────────────────────────────────
# SYNC INCREMENTAL
# ─────────────────────────────────────────────
def sync_incremental():
    log.info("═══ SYNC INCREMENTAL ═══")

    engine = engine_postgres()
    inspector = inspect(engine)

    if not inspector.has_table(PG_TABLA):
        log.warning("Tabla no existe → ejecutando migración")
        migrar()
        return

    with engine.connect() as con:
        fecha_max = con.execute(
            text(f"SELECT MAX({COLUMNA_FECHA}) FROM {PG_TABLA}")
        ).scalar()

    if fecha_max is None:
        migrar()
        return

    # formato Access compatible
    fecha_str = pd.Timestamp(fecha_max).strftime("%m/%d/%Y %H:%M:%S")

    query = f"""
        SELECT * FROM [{ACCESS_TABLA}]
        WHERE [{COLUMNA_FECHA}] > #{fecha_str}#
    """

    df = leer_access(query)

    if df.empty:
        log.info("✅ Sin nuevos registros")
        return

    df = normalizar(df)

    with engine.begin() as con:
        df.to_sql(
            PG_TABLA,
            con,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000,
        )

    log.info(f"✅ Sync completo: +{len(df):,} registros")

# ─────────────────────────────────────────────
# AUTO MODE
# ─────────────────────────────────────────────
def modo_auto():
    engine = engine_postgres()
    inspector = inspect(engine)

    if not inspector.has_table(PG_TABLA):
        migrar()
        return

    with engine.connect() as con:
        count = con.execute(text(f"SELECT COUNT(*) FROM {PG_TABLA}")).scalar()

    if count == 0:
        migrar()
    else:
        sync_incremental()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=["migracion", "sync", "auto"], default="auto")
    args = parser.parse_args()

    log.info(f"Modo: {args.modo}")

    if args.modo == "migracion":
        migrar()
    elif args.modo == "sync":
        sync_incremental()
    else:
        modo_auto()

    log.info("Fin del proceso")