#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import requests
from datetime import datetime, date, timedelta
import plotly.graph_objects as go
import numpy as np
import plotly.express as px
from scipy.stats import norm
from math import log, sqrt, exp

# NOVA SEÇÃO: Funções extraídas e adaptadas do BS.py
TOKEN_BS = "fSFuq/876/hItIxNprPz/1/Wvvd8snH1yLVVVKPQbGO4K78AAuShUWFFYG/rUdx8--0M/Ya7In/d/Go2SyUDZ7pw==--YzUzNWRlZjE0YmRiNjU3MTc2NDRiZGMyYzQ2N2NmNDA="  # Token do BS.py

def get_option_details(symbol):
    url = f"https://api.oplab.com.br/v3/market/options/details/{symbol}"
    headers = {"Access-Token": TOKEN_BS}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao consultar details API: {e}")
        return None

def implied_vol(S, K, T, r, premium, option_type):
    def objective(sigma):
        price, _, _, _, _, _ = black_scholes(S, K, T, r, sigma, option_type)
        return price - premium
    if not all(isinstance(x, (int, float)) and x > 0 for x in [S, K, T, premium]):
        return 0.0
    try:
        vol = brentq(objective, 0.001, 5.0)
        return vol * 100  # Converter para porcentagem
    except:
        return 0.0

def calculate_business_days(expiry_date_str):
    if not expiry_date_str:
        return 30
    today = date.today()
    try:
        expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        business_days = len(pd.bdate_range(start=today, end=expiry))
        return max(0, business_days)
    except:
        return 30

def black_scholes(S, K, T, r, sigma, option_type="call"):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0, 0, 0, 0, 0, 0
    d1 = (log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)

    if option_type == "call":
        price = S*norm.cdf(d1) - K*exp(-r*T)*norm.cdf(d2)
        delta = norm.cdf(d1)
        theta = -(S*norm.pdf(d1)*sigma/(2*sqrt(T))) - r*K*exp(-r*T)*norm.cdf(d2)
        rho   =  K*T*exp(-r*T)*norm.cdf(d2)
    else:
        price = K*exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
        delta = -norm.cdf(-d1)
        theta = -(S*norm.pdf(d1)*sigma/(2*sqrt(T))) + r*K*exp(-r*T)*norm.cdf(-d2)
        rho   = -K*T*exp(-r*T)*norm.cdf(-d2)

    gamma = norm.pdf(d1)/(S*sigma*sqrt(T))
    vega  = S*norm.pdf(d1)*sqrt(T)

    return price, delta, gamma, vega, theta, rho

# Função adaptada para calcular payoff de múltiplas opções selecionadas
def calculate_selected_payoff(selected_rows, spot_price, quantidade=100, taxa_juros=0.149):
    payoffs = []
    for _, row in selected_rows.iterrows():
        opcao = row['Opção']
        tipo = row['Tipo'].lower()
        strike = row['Strike']
        maturity_date = row['Maturity Date']
        premium = (row['Bid'] + row['Ask']) / 2  # Usa média Bid/Ask como prêmio
        dias_uteis = calculate_business_days(str(maturity_date))
        T = dias_uteis / 252
        vol = row.get('IV', 25.0) / 100  # Usa IV da tabela ou default 25%

        # Calcula BS para essa opção
        price_bs, delta, gamma, vega, theta, rho = black_scholes(spot_price, strike, T, taxa_juros, vol, tipo)

        # Payoff simples (intrínseco - prêmio)
        S_range = np.linspace(spot_price * 0.8, spot_price * 1.2, 100)
        if tipo == "call":
            payoff_unit = np.maximum(S_range - strike, 0) - premium
        else:
            payoff_unit = np.maximum(strike - S_range, 0) - premium
        payoff_total = payoff_unit * quantidade

        payoffs.append({
            'Opção': opcao,
            'Tipo': tipo.upper(),
            'Strike': strike,
            'Premium': premium,
            'Dias Úteis': dias_uteis,
            'Vol (%)': vol * 100,
            'Delta': delta,
            'Payoff Total': payoff_total  # Array para plot
        })
    return pd.DataFrame(payoffs)

# ---------------- Logo ----------------
st.set_page_config(page_title="Dashboard", layout="wide")

# ---------------- Logo + Botões na mesma linha ----------------
cols_top = st.columns([2, 1, 1, 1, 1])  # 5 colunas: logo maior, 4 botões

# Coluna 0: Logo
cols_top[0].image("logo.png", width=170)


# Função para criar card/botão
def tool_card(name, url, tooltip, bg_color="#1c1c1c"):
    return f"""
        <a href="{url}" target="_blank" class="tool-link" data-tip="{tooltip}" style="
            display:block;
            background-color:{bg_color};
            border:1px solid #333;
            border-radius:8px;
            padding:14px;
            margin-bottom:0px;
            box-shadow:1px 1px 6px rgba(0,0,0,0.25);
            color:#ffffff;
            font-size:14px;
            font-weight:600;
            text-align:center;
            text-decoration:none !important;
            transition: background 0.3s, transform 0.2s;
            cursor:pointer;
        ">
            {name}
        </a>
    """

# Colunas 1, 2, 3, 4: botões
cols_top[1].markdown(tool_card(
    "Calculadora<br>Notional",
    "https://calculadoranotional.streamlit.app/",
    "Equivalência entre comprar ações e opções"
), unsafe_allow_html=True)

cols_top[2].markdown(tool_card(
    "Calculadora<br>Black & Scholes",
    "https://calculadorblackescholes.streamlit.app/",
    "Calcule e simule o prêmio das opções"
), unsafe_allow_html=True)

cols_top[3].markdown(tool_card(
    "Fluxo<br>Investidores e Ativos",
    "https://fluxob3eativos.streamlit.app/",
    "Saiba o fluxo dos vários tipos de investidores e ativos da B3"
), unsafe_allow_html=True)

cols_top[4].markdown(tool_card(
    "Radar<br>Tendência do Ativo",
    "https://radarb3.streamlit.app/",
    "Tendência de movimento do Ativo pelo volume negociado por opções"
), unsafe_allow_html=True)

# ---------------- CSS para tooltip ----------------
st.markdown("""
<style>
.tool-link::after {
    content: attr(data-tip);
    position: fixed;
    left: 50%;
    bottom: 60%;
    transform: translateX(-50%);
    background: #333;
    color: #fff;
    font-size: 13px;
    line-height: 1.4;
    padding: 10px 14px;
    border-radius: 6px;
    max-width: 100vw;
    white-space: normal;
    word-wrap: break-word;
    text-align: center;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
    z-index: 9999;
}

.tool-link:hover::after {
    opacity: 1;
}
</style>
""", unsafe_allow_html=True)

# ---------------- TradingView Widget ----------------
tradingview_widget = """
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <div class="tradingview-widget-copyright">
    <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">
      <span class="blue-text"></span>
    </a>
  </div>
  <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>
  {
  "symbols": [
    {"proName": "FX_IDC:EURUSD", "title": "EUR to USD"},
    {"proName": "BITSTAMP:BTCUSD", "title": "Bitcoin"},
    {"proName": "FXOPEN:DXY", "title": "DXY"},
    {"proName": "SPREADEX:DJI", "title": "DJI"},
    {"proName": "EUREX:FESX1!", "title": "EUROPA"},
    {"proName": "NASDAQ:NQASIAN", "title": "ASIA"},
    {"proName": "GOMARKETS:ASX200", "title": "AUSTRALIA"},
    {"proName": "BLACKBULL:BRENT", "title": "PETROLEO"},
    {"proName": "BMFBOVESPA:IBOV", "title": "IBOV"},
    {"proName": "BMFBOVESPA:IND1!", "title": "INDICE FUTURO"},
    {"proName": "NASDAQ:NDX", "title": "NASDAQ"},
    {"proName": "BMFBOVESPA:PETR4", "title": "PETR4"},
    {"proName": "BMFBOVESPA:VALE3", "title": "VALE3"},
    {"proName": "BMFBOVESPA:BBAS3", "title": "BBAS3"}
  ],
  "colorTheme": "dark",
  "locale": "en",
  "largeChartUrl": "",
  "isTransparent": false,
  "showSymbolLogo": true,
  "displayMode": "adaptive"
}
  </script>
</div>
"""
components.html(tradingview_widget, height=120, scrolling=False)

