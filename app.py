import streamlit as st
import pandas as pd
import requests
import time
import datetime as dt
from pymongo import MongoClient
import pytz
import os
from dotenv import load_dotenv
import plotly.graph_objects as go

# ======= CONFIGURAÇÕES =======
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
REFRESH_INTERVAL = 60  # segundos

st.set_page_config(
    page_title="Cotação do Dólar + Operações",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ======= Conexão MongoDB =======
try:
    client = MongoClient(MONGO_URI)
    db = client["Zoho"]
except Exception as e:
    st.error(f"Erro ao conectar ao MongoDB: {e}")
    st.stop()

# ======= Funções auxiliares =======
def carregar_pedidos():
    try:
        dados = list(db["Pedidos - CRM"].find())
        return pd.DataFrame(dados) if dados else pd.DataFrame()
    except Exception as e:
        st.error(f"Erro ao carregar pedidos: {e}")
        return pd.DataFrame()

def carregar_ordens():
    try:
        dados = list(db["Ordens de compra - CRM"].find())
        return pd.DataFrame(dados) if dados else pd.DataFrame()
    except Exception as e:
        st.error(f"Erro ao carregar ordens: {e}")
        return pd.DataFrame()

def tratar_decimais(df, colunas):
    for col in colunas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df

def tratar_datas(df, colunas):
    for col in colunas:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
            if df[col].dt.tz is not None:
                df[col] = df[col].dt.tz_convert(pytz.UTC)
            df[col] = df[col].dt.tz_localize(None)
    return df

# ======= Cotação ao vivo via AwesomeAPI =======
def obter_cotacao():
    try:
        url = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "USDBRL" in data:
            return float(data["USDBRL"]["bid"])
        else:
            st.warning(f"⚠️ Retorno inesperado da API: {data}")
            return None
    except Exception as e:
        st.error(f"Erro ao obter cotação: {e}")
        return None

# ======= Histórico via AwesomeAPI =======
def obter_historico():
    try:
        url = "https://economia.awesomeapi.com.br/json/daily/USD-BRL/100"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list) or len(data) == 0:
            st.error(f"⚠️ Dados inválidos retornados da API: {data}")
            return pd.DataFrame()

        df_hist = pd.DataFrame(data)
        if 'timestamp' not in df_hist.columns or 'bid' not in df_hist.columns:
            st.error(f"⚠️ Estrutura inesperada: {df_hist.head()}")
            return pd.DataFrame()

        df_hist['timestamp'] = pd.to_datetime(df_hist['timestamp'], unit='s')
        df_hist['bid'] = df_hist['bid'].astype(float)
        df_hist = df_hist.sort_values('timestamp')
        return df_hist

    except Exception as e:
        st.error(f"Erro ao obter histórico: {e}")
        return pd.DataFrame()

# ======= Carregar dados do MongoDB =======
pedidos_raw = carregar_pedidos()
ordens_raw = carregar_ordens()

if pedidos_raw.empty or ordens_raw.empty:
    st.warning("⚠️ Nenhum dado encontrado no MongoDB. Verifique as coleções.")
    st.stop()

# ======= Tratar pedidos =======
colunas_pedidos = ['Assunto', 'Status', 'Hora de Criação', 'Condição de Pagamento', 'Pedido Filho?', 'Quantidade Total', 'Produtos']
df_pedidos = pedidos_raw[colunas_pedidos].copy()
tratar_datas(df_pedidos, ["Hora de Criação"])
tratar_decimais(df_pedidos, ["Quantidade Total"])
df_pedidos = df_pedidos.rename(columns={"Produtos": "Produto", "Quantidade Total": "Qtd_Vendida"})
df_pedidos['Data'] = df_pedidos['Hora de Criação'].dt.date

# ======= Tratar ordens =======
colunas_ordens = ['Nome Produto', 'Quantidade Paga', 'Armazém', 'Hora de Criação', "Pedido de Compra"]
df_ordens = ordens_raw[colunas_ordens].copy()
tratar_decimais(df_ordens, ["Quantidade Paga"])
df_ordens = df_ordens.rename(columns={"Nome Produto": "Produto", "Quantidade Paga": "Qtd_Comprada"})
df_ordens['Data'] = pd.to_datetime(df_ordens['Hora de Criação']).dt.date
if "Número do Pedido" not in df_ordens.columns:
    df_ordens["Número do Pedido"] = df_ordens.index + 1

# ======= Sidebar - filtros =======
st.sidebar.header("Filtros")
hoje = dt.datetime.now().date()
data_filtrada = st.sidebar.date_input("Filtrar por data", value=hoje)

condicoes = df_pedidos["Condição de Pagamento"].dropna().unique()
condicao_selecionada = st.sidebar.multiselect("Condição de Pagamento", options=condicoes, default=condicoes)

