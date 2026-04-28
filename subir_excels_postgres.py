"""
subir_excels_postgres.py
════════════════════════════════════════════════════════
Sube los archivos Excel auxiliares a PostgreSQL/Supabase.

Uso:
  python subir_excels_postgres.py --tabla facturas              --archivo "Facturas Dec-Feb.xlsx"
  python subir_excels_postgres.py --tabla incumplimientos       --archivo "Incumplimientos.xlsx"
  python subir_excels_postgres.py --tabla fecha_inicio          --archivo "Fecha Inicio Operacion.xlsx"

Requisitos:
  pip install pandas openpyxl psycopg2-binary sqlalchemy python-dotenv
════════════════════════════════════════════════════════
"""

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import os

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

# ── Conexión ─────────────────────────────────────────────
def get_engine():
    user = quote_plus(os.getenv("PG_USUARIO", "postgres"))
    pwd  = quote_plus(os.getenv("PG_PASSWORD", ""))
    host = os.getenv("PG_HOST", "localhost")
    port = os.getenv("PG_PUERTO", "5432")
    bd   = os.getenv("PG_BD", "postgres")

    return create_engine(
        f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{bd}",
        connect_args={"sslmode": "disable"},
        pool_pre_ping=True
    )

# ════════════════════════════════════════════════════════
#  MAPEOS POR TABLA
#  Cada función recibe el DataFrame crudo del Excel
#  y devuelve el DataFrame listo para insertar en PG.
#  AJUSTA los nombres de columna según tu Excel real.
# ════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    """Normaliza nombre de columna a snake_case sin acentos."""
    s = str(s).strip().lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        s = s.replace(a, b)
    return s.replace(" ", "_").replace("-", "_")


def procesar_facturas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mapea el Excel de Facturas a la tabla `facturas`.
    Ajusta los nombres de columna según tu archivo real.
    """
    df.columns = [_norm(c) for c in df.columns]
    log.info(f"Columnas detectadas en Excel: {list(df.columns)}")

    # ── Mapeo flexible: busca la columna por palabras clave ──
    def buscar(palabras: list[str]) -> str | None:
        for p in palabras:
            for c in df.columns:
                if p in c:
                    return c
        return None

    col_cliente    = buscar(["cliente"])
    col_dest       = buscar(["destinatario", "dest"])
    col_num        = buscar(["numero", "factura", "folio"])
    col_fecha_fac  = buscar(["fecha_fac", "fecha_doc", "emision"])
    col_fecha_venc = buscar(["vencimiento", "vence"])
    col_monto      = buscar(["monto", "importe", "total"])
    col_estatus    = buscar(["estatus", "status", "estado"])

    cols_encontradas = {
        "cliente":           col_cliente,
        "destinatario":      col_dest,
        "numero_factura":    col_num,
        "fecha_factura":     col_fecha_fac,
        "fecha_vencimiento": col_fecha_venc,
        "monto":             col_monto,
        "estatus_factura":   col_estatus,
    }
    log.info(f"Mapeo detectado: {cols_encontradas}")

    result = pd.DataFrame()
    for target, source in cols_encontradas.items():
        if source and source in df.columns:
            result[target] = df[source]
        else:
            result[target] = None

    # Tipos
    for c in ["fecha_factura", "fecha_vencimiento"]:
        result[c] = pd.to_datetime(result[c], errors="coerce")
    result["monto"] = pd.to_numeric(result["monto"], errors="coerce").fillna(0)
    result = result.dropna(subset=["cliente"])

    return result


def procesar_incumplimientos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mapea el Excel de Incumplimientos & Rotación.
    """
    df.columns = [_norm(c) for c in df.columns]
    log.info(f"Columnas detectadas: {list(df.columns)}")

    def buscar(palabras):
        for p in palabras:
            for c in df.columns:
                if p in c: return c
        return None

    result = pd.DataFrame()
    result["cliente"]                = df.get(buscar(["cliente"]))
    result["destinatario"]           = df.get(buscar(["destinatario","dest"]))
    result["periodo"]                = pd.to_datetime(df.get(buscar(["periodo","mes","fecha"])), errors="coerce")
    result["dias_incumplimiento"]    = pd.to_numeric(df.get(buscar(["dias_incum","incumplim"])), errors="coerce").fillna(0)
    result["dias_sobregiro"]         = pd.to_numeric(df.get(buscar(["dias_sobre","sobregiro"])), errors="coerce").fillna(0)
    result["monto_promedio_vencido"] = pd.to_numeric(df.get(buscar(["promedio","vencido"])), errors="coerce").fillna(0)
    result["rotacion_credito"]       = pd.to_numeric(df.get(buscar(["rotacion","rot"])), errors="coerce").fillna(0)
    result["monto_facturado"]        = pd.to_numeric(df.get(buscar(["facturado","facturacion"])), errors="coerce").fillna(0)
    result["num_facturas"]           = pd.to_numeric(df.get(buscar(["num_fact","cantidad","facturas"])), errors="coerce").fillna(0)
    result = result.dropna(subset=["cliente"])
    return result


