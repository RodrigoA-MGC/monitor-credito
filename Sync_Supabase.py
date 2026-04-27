import pandas as pd
from sqlalchemy import create_engine
import schedule
import time
import urllib.parse

# --- CONFIGURACIÓN ---
# Reemplaza con tus datos reales de la base LOCAL
USER_LOCAL = "postgres"
PASS_LOCAL = "Rayman123$" # <-- Cambia esto
HOST_LOCAL = "localhost"
PORT_LOCAL = "5432"
DB_LOCAL   = "monitor_credito"     # <-- Cambia esto

# Datos de SUPABASE (Ya codificados para evitar errores con el '$')
USER_SUPA = "postgres.vdaptujtrmsyhwybblya"
PASS_SUPA = urllib.parse.quote_plus("RodrigoRayman123$")
HOST_SUPA = "aws-1-us-east-1.pooler.supabase.com"
PORT_SUPA = "6543"
DB_SUPA   = "postgres"

# Motores de conexión
engine_local = create_engine(f'postgresql://{USER_LOCAL}:{PASS_LOCAL}@{HOST_LOCAL}:{PORT_LOCAL}/{DB_LOCAL}')
engine_supa  = create_engine(f'postgresql://{USER_SUPA}:{PASS_SUPA}@{HOST_SUPA}:{PORT_SUPA}/{DB_SUPA}')

def sincronizar():
    print(f"[{time.strftime('%H:%M:%S')}] Iniciando carga a Supabase...")
    try:
        # 1. Leer de local (490k filas ocupan memoria, las traemos de golpe o por bloques)
        df = pd.read_sql('SELECT * FROM historico_monitor', engine_local)
        
        # 2. Subir a Supabase
        # chunksize: manda de 5000 en 5000 para no saturar el plan gratis
        # method='multi': acelera la inserción
        df.to_sql('historico_monitor', engine_supa, 
                  if_exists='replace', 
                  index=False, 
                  chunksize=5000)
        
        print(f"✅ ÉXITO: {len(df)} registros sincronizados.")
    except Exception as e:
        print(f"❌ ERROR: {e}")

# Programar la tarea
schedule.every().day.at("09:00").do(sincronizar)

print("Servidor de sincronización activo.")
print("La tarea se ejecutará todos los días a las 09:00 AM.")
print("No cierres esta ventana si quieres que funcione automáticamente.")

# Para probar que funciona ahorita mismo sin esperar a las 9am, 
# puedes descomentar la siguiente línea una vez:
sincronizar()

while True:
    schedule.run_pending()
    time.sleep(60)