# ---------------- Título ----------------
st.title("Dashboard")

# ---------------- Token ----------------
TOKEN = "fSFuq/876/hItIxNprPz/1/Wvvd8snH1yLVVVKPQbGO4K78AAuShUWFFYG/rUdx8--0M/Ya7In/d/Go2SyUDZ7pw==--YzUzNWRlZjE0YmRiNjU3MTc2NDRiZGMyYzQ2N2NmNDA="

# Cache pra volatilidade por ticker (evita refetch)
volatility_cache = {}  # {ticker: sigma}


# ---------------- Funções ----------------
def calcular_gregas_fallback(symbol, spot, strike, maturity_date, option_type, rf_rate=0.1, days=60, token=None):
    """
    Calcula Delta, Gamma, Vega, Theta, Rho e IV usando modelo de Black-Scholes
    quando a API não retorna as gregas (usa volatilidade histórica como fallback)
    """
    try:
        # Guards contra valores inválidos
        if not spot or not strike or spot <= 0 or strike <= 0:
            return {"Delta": 0.5, "Gamma": 0.01, "Vega": 0.1, "Theta": -0.01, "Rho": 0.1, "IV": 25.0}

        # Parsing seguro de maturity_date
        try:
            maturity_dt = datetime.strptime(maturity_date, "%Y-%m-%d")
        except ValueError:
            maturity_dt = datetime.now() + timedelta(days=30)  # Default: 30 dias
        today_dt = datetime.now()
        T = max((maturity_dt - today_dt).days / 365.0, 1/365.0)  # Mínimo 1 dia

        # Volatilidade histórica (com cache e fallback e mínimo)
        sigma = volatility_cache.get(symbol, 0.25)  # Pega do cache ou default 25%
        if sigma == 0.25:  # Só calcula se não cached
            try:
                end_date = today_dt.strftime("%Y-%m-%d")
                start_date = (today_dt - timedelta(days=days)).strftime("%Y-%m-%d")
                url = f"https://api.oplab.com.br/v3/market/historical-prices/{symbol}?start_date={start_date}&end_date={end_date}"
                headers = {"Access-Token": token} if token else {}
                resp = requests.get(url, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                if "data" in data and len(data["data"]) > 1:
                    prices = [float(d.get("price", 0)) for d in data["data"] if float(d.get("price", 0)) > 0]
                    if len(prices) > 1:
                        log_returns = np.diff(np.log(prices))
                        sigma = max(np.std(log_returns) * np.sqrt(252), 0.10)  # Mínimo 10%
                        volatility_cache[symbol] = sigma  # Cacheia pro ticker
            except Exception:
                pass  # Mantém default
        sigma = max(sigma, 0.10)  # Evita <10%

        # Black-Scholes: d1 e d2
        d1 = (np.log(spot / strike) + (rf_rate + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        option_type = option_type.upper()
        if option_type == "CALL":
            delta_raw = norm.cdf(d1)
            rho = spot * T * np.exp(-rf_rate * T) * norm.cdf(d1)
            N_d1 = norm.cdf(d1)
            N_d2 = norm.cdf(d2)
        else:  # PUT
            delta_raw = -norm.cdf(-d1)
            rho = -strike * T * np.exp(-rf_rate * T) * norm.cdf(-d2)
            N_d1 = norm.cdf(-d1)
            N_d2 = norm.cdf(-d2)

        # Delta com cap em ±0.99
        delta = min(max(delta_raw, -0.99), 0.99)

        # Gamma e Vega com floor e escalonamento (baseado em |d1| <2 para ATM-ish)
        gamma_base = norm.pdf(d1) / (spot * sigma * np.sqrt(T))
        vega_base = spot * norm.pdf(d1) * np.sqrt(T) / 100
        atm_factor = max(0.1, 1 / (1 + abs(d1)))  # Fator: ~1 ATM, ~0.1 extremes
        gamma = max(gamma_base * atm_factor, 0.001)  # Mínimo 0.001
        vega = max(vega_base * atm_factor, 0.01)     # Mínimo 0.01

        # Theta corrigido (sempre ≤0, cap em -0.001 pra extremos)
        term1 = - (spot * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
        term2 = - rf_rate * strike * np.exp(-rf_rate * T) * N_d2
        term3 = rf_rate * spot * N_d1 if option_type == "CALL" else -rf_rate * spot * N_d1
        theta = min((term1 + term2 + term3) / 365, 0)  # Força ≤0, cap implícito no modelo

        return {
            "Delta": round(delta, 4),
            "Gamma": round(gamma, 4),
            "Vega": round(vega, 4),
            "Theta": round(theta, 4),
            "Rho": round(rho, 4),
            "IV": round(sigma * 100, 2)  # Em %
        }

    except Exception as e:
        # Fallback de emergência: valores aproximados com caps
        moneyness_dist = abs(spot - strike) / spot
        is_atm = moneyness_dist < 0.05
        delta_fb = 0.5 if is_atm else (0.8 if (strike < spot and option_type == "CALL") or (strike > spot and option_type == "PUT") else 0.2)
        if option_type == "PUT": delta_fb = -delta_fb
        delta_fb = min(max(delta_fb, -0.99), 0.99)  # Cap aqui também
        
        gamma_fb = 0.05 if is_atm else 0.01
        vega_fb = 0.15 if is_atm else 0.05
        
        return {
            "Delta": round(delta_fb, 4),
            "Gamma": round(gamma_fb, 4),
            "Vega": round(vega_fb, 4),
            "Theta": -0.01,
            "Rho": 0.1 if option_type == "CALL" else -0.1,
            "IV": 25.0
        }


def get_stock_data(symbol: str, token: str):
    url = f"https://api.oplab.com.br/v3/market/stocks/{symbol}"
    headers = {"Access-Token": token}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def get_all_options(symbol: str, token: str, limit: int = 200):
    url = f"https://api.oplab.com.br/v3/market/options/{symbol}?limit={limit}"
    headers = {"Access-Token": token}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def get_historical_greeks(symbol: str, token: str, days: int = 20):
    hoje = datetime.now()
    dias_passados = hoje - timedelta(days=days)
    from_date = dias_passados.strftime("%Y-%m-%d")
    to_date = hoje.strftime("%Y-%m-%d")
    url = f"https://api.oplab.com.br/v3/market/historical/options/{symbol}/{from_date}/{to_date}"
    headers = {"Access-Token": token}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def parse_stock(stock_json: dict) -> (pd.DataFrame, float):
    spot = stock_json.get("spot_price") or stock_json.get("close")
    data = {
        "Ticker": stock_json.get("symbol", "N/A"),
        "Spot Price": spot if spot else "N/A",
        "IV Current": stock_json.get("iv_current", "N/A"),
        "IV 1Y Max": stock_json.get("iv_1y_max", "N/A"),
        "IV 1Y Min": stock_json.get("iv_1y_min", "N/A"),
        "IV 1Y Percentile": stock_json.get("iv_1y_percentile", "N/A"),
        "IV 1Y Rank": stock_json.get("iv_1y_rank", "N/A")
    }
    return pd.DataFrame([data]), spot

def parse_options(raw_options: list, spot: float) -> pd.DataFrame:
    registros = []
    for opt in raw_options:
        strike = opt.get("strike", 0)
        tipo = opt.get("type", "").upper()  # Tipo da opção: CALL ou PUT

        # Cálculo correto de Moneyness
        if spot:
            diff = (strike - spot) / spot
            if -0.005 <= diff <= 0.005:
                moneyness = "ATM"
            else:
                if tipo == "CALL":
                    moneyness = "ITM" if strike < spot else "OTM"
                elif tipo == "PUT":
                    moneyness = "ITM" if strike > spot else "OTM"
                else:
                    moneyness = "N/A"
        else:
            moneyness = "N/A"

        # Processamento do vencimento (simplificado: só guarda a data como string)
        maturity = opt.get("due_date", "N/A")

        registros.append({
            "Opção": opt.get("symbol", "N/A"),
            "Tipo": tipo,
            "Estilo": opt.get("maturity_type", "N/A"),
            "Bid": opt.get("bid", 0),
            "Ask": opt.get("ask", 0),
            "Strike": strike,
            "Maturity Date": maturity,
            "Moneyness": moneyness,
            "Volume": opt.get("volume", 0),
            "Financial Volume": opt.get("financial_volume", 0),
            "Contract Size": opt.get("contract_size", 0),
            "Market Maker": opt.get("market_maker", 0)
        })

    df = pd.DataFrame(registros)
    df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
    return df

def parse_greeks(raw_history: list) -> pd.DataFrame:
    registros = []
    for opt in raw_history:
        registros.append({
            "Opção": opt.get("symbol", "N/A"),
            "Delta": opt.get("delta", 0),
            "Gamma": opt.get("gamma", 0),
            "Vega": opt.get("vega", 0),
            "Theta": opt.get("theta", 0),
            "Rho": opt.get("rho", 0),
            "IV": opt.get("volatility", 0)
        })
    return pd.DataFrame(registros)
    
def adjust_on_ticker_change():
    current_ticker = st.session_state.get('ticker_input', '').strip().upper()
    if current_ticker and current_ticker != st.session_state.get('last_ticker', ''):
        st.session_state['last_ticker'] = current_ticker
        try:
            stock_json = get_stock_data(current_ticker, TOKEN)
            spot = parse_stock(stock_json)[1]  # Só spot
            if spot:
                st.session_state['min_strike'] = spot * 0.9
                st.session_state['max_strike'] = spot * 1.1
                # Sem st.rerun() aqui – warning some e ajuste roda no main
        except Exception as e:
            pass  # Ignora erro no change
            
# ---------------- Interface lateral ----------------
ticker = st.sidebar.text_input(
    "Ticker do Ativo (ex: PETR4)", 
    value="ITUB4",
    key='ticker_input',  # Key pra controlar change
    on_change=adjust_on_ticker_change  # Callback pra atualizar session_state silenciosamente
).strip().upper()

st.sidebar.subheader("Tipo de Opção")
filter_call = st.sidebar.checkbox("CALL", True)
filter_put = st.sidebar.checkbox("PUT", False)

st.sidebar.subheader("Moneyness")
filter_itm = st.sidebar.checkbox("ITM", False)
filter_atm = st.sidebar.checkbox("ATM", True)
filter_otm = st.sidebar.checkbox("OTM", False)

st.sidebar.subheader("Vencimento")
selected_start = st.sidebar.date_input(
    "Data de Vencimento Inicial",
    value=datetime.now().date(),
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2030, 12, 31)
)
selected_end = st.sidebar.date_input(
    "Data de Vencimento Final",
    value=datetime.now().date() + timedelta(days=30),
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2030, 12, 31)
)


# ---------------- CSS global ----------------
st.markdown("""
<style>
.hover-card {
    transition: all 0.3s ease;
    cursor: pointer;
}
.hover-card:hover {
    transform: translateY(-5px);
    box-shadow: 4px 4px 20px rgba(0,0,0,0.3);
    filter: brightness(1.1);
}
.block-separator {
    margin-top: 50px;
    margin-bottom: 50px;
}
</style>
""", unsafe_allow_html=True)

# ---------------- Filtro de Strike Range ----------------
st.sidebar.subheader("Range de Strike")
min_strike = st.sidebar.slider(
    "Strike Mínimo (R$)",
    min_value=0.0,
    max_value=200.0,
    value=st.session_state.get('min_strike', 5.0),  # <-- Novo: Pega do session_state ou default
    step=0.10
)
# Salva no session_state pra persistir
if 'min_strike' not in st.session_state:
    st.session_state['min_strike'] = min_strike

max_strike = st.sidebar.slider(
    "Strike Máximo (R$)",
    min_value=0.0,
    max_value=200.0,
    value=st.session_state.get('max_strike', 150.0),  # <-- Novo: Pega do session_state ou default
    step=0.10
)
# Salva no session_state pra persistir
if 'max_strike' not in st.session_state:
    st.session_state['max_strike'] = max_strike
# ---------------- Busca e renderização ----------------
buscar_clicked = st.sidebar.button("Buscar Opções")

st.markdown("""
<style>
div.stButton > button:first-child {
    color: #28a745 !important;
    border: 2px solid #28a745 !important;
    background-color: black !important;
    font-weight: bold;
    border-radius: 8px;
    padding: 8px 16px;
    transition: all 0.2s ease;
}
div.stButton > button:first-child:hover {
    background-color: #28a745 !important;
    color: black !important;
}
</style>
""", unsafe_allow_html=True)

# ---------------- Botão/Cartão da Calculadora (expander) ----------------
with st.sidebar.expander("⚙️ Ferramentas", expanded=False):
    st.markdown("""
    <style>
    div[data-testid="stExpander"] > div:first-child {
        color: #ffc107 !important;
        font-weight: bold;
        border-bottom: 2px solid #ffc107;
        padding-bottom: 4px;
        margin-bottom: 10px;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("""
    <style>
    .tool-link {
        display: block;
        background-color: #1c1c1c;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 14px;
        box-shadow: 1px 1px 6px rgba(0,0,0,0.25);
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
        text-align: center;
        text-decoration: none !important;
        transition: background 0.3s, transform 0.2s;
        position: relative;
        cursor: pointer;
    }
    .tool-link:hover {
        background-color: #28a745;
        color: black;
        transform: translateY(-2px);
    }
    .tool-link::after {
        content: attr(data-tip);
        position: fixed;
        left: 50%;
        bottom: 60%;
        transform: translateX(-50%);
        background: #333;
        color: #fff;
        font-size: 13px;
        line-height: 1.4;
        padding: 10px 14px;
        border-radius: 6px;
        max-width: 100vw;
        white-space: normal;
        word-wrap: break-word;
        text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.2s;
        z-index: 9999;
    }
    .tool-link:hover::after {
        opacity: 1;
    }
    </style>
    <a href="https://calculadoranotional.streamlit.app/"
       target="_blank"
       class="tool-link"
       data-tip="Equivalência entre comprar ações e opções">
       Calculadora<br>Notional
    </a>
    <a href="https://calculadorblackescholes.streamlit.app/"
       target="_blank"
       class="tool-link"
       data-tip="Calcule e simule o prêmio das opções">
       Calculadora<br>Black & Scholes
    </a>
    <a href="https://fluxob3eativos.streamlit.app/"
       target="_blank"
       class="tool-link"
       data-tip="Saiba o fluxo dos vários tipos de investidores e ativos da B3">
       Fluxo<br>Investidores e Ativos
    </a>
    <a href="https://radarb3.streamlit.app/"
       target="_blank"
       class="tool-link"
       data-tip="Tendência de movimento do Ativo pelo volume negociado por opções">
       Radar<br>Tendência do Mercado
    </a>
    """, unsafe_allow_html=True)

# ---------- Lógica de Autoload e Cache de Dados ----------
# NOVA CORREÇÃO: Inicializa caches no session_state se não existirem
if "data_cached" not in st.session_state:
    st.session_state["data_cached"] = False
if "df_stock" not in st.session_state:
    st.session_state["df_stock"] = None
if "spot" not in st.session_state:
    st.session_state["spot"] = None
if "df_table" not in st.session_state:
    st.session_state["df_table"] = pd.DataFrame()
if "effective_ticker_cached" not in st.session_state:
    st.session_state["effective_ticker_cached"] = None
if "already_loaded" not in st.session_state:
    st.session_state["already_loaded"] = False

do_auto = not st.session_state["already_loaded"]
should_run = buscar_clicked or do_auto

# NOVA CORREÇÃO: Detecta mudança no ticker para forçar reload (sem depender só do botão)
current_ticker = ticker.strip().upper()
if current_ticker != st.session_state["effective_ticker_cached"]:
    should_run = True  # Força reload se ticker mudou

if should_run:
    with st.spinner(f"🔄 Carregando dados filtrados de opções para {current_ticker}... Aguarde um momento!"):
        try:
            if not current_ticker:
                st.warning("Informe um ticker.")
            else:
                stock_json = get_stock_data(current_ticker, TOKEN)
                df_stock_new, spot_new = parse_stock(stock_json)

                # ---------------- Auto-ajuste dinâmico de strike range (10% menos/mais do spot) ----------------
                current_ticker_full = current_ticker
                last_ticker = st.session_state.get('last_ticker', '')
                if spot_new and current_ticker_full != last_ticker:
                    auto_min = spot_new * 0.9
                    auto_max = spot_new * 1.1
                    st.session_state['min_strike'] = auto_min
                    st.session_state['max_strike'] = auto_max
                    st.session_state['last_ticker'] = current_ticker_full
                    st.sidebar.success(f"✅ Range ajustado: R$ {auto_min:.2f} - R$ {auto_max:.2f} (spot {spot_new:.2f})")
                    # NÃO usa st.rerun() aqui para evitar loop infinito

                acao_nome = stock_json.get("name", current_ticker)
                st.session_state["df_stock"] = df_stock_new
                st.session_state["spot"] = spot_new
                st.session_state["effective_ticker_cached"] = current_ticker_full

                if not stock_json.get("has_options", False):
                    st.info("A ação não possui opções listadas.")
                else:
                    raw_options = get_all_options(current_ticker_full, TOKEN, 200)
                    df_options = parse_options(raw_options, spot_new)
                    raw_history = get_historical_greeks(current_ticker_full, TOKEN, days=25)
                    df_greeks = parse_greeks(raw_history)
                    df_options = df_options.merge(df_greeks, left_on="Opção", right_on="Opção", how="left")

                    tipos = []
                    if filter_call: tipos.append("CALL")
                    if filter_put: tipos.append("PUT")
                    df_table_new = df_options[df_options["Tipo"].isin(tipos)]

                    moneyness_selec = []
                    if filter_itm: moneyness_selec.append("ITM")
                    if filter_atm: moneyness_selec.append("ATM")
                    if filter_otm: moneyness_selec.append("OTM")
                    df_table_new = df_table_new[df_table_new["Moneyness"].isin(moneyness_selec)]                        
                    
                    # Filtro por vencimento máximo (de hoje até a data escolhida)
                    df_table_new['Maturity Date'] = pd.to_datetime(df_table_new['Maturity Date'], errors='coerce')
                    df_table_new['Maturity Date'] = df_table_new['Maturity Date'].dt.date  # Corta horas, fica só data (YYYY-MM-DD)
                    selected_start_date = selected_start  # Já é date do input
                    selected_end_date = selected_end  # Já é date do input
                    df_table_new = df_table_new[
                        (df_table_new['Maturity Date'] >= selected_start_date) & 
                        (df_table_new['Maturity Date'] <= selected_end_date)
                    ].copy()  # .copy() pra evitar warnings no filtro
                    # ---------------- NOVO: Filtro de Range de Strike ----------------
                    df_table_new = df_table_new[
                        (df_table_new['Strike'] >= min_strike) & 
                        (df_table_new['Strike'] <= max_strike)
                    ].copy()

                    df_table_new = df_table_new.drop_duplicates(subset=["Opção", "Strike", "Maturity Date"])

                    st.session_state["df_table"] = df_table_new
                    st.session_state["data_cached"] = True

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                st.error("Token inválido ou expirado (401 Unauthorized).")
            else:
                st.error(f"Erro HTTP: {e}")
        except Exception as e:
            st.error(f"Erro inesperado: {e}")
        finally:
            st.session_state["already_loaded"] = True

# NOVA CORREÇÃO: Renderiza o conteúdo SEMPRE (usando dados cached se disponível)
if st.session_state["data_cached"]:
    df_stock = st.session_state["df_stock"]
    spot = st.session_state["spot"]
    df_table = st.session_state["df_table"]
    effective_ticker = st.session_state["effective_ticker_cached"]
    acao_nome = df_stock.at[0, 'Ticker'] if not df_stock.empty else effective_ticker  # Fallback para nome

    st.subheader(f"Dados de Volatilidade de: {acao_nome} ({effective_ticker})")

    cols = st.columns(6)
    
    def generate_card(title, value, bg_color=None, border_color=None, text_color="black"):
        style_bg = f"background-color:{bg_color};" if bg_color else ""
        style_border = f"border:3px solid {border_color};" if border_color else ""
        style_text = f"color:{text_color};"
        return f"""
            <div class="hover-card" style='{style_bg}{style_border}{style_text}
                        padding:15px;
                        border-radius:10px;
                        text-align:center;
                        display:flex;
                        flex-direction:column;
                        justify-content:center;
                        align-items:center;
                        min-height:140px;
                        box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
                        width:100%;'>
                <span style='font-size:16px;font-weight:bold'>{title}</span>
                <span style='font-size:24px;font-weight:bold'>{value}</span>
            </div>
        """
    
    def cor_borda_iv(valor):
        try:
            val = float(valor)
        except:
            return "gray"
        if val < 50:
            return "green"
        elif val < 70:
            return "orange"
        else:
            return "red"

    def fmt(val, patt="{:.2f}"):
        try:
            return patt.format(float(val))
        except:
            return val

    try:
        iv_percentil = float(df_stock.at[0, 'IV 1Y Percentile'])
    except:
        iv_percentil = 0.0
    try:
        iv_rank = float(df_stock.at[0, 'IV 1Y Rank'])
    except:
        iv_rank = 0.0

    spot_display = fmt(spot, "{:.2f}") if spot is not None else "N/A"
    iv_current_display = fmt(df_stock.at[0, 'IV Current']) if 'IV Current' in df_stock.columns else "N/A"
    iv_min_display = fmt(df_stock.at[0, 'IV 1Y Min']) if 'IV 1Y Min' in df_stock.columns else "N/A"
    iv_max_display = fmt(df_stock.at[0, 'IV 1Y Max']) if 'IV 1Y Max' in df_stock.columns else "N/A"

    cols[0].markdown(generate_card("Preço Atual", f"{spot_display}",
                                   bg_color="#007bff", text_color="white"), unsafe_allow_html=True)
    cols[1].markdown(generate_card("IV Atual", f"{iv_current_display}",
                                   bg_color="#ffeb3b"), unsafe_allow_html=True)
    cols[2].markdown(generate_card("IV Min", f"{iv_min_display}",
                                   border_color="#ffeb3b", text_color="#ffeb3b"), unsafe_allow_html=True)
    cols[3].markdown(generate_card("IV Max", f"{iv_max_display}",
                                   border_color="#ffeb3b", text_color="#ffeb3b"), unsafe_allow_html=True)
    cols[4].markdown(generate_card("IV Percentil", f"{iv_percentil:.2f}",
                                   border_color=cor_borda_iv(iv_percentil),
                                   text_color=cor_borda_iv(iv_percentil)), unsafe_allow_html=True)
    cols[5].markdown(generate_card("IV Rank", f"{iv_rank:.2f}",
                                   border_color=cor_borda_iv(iv_rank),
                                   text_color=cor_borda_iv(iv_rank)), unsafe_allow_html=True)

    st.markdown("<div class='block-separator'></div>", unsafe_allow_html=True)

    st.subheader(f"📋 Tabela de Opções de {effective_ticker} - com filtro")
    df_table_display = df_table.copy()  # Não precisa dropar colunas auxiliares
    
    cols_order = [
        "Opção", "Tipo", "Estilo",
        "Bid", "Ask", "Strike",
        "Maturity Date", "Moneyness",
        "Volume", "Financial Volume", "Market Maker",
        "Delta", "Gamma", "Vega", "Theta", "Rho", "IV"
    ]
    cols_order = [c for c in cols_order if c in df_table_display.columns]
    df_table_display = df_table_display[cols_order].reset_index(drop=True)
    
    def color_moneyness(val):
        colors = {"ITM": "#28a745", "ATM": "#007bff", "OTM": "#dc3545"}
        return f"background-color: {colors.get(val, '')}; color: white" if val in colors else ""
    
    # Aplica cor e centraliza a coluna "Moneyness"
    if "Moneyness" in df_table_display.columns:
        styled_table = (
            df_table_display.style
            .applymap(color_moneyness, subset=["Moneyness"])
            .set_properties(subset=["Moneyness"], **{'text-align': 'center'})
            .format({k:"{:.2f}" for k in ["Strike","Bid","Ask","IV"] if k in df_table_display.columns}, na_rep="N/A")
        )
    else:
        styled_table = df_table_display

    if {"Delta", "Gamma", "Vega", "Theta", "Rho"}.issubset(df_table_display.columns):
        for idx, row in df_table_display.iterrows():
            if any(pd.isna(row[col]) or row[col] in [None, "None"] for col in ["Delta", "Gamma", "Vega", "Theta", "Rho"]):
                # Debug opcional: remova após testar
                #st.write(f"Chamando fallback para {row['Opção']} (Tipo: {row['Tipo']}, Strike: {row['Strike']})")
                
                fallback = calcular_gregas_fallback(
                    symbol=row["Opção"],  # Ou use effective_ticker se symbol for o ativo base
                    spot=spot,
                    strike=row["Strike"],
                    maturity_date=str(row["Maturity Date"]) if pd.notna(row["Maturity Date"]) else "2025-12-19",  # Default se NaN
                    option_type=row["Tipo"],
                    token=TOKEN  # <-- CORREÇÃO: Usa a variável TOKEN do topo
                )
                
                # Debug opcional: mostra o que foi calculado
                #st.write(f"Resultado fallback: {fallback}")
                
                for col in ["Delta", "Gamma", "Vega", "Theta", "Rho", "IV"]:
                    df_table_display.at[idx, col] = fallback[col]

    # Inicializa coluna de seleção se não existir
    if 'Selecionar' not in df_table_display.columns:
        df_table_display['Selecionar'] = False
    # Carrega seleções anteriores do session_state se disponíveis
    if 'selected_options' in st.session_state:
        previous_selections = st.session_state['selected_options']
        for sel in previous_selections:
            # Encontra índice baseado em uma chave única (ex: Opção + Strike)
            key = f"{sel.get('Opção', '')}_{sel.get('Strike', '')}"
            for idx, row in df_table_display.iterrows():
                row_key = f"{row['Opção']}_{row['Strike']}"
                if row_key == key:
                    df_table_display.at[idx, 'Selecionar'] = True
                    break

    # Ordem das colunas: "Selecionar" no início
    cols_order_with_select = ["Selecionar"] + [c for c in cols_order if c != "Selecionar"]
    df_table_display = df_table_display[cols_order_with_select]

    # Config de colunas para st.data_editor
    column_config = {}
    if "Strike" in df_table_display.columns:
        column_config["Strike"] = st.column_config.NumberColumn("Strike", format="%.2f")
    if "Bid" in df_table_display.columns:
        column_config["Bid"] = st.column_config.NumberColumn("Bid", format="%.2f")
    if "Ask" in df_table_display.columns:
        column_config["Ask"] = st.column_config.NumberColumn("Ask", format="%.2f")
    if "IV" in df_table_display.columns:
        column_config["IV"] = st.column_config.NumberColumn("IV", format="%.2f")
    column_config["Selecionar"] = st.column_config.CheckboxColumn(
        "Selecionar",
        help="Marque para selecionar esta opção",
        default=False,
    )

    # Renderiza com st.data_editor
    edited_df = st.data_editor(
        df_table_display,
        column_config=column_config,
        hide_index=True,
        use_container_width=True,
        key="options_table"  # Key para persistir edições
    )

    # Processa seleções
    if edited_df is not None and not edited_df.empty:
        selected_rows = edited_df[edited_df['Selecionar'] == True]
        if not selected_rows.empty:
            st.success(f"✅ {len(selected_rows)} opções selecionadas!")
            # Salva no session_state para persistir
            st.session_state['selected_options'] = selected_rows.to_dict('records')
            # Mostra resumo das selecionadas
            summary_cols = ['Opção', 'Tipo', 'Strike', 'IV']
            selected_summary = selected_rows[summary_cols] if all(col in selected_rows.columns for col in summary_cols) else selected_rows
            st.dataframe(selected_summary, hide_index=True, use_container_width=True)
            # NOVA SEÇÃO: Botão para Gráfico de PayOff (só se há seleções)
            if not selected_rows.empty:
                if st.button("📈 Gráfico de PayOff", key="payoff_button"):
                    st.session_state['show_payoff'] = True
                else:
                    st.session_state['show_payoff'] = st.session_state.get('show_payoff', False)
            
                # Expander para mostrar/ocultar a seção de payoff
                with st.expander(f"Gráfico de PayOff para {len(selected_rows)} opções selecionadas", expanded=st.session_state.get('show_payoff', False)):
                    # Calcula payoff para as selecionadas
                    payoff_df = calculate_selected_payoff(selected_rows, spot, quantidade=100)
                    
                    if not payoff_df.empty:
                        st.subheader("Métricas Black-Scholes por Opção")
                        metrics_df = payoff_df[['Opção', 'Tipo', 'Strike', 'Premium', 'Dias Úteis', 'Vol (%)', 'Delta']].copy()
                        st.dataframe(metrics_df, use_container_width=True)

                        # NOVA SEÇÃO: Selectbox para escolher estrutura (como no BS.py)
                        estrutura = st.selectbox(
                            "Escolha a Estrutura para Simulação",
                            [
                                "Opção Simples",  # Já funciona com múltiplas
                                "Trava de Alta de Débito",
                                "Trava de Alta de Crédito",
                                "Trava de Baixa de Débito",
                                "Trava de Baixa de Crédito",
                                "Colar",
                                "Compra Sintética",
                                "Venda Sintética"
                            ],
                            key="estrutura_select"
                        )
                        
                        # Parâmetros comuns (taxa, quantidade – adaptados pro dashboard)
                        col_params1, col_params2 = st.columns(2)
                        with col_params1:
                            quantidade = st.number_input("Quantidade (por perna/opção)", value=100, step=1, key="quantidade")
                        with col_params2:
                            taxa_juros = st.number_input("Taxa de juros (%)", value=14.9, step=0.1, key="taxa_juros") / 100
                        
                        # Lógica condicional por estrutura
                        if estrutura == "Opção Simples":
                            # Já tá implementado – mostra métricas e gráficos individuais
                            st.success("Usando Opção Simples para todas selecionadas.")
                            # (Mantenha o código de métricas e gráficos de payoff aqui, como no passo 3)
                        elif estrutura == "Trava de Alta de Débito":
                            st.subheader("Selecione 2 CALLs: Baixa (Comprada) e Alta (Vendida)")
                            if len(selected_rows[selected_rows['Tipo'] == 'CALL']) >= 2:
                                calls = selected_rows[selected_rows['Tipo'] == 'CALL'].copy()
                                idx_buy = st.selectbox("CALL Comprada (Strike Baixo)", calls.index, key="buy_call")
                                idx_sell = st.selectbox("CALL Vendida (Strike Alto)", calls.index, key="sell_call", index=1)  # Evita mesmo
                                if idx_buy != idx_sell:
                                    row_buy = calls.loc[idx_buy]
                                    row_sell = calls.loc[idx_sell]
                                    # Calcula payoff combinado (como no BS.py)
                                    premium_buy = (row_buy['Bid'] + row_buy['Ask']) / 2
                                    premium_sell = (row_sell['Bid'] + row_sell['Ask']) / 2
                                    debit = (premium_buy - premium_sell) * quantidade
                                    max_profit = (row_sell['Strike'] - row_buy['Strike'] - (premium_buy - premium_sell)) * quantidade
                                    break_even = row_buy['Strike'] + (premium_buy - premium_sell)
                        
                                    st.metric("Débito Líquido", f"R$ {debit:.2f}")
                                    st.metric("Lucro Máx.", f"R$ {max_profit:.2f}")
                                    st.metric("Break-even", f"R$ {break_even:.2f}")
                        
                                    # Gráfico combinado
                                    S_range = np.linspace(spot * 0.8, spot * 1.2, 100)
                                    payoff_buy = np.maximum(S_range - row_buy['Strike'], 0) - premium_buy
                                    payoff_sell = -np.maximum(S_range - row_sell['Strike'], 0) + premium_sell
                                    payoff_total = (payoff_buy + payoff_sell) * quantidade
                        
                                    fig = go.Figure()
                                    fig.add_trace(go.Scatter(x=S_range, y=payoff_total * quantidade, mode='lines', name='Payoff Trava', line=dict(color='cyan', width=3)))
                                    fig.add_vline(x=break_even, line_dash="dash", line_color="yellow")
                                    fig.update_layout(xaxis_title="Preço Ativo (R$)", yaxis_title="Payoff (R$)", template='plotly_dark')
                                    st.plotly_chart(fig, use_container_width=True)
                                else:
                                    st.warning("Escolha strikes diferentes!")
                            else:
                                st.warning("Selecione pelo menos 2 CALLs na tabela principal.")
                        # Repita o padrão para outras estruturas (ex: "Trava de Baixa" usa PUTs, "Colar" usa PUT + CALL, etc.)
                        # Por enquanto, adicione um placeholder para as outras:
                        else:
                            st.info(f"Estrutura '{estrutura}' em desenvolvimento – payoff simples por opção por enquanto.")
                            # (Mantenha os gráficos individuais aqui como fallback)
                    
                        st.subheader("📊 Gráficos de PayOff")
                        for _, row in payoff_df.iterrows():
                            S_range = np.linspace(spot * 0.8, spot * 1.2, 100)
                            payoff_total = row['Payoff Total']
                    
                            fig = go.Figure()
                            fig.add_trace(go.Scatter(
                                x=S_range,
                                y=payoff_total,
                                mode='lines',
                                name=f'PayOff {row["Opção"]}',
                                line=dict(color='cyan', width=3),
                                hovertemplate="Preço do Ativo: R$ %{x:.2f}<br>PayOff: R$ %{y:.2f}<extra></extra>"
                            ))
                            fig.add_trace(go.Scatter(
                                x=S_range, 
                                y=np.zeros_like(S_range),
                                mode='lines',
                                name='Linha Zero',
                                line=dict(color='white', width=1, dash='dash')
                            ))
                            fig.update_layout(
                                xaxis_title="Preço do Ativo (R$)",
                                yaxis_title="PayOff Total (R$)",
                                plot_bgcolor='black',
                                paper_bgcolor='black',
                                font=dict(color='white'),
                                title=f"PayOff para {row['Opção']} ({row['Tipo']})",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                            )
                            st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.warning("Nenhuma opção válida para calcular payoff.")
                    # Placeholder: Vamos integrar o BS.py aqui no passo 3
        else:
            if 'selected_options' in st.session_state:
                del st.session_state['selected_options']
            st.info("Nenhuma opção selecionada ainda. Marque os checkboxes para selecionar!")
    else:
        st.info("Nenhuma opção disponível para seleção.")
          
    st.markdown("<div class='block-separator'></div>", unsafe_allow_html=True)

    st.subheader(f"Concentração de Strikes no período filtrado - {effective_ticker}")
    df_plot = df_table.drop_duplicates(subset=['Opção']).copy()
    df_plot['Maturity Date'] = pd.to_datetime(df_plot['Maturity Date'], errors='coerce')

    fig = px.scatter(
        df_plot,
        x='Strike',
        y='Maturity Date',
        size='Financial Volume' if 'Financial Volume' in df_plot.columns else None,
        color='Tipo' if 'Tipo' in df_plot.columns else None,
        hover_data={
            'Opção': True,
            'Tipo': True,
            'Strike': ':.2f',
            'Maturity Date': True,
            'IV': ':.2f',
            'Delta': ':.4f',
            'Gamma': ':.4f',
            'Theta': ':.4f',
            'Vega': ':.4f',
            'Rho': ':.4f',
            'Financial Volume': ':.2f'
        },
        color_discrete_map={'CALL':'#28a745','PUT':'#dc3545'},
        size_max=40,
        opacity=0.8,
        template='plotly_dark'
    )

    fig.update_layout(
        xaxis_title='Strike (R$)',
        yaxis_title='Vencimento',
        legend_title='Tipo',
        hovermode='closest'
    )

    st.plotly_chart(fig, use_container_width=True)

    # ---------------- Resumo com Cards ----------------
    st.subheader("Resumo do Período Filtrado")
    df_calls = df_plot[df_plot['Tipo'].str.upper()=='CALL'] if 'Tipo' in df_plot.columns else pd.DataFrame()
    df_puts = df_plot[df_plot['Tipo'].str.upper()=='PUT'] if 'Tipo' in df_plot.columns else pd.DataFrame()

    total_calls = df_calls['Financial Volume'].sum() if 'Financial Volume' in df_calls.columns else 0
    total_puts = df_puts['Financial Volume'].sum() if 'Financial Volume' in df_puts.columns else 0

    if 'IV' in df_plot.columns and not df_plot.empty:
        most_volatile = df_plot.loc[df_plot['IV'].idxmax()]
        most_volatile_name = most_volatile['Opção']
        most_volatile_iv = most_volatile['IV']
    else:
        most_volatile_name = "N/A"
        most_volatile_iv = 0.0

    # Cálculo do Strike mais negociado
    if 'Financial Volume' in df_plot.columns and not df_plot.empty:
        strike_volumes = df_plot.groupby('Strike')['Financial Volume'].sum()
        most_traded_strike = strike_volumes.idxmax() if not strike_volumes.empty else "N/A"
        most_traded_volume = strike_volumes.max() if not strike_volumes.empty else 0.0
    else:
        most_traded_strike = "N/A"
        most_traded_volume = 0.0

    cols_resumo = st.columns(4)

    cols_resumo[0].markdown(f"""
        <div style='
            background-color:#28a745;
            padding:15px;
            border-radius:10px;
            text-align:center;
            color:white;
            box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
            min-height:140px;
        '>
            <div style='font-size:16px;font-weight:bold'>Total de Calls Negociadas</div>
            <div style='font-size:18px;font-weight:bold;margin-top:10px'>R$ {total_calls:,.2f}</div>
        </div>
    """, unsafe_allow_html=True)

    cols_resumo[1].markdown(f"""
        <div style='
            background-color:#dc3545;
            padding:15px;
            border-radius:10px;
            text-align:center;
            color:white;
            box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
            min-height:140px;
        '>
            <div style='font-size:16px;font-weight:bold'>Total de Puts Negociadas</div>
            <div style='font-size:18px;font-weight:bold;margin-top:10px'>R$ {total_puts:,.2f}</div>
        </div>
    """, unsafe_allow_html=True)

    cols_resumo[2].markdown(f"""
        <div style='
            background-color:#ff8c00;
            padding:15px;
            border-radius:10px;
            text-align:center;
            color:white;
            box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
            min-height:140px;
        '>
            <div style='font-size:16px;font-weight:bold'>Opção mais Volátil</div>
            <div style='font-size:14px;margin-top:5px'>{most_volatile_name}</div>
            <div style='font-size:18px;font-weight:bold;margin-top:10px'>IV: {most_volatile_iv:.2f}%</div>
        </div>
    """, unsafe_allow_html=True)

    cols_resumo[3].markdown(f"""
        <div style='
            background-color:#007bff;
            padding:15px;
            border-radius:10px;
            text-align:center;
            color:white;
            box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
            min-height:140px;
        '>
            <div style='font-size:16px;font-weight:bold'>Strike mais Negociado</div>
            <div style='font-size:14px;margin-top:5px'>Strike: R$ {most_traded_strike:,.2f}</div>
            <div style='font-size:18px;font-weight:bold;margin-top:10px'>R$ {most_traded_volume:,.2f}</div>
        </div>
    """, unsafe_allow_html=True)

    if total_calls + total_puts == 0:
        sentiment = "Não há volume suficiente para identificar tendência."
    elif total_calls > total_puts:
        sentiment = "Tendência de Alta: volume de CALLs predominante."
    else:
        sentiment = "Tendência de Baixa: volume de PUTs predominante."

    st.markdown("<div class='block-separator'></div>", unsafe_allow_html=True)

    st.subheader(f"🏆 Top 5 Global - Opções mais negociadas de {effective_ticker}")
    df_top5_unique = df_options.drop_duplicates(subset=["Opção", "Strike", "Maturity Date"]) if 'df_options' in locals() else df_table
    df_top5 = df_top5_unique.sort_values("Financial Volume", ascending=False).head(5)
    cols_top5 = st.columns(len(df_top5)) if len(df_top5)>0 else []

    for i, (_, row) in enumerate(df_top5.iterrows()):
        tipo = row['Tipo'].upper()
        color = "#28a745" if tipo == "CALL" else "#dc3545"
        cols_top5[i].markdown(f"""
            <div style='
                background-color:{color};
                padding:15px;
                border-radius:10px;
                text-align:center;
                color:white;
                box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
                min-height:140px;
            '>
                <div style='font-size:16px;font-weight:bold'>{row['Opção']} ({tipo})</div>
                <div style='font-size:14px;margin-top:5px'>Vencimento: {row['Maturity Date']}</div>
                <div style='font-size:14px;margin-top:5px'>Strike: R$ {row['Strike']:,.2f}</div>
                <div style='font-size:18px;font-weight:bold;margin-top:10px'>R$ {row['Financial Volume']:,.2f}</div>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<div class='block-separator'></div>", unsafe_allow_html=True)    

    st.subheader(f"Indicador Global de Tendência – {effective_ticker}")
    df_options_local = df_table  # Fallback se df_options não estiver no scope
    df_near = df_options_local[(df_options_local['Strike'] >= 0.9*spot) & (df_options_local['Strike'] <= 1.1*spot)].copy()
    
    if not df_near.empty:
        df_near['Peso'] = df_near['Financial Volume'] * (1 - abs(df_near['Strike'] - spot)/spot)
        
        # Soma de volume financeiro ponderado por tipo
        call_signal = df_near[df_near['Tipo'] == 'CALL']['Peso'].sum()
        put_signal = df_near[df_near['Tipo'] == 'PUT']['Peso'].sum()
        
        # =========================
        # Cálculo do Gauge Value (mantido igual)
        # =========================
        gauge_value = 0
        if call_signal + put_signal > 0:
            gauge_value = (call_signal - put_signal) / (call_signal + put_signal) * 100
        
        # =========================
        # Sentimento com Força (atualizado para range Neutro de -5% a +5%)
        # =========================
        if abs(gauge_value) <= 5:
            strength = "Neutro"
            principal_tipo = None
        elif gauge_value >= 50:
            strength = "Forte Alta"
            principal_tipo = "CALL"
        elif gauge_value > 5:
            strength = "Alta"
            principal_tipo = "CALL"
        elif gauge_value <= -50:
            strength = "Forte Baixa"
            principal_tipo = "PUT"
        else:  # gauge_value < -5
            strength = "Baixa"
            principal_tipo = "PUT"
        
        # =========================
        # Percentuais de moneyness dentro do tipo com maior volume (adaptado para Neutro)
        # =========================
        moneyness_perc = {}
        if strength == "Neutro":
            # Para Neutro, combina CALLs + PUTs
            df_tipo = df_near.copy()
        else:
            df_tipo = df_near[df_near['Tipo'] == principal_tipo]
        
        total_volume_tipo = df_tipo['Financial Volume'].sum() if not df_tipo.empty else 0
        if total_volume_tipo > 0:
            moneyness_perc = (df_tipo.groupby('Moneyness')['Financial Volume'].sum() / total_volume_tipo * 100).to_dict()
        moneyness_secundaria = max(moneyness_perc, key=moneyness_perc.get) if moneyness_perc else "Misto"
        
        # =========================
        # Dicionário de Textos da Legenda (mapeia strength + moneyness para texto exato)
        # =========================
        legend_texts = {
            # Para Alta/Forte Alta (CALLs)
            ("Alta", "ITM"): "alta moderada consolidada",
            ("Alta", "ATM"): "visão bullish equilibrada",
            ("Alta", "OTM"): "upside especulativo",
            ("Forte Alta", "ITM"): "alta dominante consolidada",
            ("Forte Alta", "ATM"): "visão bullish consolidada",
            ("Forte Alta", "OTM"): "upside agressivo",
            
            # Para Baixa/Forte Baixa (PUTs)
            ("Baixa", "ITM"): "hedge defensivo moderado",
            ("Baixa", "ATM"): "visão bearish equilibrada",
            ("Baixa", "OTM"): "downside especulativo",
            ("Forte Baixa", "ITM"): "hedge defensivo consolidado",
            ("Forte Baixa", "ATM"): "visão bearish consolidada",
            ("Forte Baixa", "OTM"): "downside agressivo",
            
            # Para Neutro (fallback genérico se "Misto")
            ("Neutro", "Misto"): "indecisão geral no mercado."
        }
        
        # Puxa o texto da legenda (fallback se não exato)
        key = (strength, moneyness_secundaria)
        texto_legenda = legend_texts.get(key, "interpretação mista no mercado.")
        
        # Para Neutro, prepend o texto base da legenda + sub para moneyness
        if strength == "Neutro":
            neutro_base = {
                "ITM": "indecisão",
                "ATM": "consolidação lateral",
                "OTM": "indecisão"
            }
            sub_neutro = neutro_base.get(moneyness_secundaria, "equilíbrio geral")
            texto_legenda = f"Equilíbrio ({sub_neutro}). {texto_legenda}"
        else:
            texto_legenda = f"{texto_legenda}."
        
        # =========================
        # Gráfico do Gauge (ponteiro sempre azul)
        # =========================
        import plotly.graph_objects as go
    
        # Ponteiro sempre azul, como no original
        bar_color = "darkblue"

        fig = go.Figure(go.Indicator(
            mode = "gauge+number+delta",
            value = gauge_value,
            number={'suffix': '%'},
            title = {'text': "Sentimento do Mercado"},
            delta = {'reference': 0, 'increasing': {'color': "green"}, 'decreasing': {'color': "red"}},
            gauge = {
                'axis': {'range': [-100, 100]},
                'bar': {'color': bar_color},
                'steps' : [
                    {'range': [-100, -50], 'color': 'red'},
                    {'range': [-50, -5], 'color': 'lightcoral'},
                    {'range': [-5, 5], 'color': '#D3D3D3'},  # Cinza para Neutro
                    {'range': [5, 50], 'color': 'lightgreen'},
                    {'range': [50, 100], 'color': 'green'}
                ],
            }
        ))
    
        st.plotly_chart(fig, use_container_width=True)
    
        # =========================
        # Cards de PUTs e CALLs (PUTs à esquerda, CALLs à direita)
        # =========================
        total_calls = df_near[df_near['Tipo'] == 'CALL']['Financial Volume'].sum()
        total_puts = df_near[df_near['Tipo'] == 'PUT']['Financial Volume'].sum()
        
        # Percentuais de moneyness
        calls_moneyness = (df_near[df_near['Tipo'] == 'CALL'].groupby('Moneyness')['Financial Volume'].sum() / total_calls * 100).to_dict() if total_calls else {}
        puts_moneyness = (df_near[df_near['Tipo'] == 'PUT'].groupby('Moneyness')['Financial Volume'].sum() / total_puts * 100).to_dict() if total_puts else {}
        
        col_left, col_right = st.columns(2)  # left = PUTs, right = CALLs
        
        # Card PUTs (à esquerda)
        col_left.markdown(f"""
            <div style='background-color:#dc3545; color:white; padding:15px; border-radius:10px; text-align:center; box-shadow:2px 2px 6px rgba(0,0,0,0.2);'>
                <div style='font-size:16px; font-weight:bold;'>Total de PUTs Negociadas</div>
                <div style='font-size:14px; margin-top:5px;'>R$ {total_puts:,.0f}</div>
                <div style='font-size:13px; margin-top:8px;'>
                    ITM: {puts_moneyness.get('ITM',0):.1f}% | ATM: {puts_moneyness.get('ATM',0):.1f}% | OTM: {puts_moneyness.get('OTM',0):.1f}%
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        # Card CALLs (à direita)
        col_right.markdown(f"""
            <div style='background-color:#28a745; color:white; padding:15px; border-radius:10px; text-align:center; box-shadow:2px 2px 6px rgba(0,0,0,0.2);'>
                <div style='font-size:16px; font-weight:bold;'>Total de CALLs Negociadas</div>
                <div style='font-size:14px; margin-top:5px;'>R$ {total_calls:,.0f}</div>
                <div style='font-size:13px; margin-top:8px;'>
                    ITM: {calls_moneyness.get('ITM',0):.1f}% | ATM: {calls_moneyness.get('ATM',0):.1f}% | OTM: {calls_moneyness.get('OTM',0):.1f}%
                </div>
            </div>
        """, unsafe_allow_html=True)
    
        st.markdown("<div style='margin-top:20px; height:10px;'></div>", unsafe_allow_html=True)
        
        # =========================
        # Card de Sentimento (sem bold, tudo em maiúsculas)
        # =========================
        if principal_tipo:
            mensagem = f"SENTIMENTO DE {strength.upper()} - PREDOMINÂNCIA DE {principal_tipo.upper()}S {moneyness_secundaria.upper()} - {texto_legenda.upper()}"
        else:
            mensagem = f"SENTIMENTO DE {strength.upper()} - PREDOMINÂNCIA DE MONEYNESS {moneyness_secundaria.upper()} - {texto_legenda.upper()}"
        
        if strength in ["Alta", "Forte Alta"]:
            st.markdown(f"""
            <div style='
                background-color: #d4edda; 
                border: 1px solid #c3e6cb; 
                border-radius: 8px; 
                padding: 16px; 
                margin: 10px 0; 
                font-size: 18px;  
                font-weight: bold; 
                color: #155724; 
                text-align: center;
                text-transform: uppercase;
            '>
                {mensagem}
            </div>
            """, unsafe_allow_html=True)
        
        elif strength in ["Baixa", "Forte Baixa"]:
            st.markdown(f"""
            <div style='
                background-color: #f8d7da; 
                border: 1px solid #f5c6cb; 
                border-radius: 8px; 
                padding: 16px; 
                margin: 10px 0; 
                font-size: 18px;  
                font-weight: bold; 
                color: #721c24; 
                text-align: center;
                text-transform: uppercase;
            '>
                {mensagem}
            </div>
            """, unsafe_allow_html=True)
        
        else:  # Neutro
            st.markdown(f"""
            <div style='
                background-color: #d1ecf1; 
                border: 1px solid #bee5eb; 
                border-radius: 8px; 
                padding: 16px; 
                margin: 10px 0; 
                font-size: 18px;  
                font-weight: bold; 
                color: #0c5460; 
                text-align: center;
                text-transform: uppercase;
            '>
                {mensagem}
            </div>
            """, unsafe_allow_html=True)
    
        # =========================
        # Legenda (mantida igual)
        # =========================
        st.markdown("""
        <div style='margin-top:20px;'>
            <h4>Legenda do Indicador</h4>
            <div style='display:flex; gap:10px; flex-wrap:wrap;'>
                <div style='background-color:#D3D3D3; width:30px; height:20px; border-radius:4px;'></div>
                <span><b>Neutra</b>: Equilíbrio (ITM: indecisão; ATM: consolidação lateral; OTM: indecisão).</span>
            </div>
            <div style='display:flex; gap:10px; flex-wrap:wrap; margin-top:5px;'>
                <div style='background-color:red; width:30px; height:20px; border-radius:4px;'></div>
                <span><b>Forte Baixa</b>: Puts dominantes (ITM: hedge defensivo consolidado; ATM: visão bearish consolidada; OTM: downside agressivo).</span>
            </div>
            <div style='display:flex; gap:10px; flex-wrap:wrap; margin-top:5px;'>
                <div style='background-color:lightcoral; width:30px; height:20px; border-radius:4px;'></div>
                <span><b>Baixa</b>: Puts moderados (ITM: hedge defensivo moderado; ATM: visão bearish equilibrada; OTM: downside especulativo).</span>
            </div>
            <div style='display:flex; gap:10px; flex-wrap:wrap; margin-top:5px;'>
                <div style='background-color:lightgreen; width:30px; height:20px; border-radius:4px;'></div>
                <span><b>Alta</b>: Calls moderados (ITM: alta moderada consolidada; ATM: visão bullish equilibrada; OTM: upside especulativo).</span>
            </div>
            <div style='display:flex; gap:10px; flex-wrap:wrap; margin-top:5px;'>
                <div style='background-color:green; width:30px; height:20px; border-radius:4px;'></div>
                <span><b>Forte Alta</b>: Calls dominantes (ITM: alta dominante consolidada; ATM: visão bullish consolidada; OTM: upside agressivo).</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    else:
        st.info("Não há opções próximas do preço atual para calcular a tendência.")
else:
    st.info("Clique em 'Buscar Opções' ou mude o ticker para carregar os dados.")