ped_filho_options = df_pedidos["Pedido Filho?"].dropna().unique()
pedido_filho_selecionado = st.sidebar.multiselect("Pedido Filho?", options=ped_filho_options, default=ped_filho_options)

status_options = df_pedidos["Status"].dropna().unique()
status_selecionado = st.sidebar.multiselect("Status", options=status_options, default=status_options)

armazens = df_ordens["Armazém"].dropna().unique() if "Armazém" in df_ordens.columns else []
armazem_selecionado = st.sidebar.multiselect("Armazém", options=armazens, default=armazens)

# ======= Layout placeholders =======
col1, col2 = st.columns([2, 1])
grafico_placeholder = col1.empty()
cards_placeholder = col2.empty()
status_placeholder = st.empty()

# ======= Loop principal =======
while True:
    try:
        # --- Cotação ---
        cotacao = obter_cotacao()
        fuso = pytz.timezone("America/Sao_Paulo")
        hora = dt.datetime.now(fuso).strftime("%H:%M:%S")

        # --- Histórico ---
        df_hist = obter_historico()
        if df_hist.empty:
            time.sleep(REFRESH_INTERVAL)
            st.rerun()

        ultimos_valores = df_hist['bid'].tolist()
        fechamento_anterior = ultimos_valores[-2] if len(ultimos_valores) >= 2 else ultimos_valores[-1]
        variacao = (ultimos_valores[-1] - fechamento_anterior) / fechamento_anterior * 100
        cor_variacao = "lime" if variacao >= 0 else "red"
        fill_color = 'rgba(0,255,0,0.2)' if cor_variacao == "lime" else 'rgba(255,0,0,0.2)'

        # --- Gráfico ---
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_hist['timestamp'],
            y=df_hist['bid'],
            mode='lines',
            line=dict(color=cor_variacao, width=3),
            fill='tozeroy',
            fillcolor=fill_color,
            name='USD/BRL'
        ))
        y_min, y_max = min(ultimos_valores), max(ultimos_valores)
        y_margin = (y_max - y_min) * 0.08 if (y_max - y_min) > 0 else 0.2
        fig.update_layout(
            template='plotly_dark',
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="Data",
            yaxis_title="Cotação (R$)",
            height=420,
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            yaxis=dict(range=[y_min - y_margin, y_max + y_margin])
        )
        grafico_placeholder.plotly_chart(fig, use_container_width=True)

        # --- Cards ---
        cards_html = f"""
        <div style='display:flex; flex-direction:column; gap:20px; margin-top:10px;'>
            <div style='background:#0e1117; padding:20px; border-radius:15px; text-align:center;
                        box-shadow:0 4px 10px rgba(0,0,0,0.4);'>
                <h4 style='color:#aaa;'>💰 Dólar (ao vivo)</h4>
                <h2 style='color:{cor_variacao};'>
                    {f'R$ {cotacao:.4f}' if cotacao is not None else 'Sem dados'}
                </h2>
            </div>
            <div style='background:#0e1117; padding:20px; border-radius:15px; text-align:center;
                        box-shadow:0 4px 10px rgba(0,0,0,0.4);'>
                <h4 style='color:#aaa;'>📉 Variação (vs último fechamento)</h4>
                <h2 style='color:{cor_variacao};'>{variacao:+.2f}%</h2>
            </div>
        </div>
        """
        cards_placeholder.markdown(cards_html, unsafe_allow_html=True)

        # ======= Aplicar filtros =======
        df_pedidos_filtrado = df_pedidos[
            (df_pedidos['Data'] == data_filtrada) &
            (df_pedidos["Condição de Pagamento"].isin(condicao_selecionada)) &
            (df_pedidos["Pedido Filho?"].isin(pedido_filho_selecionado)) &
            (df_pedidos["Status"].isin(status_selecionado))
        ]
        df_ordens_filtrado = df_ordens[
            (df_ordens['Data'] == data_filtrada) &
            (df_ordens["Armazém"].isin(armazem_selecionado))
        ]

        # --- Tabelas ---
        tabela1, tabela2 = st.columns(2)
        with tabela1:
            st.markdown("#### Pedidos de Venda")
            st.dataframe(df_pedidos_filtrado[['Assunto', 'Produto', 'Qtd_Vendida', 'Data', 'Status']], use_container_width=True)
        with tabela2:
            st.markdown("#### Ordens de Compra")
            st.dataframe(df_ordens_filtrado[['Pedido de Compra', 'Produto', 'Qtd_Comprada', 'Data']], use_container_width=True)

        status_placeholder.info(f"🕒 Atualizado em {hora} — próxima atualização em {REFRESH_INTERVAL}s")

    except Exception as e:
        status_placeholder.error(f"Erro ao obter dados: {e}")

    time.sleep(REFRESH_INTERVAL)
    st.rerun()
