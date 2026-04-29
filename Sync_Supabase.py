import pandas as pd
from sqlalchemy import create_engine, text
import schedule
import time
import urllib.parse

# ─────────────────────────────────────────────
# CONFIG LOCAL
# ─────────────────────────────────────────────
USER_LOCAL = "postgres"
PASS_LOCAL = "Rayman123$"
HOST_LOCAL = "localhost"
PORT_LOCAL = "5432"
DB_LOCAL   = "monitor_credito"

# ─────────────────────────────────────────────
# CONFIG SUPABASE
# ─────────────────────────────────────────────
USER_SUPA = "postgres.vdaptujtrmsyhwybblya"
PASS_SUPA = urllib.parse.quote_plus("RodrigoRayman123$")
HOST_SUPA = "aws-1-us-east-1.pooler.supabase.com"
PORT_SUPA = "6543"
DB_SUPA   = "postgres"

engine_local = create_engine(
    f'postgresql://{USER_LOCAL}:{PASS_LOCAL}@{HOST_LOCAL}:{PORT_LOCAL}/{DB_LOCAL}'
)

engine_supa = create_engine(
    f'postgresql://{USER_SUPA}:{PASS_SUPA}@{HOST_SUPA}:{PORT_SUPA}/{DB_SUPA}'
)

# ─────────────────────────────────────────────
# SYNC SIN DUPLICADOS (UPSERT REAL)
# ─────────────────────────────────────────────
def sincronizar():
    print(f"[{time.strftime('%H:%M:%S')}] Iniciando carga a Supabase...")

    try:
        # 1. Leer datos locales
        df = pd.read_sql("SELECT * FROM historico_monitor", engine_local)

        # 2. Limpiar NaN → None (IMPORTANTE para Postgres)
        df = df.where(pd.notnull(df), None)

        # 3. Insertar con control de duplicados
        query = text("""
            INSERT INTO historico_monitor (
                cliente,
                fecha,
                destinatario_mercancia,
                cldocumfinanciero,
                saldo_vencido,
                saldo_por_vencer,
                saldo,
                estatus,
                nombre
            )
            VALUES (
                :cliente,
                :fecha,
                :destinatario_mercancia,
                :cldocumfinanciero,
                :saldo_vencido,
                :saldo_por_vencer,
                :saldo,
                :estatus,
                :nombre
            )
            ON CONFLICT (
                cliente,
                fecha,
                destinatario_mercancia,
                cldocumfinanciero
            )
            DO NOTHING;
        """)

        with engine_supa.begin() as conn:
            for row in df.to_dict(orient="records"):
                conn.execute(query, row)

        print(f"✅ ÉXITO: {len(df)} registros procesados sin duplicados")

    except Exception as e:
        print(f"❌ ERROR: {e}")
    

# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────
schedule.every().day.at("09:00").do(sincronizar)

print("Servidor de sincronización activo")
print("Ejecución diaria: 09:00 AM")

# prueba inmediata
sincronizar()

while True:
    schedule.run_pending()
    time.sleep(60)