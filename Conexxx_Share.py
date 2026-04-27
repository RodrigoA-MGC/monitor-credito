import streamlit as st
import pandas as pd
import pyodbc

# Configuración de la página
st.set_page_config(page_title="Monitor de Crédito Ali", layout="wide")

st.title("📊 Monitor de Saldos y Clientes")

# --- 1. Inicializar la memoria (Session State) ---
# Guardamos dos cosas: el resumen para la tabla y el detallado para las fichas
if 'resumen' not in st.session_state:
    st.session_state.resumen = None
if 'detallado' not in st.session_state:
    st.session_state.detallado = None

# Barra lateral
with st.sidebar:
    st.header("Configuración")
    ruta = st.text_input("Ruta de la base de datos:", r'C:\Users\rodrigo.vazquez\Desktop\Ali\Versiones Access\Credito361 Ali A3.accdb')
    btn_procesar = st.button("🔄 Actualizar/Cargar Datos")

# --- 2. Lógica de Procesamiento ---
if btn_procesar:
    try:
        conn_str = f'DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={ruta};'
        conexion = pyodbc.connect(conn_str)
        query = "SELECT Cliente, fecha, Saldo, [Saldo vencido], [Saldo por vencer], Anticipos FROM [Historico_Monitor]"
        df = pd.read_sql(query, conexion)
        conexion.close()

        # Limpieza y Operaciones
        df['fecha'] = pd.to_datetime(df['fecha'])
        df = df.sort_values(by=['Cliente', 'fecha'])
        
        # Dejamos solo un registro por día por cliente
        df_limpio = df.drop_duplicates(subset=['Cliente', 'fecha'], keep='first').copy()
        
        # Llenar nulos antes de operar
        columnas_conta = ['Saldo vencido', 'Saldo por vencer', 'Anticipos']
        df_limpio[columnas_conta] = df_limpio[columnas_conta].fillna(0)
        
        # Cálculo de lógica de negocio
        df_limpio['Calculo_Neto'] = (df_limpio['Saldo vencido'] + df_limpio['Saldo por vencer']) - df_limpio['Anticipos']
        df_limpio['Es_Positivo'] = (df_limpio['Calculo_Neto'] > 0).astype(int)

        # Agregación Final para la tabla de arriba
        resultado = df_limpio.groupby('Cliente').agg({
            'fecha': 'count',
            'Saldo': 'sum',
            'Es_Positivo': 'sum'
        }).reset_index()
        
        resultado.columns = ['Cliente', 'Dias_Unicos', 'Suma_Saldo', 'Alertas']
        
        # --- GUARDAR EN SESIÓN ---
        st.session_state.resumen = resultado
        st.session_state.detallado = df_limpio
        
        st.success("¡Datos cargados con éxito!")

    except Exception as e:
        st.error(f"Error al conectar: {e}")

# --- 3. Interfaz de Usuario ---
# Solo mostramos esto si ya hay datos cargados en la sesión
if st.session_state.resumen is not None:
    resumen = st.session_state.resumen
    detallado = st.session_state.detallado

    # A. Métricas rápidas
    col1, col2 = st.columns(2)
    col1.metric("Total Clientes", len(resumen))
    col2.metric("Suma Total Saldo", f"${resumen['Suma_Saldo'].sum():,.2f}")

    # B. Buscador interactivo
    st.markdown("---")
    st.subheader("🔍 Buscador de Clientes")
    busqueda = st.text_input("Escribe el nombre del cliente para filtrar la tabla:")

    if busqueda:
        resumen_filtrado = resumen[resumen['Cliente'].str.contains(busqueda, case=False, na=False)]
    else:
        resumen_filtrado = resumen

    st.dataframe(resumen_filtrado, use_container_width=True)

    # C. Ficha de Estatus Individual
    st.markdown("---")
    st.header("👤 Ficha de Estatus Individual")

    cliente_fichas = resumen['Cliente'].unique()
    seleccion = st.selectbox("Selecciona un cliente para ver su detalle:", cliente_fichas)

    if seleccion:
        info_cliente = resumen[resumen['Cliente'] == seleccion].iloc[0]
        datos_historia = detallado[detallado['Cliente'] == seleccion]

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Días Operados", f"{int(info_cliente['Dias_Unicos'])} días")
        with col_b:
            st.metric("Saldo Acumulado", f"${info_cliente['Suma_Saldo']:,.2f}")
        with col_c:
            nivel = "Normal" if info_cliente['Alertas'] == 0 else "Crítico"
            st.metric("Nivel de Alerta", nivel, delta=int(info_cliente['Alertas']), delta_color="inverse")

        with st.expander(f"Ver historial detallado de {seleccion}"):
            columnas_ver = ['fecha', 'Saldo', 'Calculo_Neto', 'Es_Positivo']
            st.table(datos_historia[columnas_ver].sort_values(by='fecha', ascending=False))

            # --- BOTÓN DE ESTADO DE CUENTA ---
        st.subheader("📄 Reporte Oficial")
        
        # Preparamos los datos del Estado de Cuenta
        # Filtramos los movimientos del cliente seleccionado
        edo_cuenta = datos_historia[['fecha', 'Saldo', 'Calculo_Neto']].copy()
        edo_cuenta.columns = ['Fecha', 'Saldo Total', 'Monto Neto (V-A)']
        edo_cuenta = edo_cuenta.sort_values(by='Fecha', ascending=True)

        # Agregamos una columna de "Saldo Acumulado" (Cálculo dinámico)
        edo_cuenta['Saldo_Acumulado'] = edo_cuenta['Saldo Total'].cumsum()

        # Botón para descargar el Estado de Cuenta en Excel
        # Usamos un truco de Pandas para crear el archivo en memoria
        import io
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            edo_cuenta.to_excel(writer, index=False, sheet_name='Estado de Cuenta')
        
        st.download_button(
            label=f"📥 Descargar Estado de Cuenta de {seleccion}",
            data=buffer,
            file_name=f"EdoCuenta_{seleccion}.xlsx",
            mime="application/vnd.ms-excel"
        )

    # D. Botón de Descarga (Siempre disponible al final)
    st.markdown("---")
    csv = resumen.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Descargar Reporte Resumido (CSV)",
        data=csv,
        file_name="Reporte_Credito_Ali.csv",
        mime="text/csv"
    )

else:
    st.info("👋 ¡Hola! Por favor, verifica la ruta de la base de datos y presiona 'Actualizar/Cargar Datos' en la barra lateral.")