def procesar_fecha_inicio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mapea el Excel de Fecha de Inicio de Operación.
    """
    df.columns = [_norm(c) for c in df.columns]
    log.info(f"Columnas detectadas: {list(df.columns)}")

    def buscar(palabras):
        for p in palabras:
            for c in df.columns:
                if p in c: return c
        return None

    result = pd.DataFrame()
    result["cliente"]          = df.get(buscar(["cliente"]))
    result["destinatario"]     = df.get(buscar(["destinatario","dest"]))
    result["nombre"]           = df.get(buscar(["nombre","razon"]))
    result["fecha_inicio"]     = pd.to_datetime(df.get(buscar(["inicio","alta","primer"])), errors="coerce")
    result["tipo_cliente"]     = df.get(buscar(["tipo"]))
    result["documento_actual"] = df.get(buscar(["documento","pagare","fianza","garantia"]))
    result["condicion_inicial"]= df.get(buscar(["condicion","condición"]))
    result["notas"]            = df.get(buscar(["nota","comentario","observacion"]))
    result = result.dropna(subset=["cliente"])
    return result


# ── Dispatch ─────────────────────────────────────────────
PROCESADORES = {
    "facturas":       (procesar_facturas,       "facturas"),
    "incumplimientos":(procesar_incumplimientos, "incumplimientos_rotacion"),
    "fecha_inicio":   (procesar_fecha_inicio,    "fecha_inicio_operacion"),
}


def subir(tabla_key: str, ruta_excel: str, hoja: str = 0, modo: str = "append"):
    if tabla_key not in PROCESADORES:
        log.error(f"Tabla '{tabla_key}' no reconocida. Opciones: {list(PROCESADORES)}")
        sys.exit(1)

    fn_procesar, tabla_pg = PROCESADORES[tabla_key]

    log.info(f"Leyendo: {ruta_excel} (hoja: {hoja})")
    df_raw = pd.read_excel(ruta_excel, sheet_name=hoja)
    log.info(f"Filas leídas del Excel: {len(df_raw):,}")

    df = fn_procesar(df_raw)
    log.info(f"Filas después de procesar: {len(df):,}")

    if df.empty:
        log.warning("DataFrame vacío — nada que subir.")
        return

    engine = get_engine()
    with engine.begin() as con:
        df.to_sql(
            tabla_pg,
            con,
            if_exists=modo,   # "replace" borra todo antes; "append" agrega
            index=False,
            chunksize=2_000,
            method="multi",
        )
    log.info(f"✅ {len(df):,} filas subidas a tabla '{tabla_pg}' (modo: {modo})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sube Excel a PostgreSQL")
    parser.add_argument("--tabla",    required=True,
                        choices=list(PROCESADORES),
                        help="facturas | incumplimientos | fecha_inicio")
    parser.add_argument("--archivo",  required=True, help="Ruta al archivo Excel")
    parser.add_argument("--hoja",     default=0,
                        help="Nombre o índice de la hoja (default: primera)")
    parser.add_argument("--modo",     default="append",
                        choices=["append","replace"],
                        help="append=agrega | replace=borra y recrea")
    args = parser.parse_args()

    # Convierte hoja a int si es número
    try:
        hoja = int(args.hoja)
    except ValueError:
        hoja = args.hoja

    subir(args.tabla, args.archivo, hoja=hoja, modo=args.modo)
