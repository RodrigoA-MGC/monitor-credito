import streamlit as st
import psycopg2
import pandas as pd

st.set_page_config(page_title="Test Conexion", layout="wide")
st.title("🔌 Test de Conexión — Supabase")

st.write("Leyendo secrets...")
try:
    host  = st.secrets["PG_HOST"]
    port  = st.secrets["PG_PORT"]
    db    = st.secrets["PG_DATABASE"]
    user  = st.secrets["PG_USER"]
    pwd   = st.secrets["PG_PASSWORD"]
    tabla = st.secrets["PG_TABLA"]
    st.success(f"✅ Secrets OK — host: {host}, tabla: {tabla}")
except Exception as e:
    st.error(f"❌ Error leyendo secrets: {e}")
    st.stop()

st.write("Conectando a PostgreSQL...")
try:
    conn = psycopg2.connect(
        host=host, port=int(port), dbname=db,
        user=user, password=pwd,
        sslmode="require", connect_timeout=15,
    )
    st.success("✅ Conexión OK")

    st.write("Leyendo primeras 5 filas...")
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {tabla} LIMIT 5")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    df = pd.DataFrame.from_records(rows, columns=cols)
    st.success(f"✅ Datos OK — columnas: {list(df.columns)}")
    st.dataframe(df)

except Exception as e:
    st.error(f"❌ Error: {e}")
