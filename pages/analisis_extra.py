import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
import os

st.set_page_config(page_title="Análisis Extra", page_icon="📈")

st.title("📊 Análisis Profundo de Crédito")
st.write("Esta es una página independiente para ver detalles específicos.")

# Aquí podrías poner una consulta a tu nueva tabla de Postgres
# para ver, por ejemplo, los clientes con más deuda.