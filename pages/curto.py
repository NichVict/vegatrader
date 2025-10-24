# CURTO.PY - LEITURA DO ESTADO DA NUVEM (SUPABASE)
# -*- coding: utf-8 -*-
import streamlit as st
import datetime
import requests
from telegram import Bot
import asyncio
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import re
import os
import json
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")
TZ = ZoneInfo("Europe/Lisbon")

SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"
LOCAL_STATE_FILE = "session_data/state_curto.json"

LOG_MAX_LINHAS = 1000
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# FUNÇÕES BÁSICAS
# -----------------------------
def agora_lx():
    return datetime.datetime.now(TZ)

def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def color_for_ticker(ticker):
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\.SA\b")
PLAIN_TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\b")

def extract_ticker(line):
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    m2 = PLAIN_TICKER_PAT.search(line)
    return m2.group(1) if m2 else None

def inicializar_estado():
    defaults = {
        "ativos": [], "historico_alertas": [], "log_monitoramento": [],
        "precos_historicos": {}, "disparos": {}, "status": {},
        "origem_estado": "❓", "__carregado_ok__": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    ensure_color_map()

def carregar_estado_nuvem():
    """Lê o JSONB do Supabase."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    origem = "❌ Nenhum"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            if isinstance(estado, str):
                estado = json.loads(estado)
            for k, v in estado.items():
                st.session_state[k] = v
            origem = "☁️ Supabase"
            st.sidebar.success("✅ Estado carregado da nuvem.")
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto encontrado.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")

    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem == "☁️ Supabase")

# -----------------------------
# INICIALIZAÇÃO
# -----------------------------
inicializar_estado()
carregar_estado_nuvem()

# -----------------------------
# SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_curto", "")
    async def teste_tg():
        try:
            if tok and chat:
                bot = Bot(token=tok)
                await bot.send_message(chat_id=chat, text="✅ Teste de alerta CURTO PRAZO funcionando!")
                st.sidebar.success("✅ Mensagem enviada com sucesso!")
            else:
                st.sidebar.warning("Token/chat_id não configurado.")
        except Exception as e:
            st.sidebar.error(f"Erro Telegram: {e}")
    asyncio.run(teste_tg())

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(
            f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}"
        )
else:
    st.sidebar.info("Nenhum alerta ainda.")

if st.sidebar.button("🔄 Atualizar Agora"):
    carregar_estado_nuvem()
    st.sidebar.success("Dados atualizados da nuvem!")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CURTO PRAZO - COMPRA E VENDA")

origem = st.session_state.get("origem_estado", "❓")
st.markdown({
    "☁️ Supabase": "🟢 **Origem dos dados:** Nuvem (Supabase)",
    "📁 Local": "🟠 **Origem dos dados:** Local",
}.get(origem, "⚪ **Origem dos dados:** Desconhecida"))
st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

st.write("Robô automático da **CARTEIRA CURTO PRAZO** — leitura da nuvem (somente visualização).")

# -----------------------------
# STATUS DOS ATIVOS
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()
data = []
for ativo in st.session_state.get("ativos", []):
    t = ativo.get("ticker", "")
    preco_alvo = ativo.get("preco", 0)
    operacao = ativo.get("operacao", "").upper()
    status_txt = st.session_state.get("status", {}).get(t, "🟢 Monitorando")
    data.append({
        "Ticker": t,
        "Operação": operacao,
        "Preço Alvo": f"R$ {preco_alvo:.2f}",
        "Status": status_txt
    })
if data:
    tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)
else:
    tabela_status.info("Nenhum ativo registrado.")

# -----------------------------
# GRÁFICO
# -----------------------------
st.subheader("📉 Evolução dos Preços")
fig = go.Figure()
precos_hist = st.session_state.get("precos_historicos", {})
historico_alertas = st.session_state.get("historico_alertas", [])
disparos = st.session_state.get("disparos", {})

if precos_hist:
    for t, dados in precos_hist.items():
        if not dados:
            continue
        xs, ys = zip(*[(datetime.datetime.fromisoformat(dtv) if isinstance(dtv, str) else dtv, pv) for dtv, pv in dados])
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t))
elif historico_alertas:
    df = pd.DataFrame(historico_alertas)
    df["hora"] = pd.to_datetime(df["hora"], errors="coerce")
    for tkr, df_tkr in df.groupby("ticker"):
        fig.add_trace(go.Scatter(
            x=df_tkr["hora"], y=df_tkr["preco_atual"],
            mode="lines+markers", name=f"{tkr} (alertas)",
            line=dict(dash="dot")
        ))
for t, pontos in disparos.items():
    if pontos:
        xs, ys = zip(*pontos)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers", name=f"Ativação {t}",
            marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))
        ))
fig.update_layout(title="📉 Evolução dos Preços", template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG DE MONITORAMENTO
# -----------------------------
st.subheader("🕒 Monitoramento (Logs e Alertas)")
log_lines = st.session_state.get("log_monitoramento", [])
if not log_lines and historico_alertas:
    for h in historico_alertas:
        log_lines.append(
            f"{h['hora']} | ALERTA {h['operacao'].upper()} | {h['ticker']} "
            f"alvo R$ {h['preco_alvo']:.2f} | atual R$ {h['preco_atual']:.2f}"
        )

def render_log_html(lines, max_lines=250):
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]
    css = """
    <style>
      .log-card {background:#0b1220;border:1px solid #1f2937;border-radius:10px;
        padding:10px 12px;max-height:360px;overflow-y:auto;}
      .log-line {font-family:ui-monospace, Menlo, Monaco, Consolas;
        font-size:13px;line-height:1.35;margin:2px 0;color:#e5e7eb;
        display:flex;align-items:baseline;gap:8px;}
      .ts {color:#9ca3af;min-width:64px;text-align:right;}
      .badge {display:inline-block;padding:1px 8px;font-size:12px;
        border-radius:9999px;color:white;}
      .msg {white-space:pre-wrap;}
    </style>
    """
    html = [css, "<div class='log-card'>"]
    for l in subset:
        if " | " in l:
            ts, rest = l.split(" | ", 1)
        else:
            ts, rest = "", l
        tk = extract_ticker(l)
        badge_html = f"<span class='badge' style='background:{color_for_ticker(tk)}'>{tk}</span>" if tk else ""
        html.append(f"<div class='log-line'><span class='ts'>{ts}</span>{badge_html}<span class='msg'>{rest}</span></div>")
    html.append("</div>")
    st.markdown("\n".join(html), unsafe_allow_html=True)

render_log_html(log_lines, 250)

# -----------------------------
# DEBUG E AUTOREFRESH
# -----------------------------
with st.expander("🧪 Debug / Backup do estado (JSON)", expanded=False):
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v,updated_at"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200 and res.json():
            state_preview = res.json()[0]["v"]
            st.json(state_preview)
            st.download_button("⬇️ Baixar state_curto.json",
                               data=json.dumps(state_preview, indent=2),
                               file_name="state_curto.json", mime="application/json")
        else:
            st.info("Nenhum estado salvo ainda.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")

st_autorefresh(interval=60_000, limit=None, key="curto-refresh")
