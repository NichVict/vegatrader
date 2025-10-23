# CURTO.PY - ENVIO DE ORDENS (versão nuvem: curto_przo_v2)
# -*- coding: utf-8 -*-

import streamlit as st
from yahooquery import Ticker
import datetime
import requests
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import re
import json
import time
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components

# =============================
# CONFIGURAÇÕES GERAIS
# =============================
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA (Nuvem v2)", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(3, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 59, 0)
INTERVALO_VERIFICACAO = 180
TEMPO_ACUMULADO_MAXIMO = 480
LOG_MAX_LINHAS = 1000
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# =============================
# FUNÇÕES AUXILIARES DE NUVEM
# =============================

def agora_lx():
    return datetime.datetime.now(TZ)

def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

def carregar_estado_nuvem():
    """Lê o estado completo (v) da linha curto_przo_v1."""
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        if r.status_code == 200 and r.json():
            return r.json()[0]["v"]
        else:
            st.warning(f"⚠️ Nenhum estado remoto encontrado ({r.status_code})")
            return {}
    except Exception as e:
        st.error(f"Erro ao carregar estado remoto: {e}")
        return {}

def salvar_estado_nuvem(estado: dict):
    """Substitui o campo v inteiro na nuvem."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
        r = requests.patch(url, headers=headers(),
                           data=json.dumps({"v": estado}), timeout=15)
        if r.status_code not in (200, 204):
            st.warning(f"⚠️ Falha ao salvar estado: {r.status_code} - {r.text}")
    except Exception as e:
        st.error(f"⚠️ Erro ao salvar estado: {e}")

def dentro_pregao(dt):
    t = dt.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_abertura(dt):
    abre = dt.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    fecha = dt.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt < abre:
        return int((abre - dt).total_seconds()), abre
    elif dt > fecha:
        prox = abre + datetime.timedelta(days=1)
        return int((prox - dt).total_seconds()), prox
    else:
        return 0, abre

def obter_preco_atual(ticker_symbol):
    """Retorna o preço atual via YahooQuery."""
    try:
        tk = Ticker(ticker_symbol)
        p = tk.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
        preco_atual = tk.history(period="1d")["close"].iloc[-1]
        return float(preco_atual)
    except Exception:
        return "-"

# =============================
# CARGA INICIAL DO ESTADO
# =============================
estado = carregar_estado_nuvem()

def garantir_chave(k, default):
    if k not in estado or estado[k] is None:
        estado[k] = default
garantir_chave("ativos", [])
garantir_chave("tempo_acumulado", {})
garantir_chave("em_contagem", {})
garantir_chave("status", {})
garantir_chave("historico_alertas", [])
garantir_chave("log_monitoramento", [])
garantir_chave("precos_historicos", {})
garantir_chave("disparos", {})
garantir_chave("ultima_data_abertura_enviada", None)

# =============================
# FUNÇÕES DE NEGÓCIO
# =============================
def registrar_log(msg: str):
    """Adiciona linha de log ao estado e salva."""
    ts = agora_lx().strftime("%H:%M:%S")
    linha = f"{ts} | {msg}"
    estado["log_monitoramento"].append(linha)
    if len(estado["log_monitoramento"]) > LOG_MAX_LINHAS:
        estado["log_monitoramento"] = estado["log_monitoramento"][-LOG_MAX_LINHAS:]
    salvar_estado_nuvem(estado)

def notificar_abertura_pregao_uma_vez_por_dia():
    now = agora_lx()
    data_atual = str(now.date())
    if estado.get("ultima_data_abertura_enviada") == data_atual:
        return
    registrar_log("📣 Pregão Aberto (log, sem envio)")
    estado["ultima_data_abertura_enviada"] = data_atual
    salvar_estado_nuvem(estado)

def notificar_preco_alvo_alcancado_curto(ticker, preco_alvo, preco_atual, operacao):
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    tk_sem_ext = ticker.replace(".SA", "")
    registrar_log(f"🚀 ALERTA (simulado) {msg_op} em {tk_sem_ext} | alvo {preco_alvo:.2f} | atual {preco_atual:.2f}")

# =============================
# INTERFACE
# =============================
st.title("📈 CURTO PRAZO - COMPRA E VENDA (Robô na Nuvem v2)")

now = agora_lx()
st.caption(f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")

st.sidebar.header("⚙️ Configurações")
# =============================
# SIDEBAR E CONTROLES
# =============================

if st.sidebar.button("🧹 Limpar Tabela"):
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
        requests.patch(url, headers=headers(), data=json.dumps({"v": {}}), timeout=10)
        st.sidebar.success("✅ Linha da nuvem zerada com sucesso.")
        estado.clear()
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao limpar tabela: {e}")

st.sidebar.header("📜 Histórico de Alertas")

if estado["historico_alertas"]:
    for alerta in reversed(estado["historico_alertas"]):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")

if st.sidebar.button("🧹 Limpar Histórico"):
    estado["historico_alertas"] = []
    salvar_estado_nuvem(estado)
    st.sidebar.success("Histórico limpo!")

if st.sidebar.button("🧹 Limpar Log"):
    estado["log_monitoramento"] = []
    salvar_estado_nuvem(estado)
    st.sidebar.success("Log limpo!")

if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    estado["disparos"] = {}
    ativos_atuais = {a["ticker"] for a in estado["ativos"]}
    estado["precos_historicos"] = {t: dados for t, dados in estado["precos_historicos"].items() if t in ativos_atuais}
    salvar_estado_nuvem(estado)
    st.sidebar.success("Marcadores e históricos antigos limpos!")

tickers_existentes = sorted(set(a["ticker"] for a in estado["ativos"])) if estado["ativos"] else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# =============================
# ADIÇÃO DE ATIVO
# =============================

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if not ticker:
        st.error("Digite um ticker válido.")
    else:
        ativo = {"ticker": ticker, "operacao": operacao, "preco": preco}
        estado["ativos"].append(ativo)
        estado["tempo_acumulado"][ticker] = 0
        estado["em_contagem"][ticker] = False
        estado["status"][ticker] = "🟢 Monitorando"
        estado["precos_historicos"][ticker] = []
        try:
            preco_inicial = obter_preco_atual(f"{ticker}.SA")
            if preco_inicial != "-":
                estado["precos_historicos"][ticker].append((agora_lx().isoformat(), preco_inicial))
                st.success(f"Ativo {ticker} adicionado com preço inicial R$ {preco_inicial:.2f}.")
            else:
                st.warning(f"Ativo {ticker} adicionado, sem preço inicial.")
        except Exception as e:
            st.error(f"Erro ao coletar preço de {ticker}: {e}")
        salvar_estado_nuvem(estado)

# =============================
# STATUS DOS ATIVOS
# =============================
st.subheader("📊 Status dos Ativos Monitorados")

if estado["ativos"]:
    data = []
    for ativo in estado["ativos"]:
        t = ativo["ticker"]
        preco_atual = "-"
        try:
            preco_atual = obter_preco_atual(f"{t}.SA")
        except Exception:
            pass
        tempo = estado["tempo_acumulado"].get(t, 0)
        minutos = tempo / 60
        data.append({
            "Ticker": t,
            "Operação": ativo["operacao"].upper(),
            "Preço Alvo": f"R$ {ativo['preco']:.2f}",
            "Preço Atual": f"R$ {preco_atual:.2f}" if preco_atual != "-" else "-",
            "Status": estado["status"].get(t, "🟢 Monitorando"),
            "Tempo Acumulado": f"{int(minutos)} min"
        })
    st.dataframe(pd.DataFrame(data), use_container_width=True, height=220)
else:
    st.info("Nenhum ativo monitorado no momento.")

# =============================
# LOOP PRINCIPAL DE MONITORAMENTO
# =============================
if dentro_pregao(now):
    notificar_abertura_pregao_uma_vez_por_dia()

    tickers_para_remover = []
    for ativo in estado["ativos"]:
        t = ativo["ticker"]
        preco_alvo = ativo["preco"]
        operacao = ativo["operacao"]
        tk_full = f"{t}.SA"
        preco_atual = obter_preco_atual(tk_full)

        registrar_log(f"{tk_full}: R$ {preco_atual:.2f}")

        condicao = (
            (operacao == "compra" and preco_atual >= preco_alvo)
            or (operacao == "venda" and preco_atual <= preco_alvo)
        )

        if condicao:
            estado["status"][t] = "🟡 Em contagem"
            estado["tempo_acumulado"][t] = estado["tempo_acumulado"].get(t, 0) + INTERVALO_VERIFICACAO
            registrar_log(f"⌛ {t}: {estado['tempo_acumulado'][t]}s acumulados")

            if estado["tempo_acumulado"][t] >= TEMPO_ACUMULADO_MAXIMO and estado["status"][t] != "🚀 Disparado":
                estado["status"][t] = "🚀 Disparado"
                notificar_preco_alvo_alcancado_curto(tk_full, preco_alvo, preco_atual, operacao)
                estado["historico_alertas"].append({
                    "hora": agora_lx().strftime("%Y-%m-%d %H:%M:%S"),
                    "ticker": t,
                    "operacao": operacao,
                    "preco_alvo": preco_alvo,
                    "preco_atual": preco_atual
                })
                estado["disparos"].setdefault(t, []).append((agora_lx().isoformat(), preco_atual))
                tickers_para_remover.append(t)
                salvar_estado_nuvem(estado)
        else:
            if estado["em_contagem"].get(t):
                estado["em_contagem"][t] = False
                estado["tempo_acumulado"][t] = 0
                estado["status"][t] = "🔴 Fora da zona"
                registrar_log(f"❌ {t} saiu da zona de preço alvo.")

    if tickers_para_remover:
        estado["ativos"] = [a for a in estado["ativos"] if a["ticker"] not in tickers_para_remover]
        for t in tickers_para_remover:
            estado["tempo_acumulado"].pop(t, None)
            estado["em_contagem"].pop(t, None)
            estado["status"][t] = "✅ Ativado (removido)"
        registrar_log(f"🧹 Removidos após ativação: {', '.join(tickers_para_remover)}")
        salvar_estado_nuvem(estado)
else:
    faltam, prox_abertura = segundos_ate_abertura(now)
    components.html(f"""
    <div style="background:#0b1220;border:1px solid #1f2937;
         border-radius:10px;padding:12px;margin-top:10px;
         color:white;">
        ⏸️ Pregão fechado. Reabre em 
        <b style="color:#60a5fa;">{datetime.timedelta(seconds=faltam)}</b>
        (às <span style="color:#60a5fa;">{prox_abertura.strftime('%H:%M')}</span>).
    </div>""", height=70)

# =============================
# GRÁFICO
# =============================
st.subheader("📉 Evolução dos Preços")

fig = go.Figure()
for t, dados in estado["precos_historicos"].items():
    if len(dados) > 0:
        xs, ys = zip(*[(datetime.datetime.fromisoformat(dt), p) for dt, p in dados])
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t))
for t, pontos in estado["disparos"].items():
    if pontos:
        xs, ys = zip(*[(datetime.datetime.fromisoformat(dt), p) for dt, p in pontos])
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name=f"Ativação {t}",
                                 marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))))
fig.update_layout(title="📈 Histórico de preços", template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# =============================
# LOG (NUVEM)
# =============================
st.subheader("☁️ Log do Robô (Nuvem)")

def extract_ticker(line):
    m = re.search(r"\b([A-Z0-9]{4,6})\.SA\b", line)
    if m:
        return m.group(1)
    m2 = re.search(r"\b([A-Z0-9]{4,6})\b", line)
    return m2.group(1) if m2 else None

def render_log_html(lines, selected_tickers=None, max_lines=250):
    if not lines:
        st.info("Nenhum log ainda.")
        return
    subset = lines[-max_lines:][::-1]
    if selected_tickers:
        subset = [l for l in subset if extract_ticker(l) in selected_tickers]
    css = """
    <style>
      .log-card {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 10px;
        padding: 10px 12px;
        max-height: 360px;
        overflow-y: auto;
      }
      .log-line {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px;
        line-height: 1.35;
        margin: 2px 0;
        color: #e5e7eb;
      }
    </style>
    """
    html = [css, "<div class='log-card'>"]
    for l in subset:
        html.append(f"<div class='log-line'>{l}</div>")
    html.append("</div>")
    st.markdown("\n".join(html), unsafe_allow_html=True)

render_log_html(estado["log_monitoramento"], selected_tickers, 250)

# =============================
# AUTOREFRESH
# =============================
st_autorefresh(interval=300_000, limit=None, key="curto-refresh")
