# clube.PY - INTERFACE VISUAL (LEITURA/INSERÇÃO NA SUPABASE; SEM ALTERAR/DELETAR NA NUVEM)
# -*- coding: utf-8 -*-

import streamlit as st
from yahooquery import Ticker
import datetime
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import re
import json
import os
import time
import streamlit.components.v1 as components

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="⚡ CLUBE - COMPRA E VENDA", layout="wide")

# ✅ Atualiza tudo a cada 2 minutos — seguro e sem conflito
try:
    st.autorefresh(interval=120 * 1000, key="refresh_monitoramento")
except Exception:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=120 * 1000, key="refresh_monitoramento")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(3, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 59, 0)

# Intervalos/limites da interface (visual)
INTERVALO_VERIFICACAO      = 120     # s entre leituras de preço (por ticker)
TEMPO_ACUMULADO_MAXIMO     = 360     # s para “disparo visual”
LOG_MAX_LINHAS             = 1000

# Paleta para cores de tickers no log/gráfico
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# SUPABASE (LEITURA/INSERÇÃO EM kv_state_clube)
# -----------------------------
SUPABASE_URL   = st.secrets["supabase_url_clube"]
SUPABASE_KEY   = st.secrets["supabase_key_clube"]

# Nome da tabela KV e chave do estado (confirmado por você)
SUPABASE_TABLE = "kv_state_clube"
STATE_KEY      = "clube_przo_v1"   # (atenção: é "przo" mesmo)

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def ler_ativos_da_supabase() -> list[dict]:
    """
    Lê os ativos de v['ativos'] na linha (k) 'clube_przo_v1' da tabela kv_state_clube.
    Espera cada item com: {"ticker": str, "operacao": "compra"|"venda", "preco": float}.
    """
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?k=eq.{STATE_KEY}&select=v"
    try:
        r = requests.get(url, headers=_sb_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return []
        estado = data[0].get("v", {})
        ativos = estado.get("ativos", [])
        norm = []
        for a in ativos:
            t = (a.get("ticker") or "").upper().strip()
            op = (a.get("operacao") or "").lower().strip()
            pr = a.get("preco")
            if t and op in ("compra", "venda") and isinstance(pr, (int, float)):
                norm.append({"ticker": t, "operacao": op, "preco": float(pr)})
        return norm
    except Exception as e:
        st.sidebar.error(f"⚠️ Erro ao ler Supabase: {e}")
        return []

def inserir_ativo_na_supabase(ticker: str, operacao: str, preco: float) -> tuple[bool, str | None]:
    """
    Insere um novo ativo no array v['ativos'] (merge) da chave 'clube_przo_v1'.
    NÃO remove/atualiza nada do que já existe. Apenas adiciona.
    """
    try:
        # 1) Lê estado atual
        url_get = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?k=eq.{STATE_KEY}&select=v"
        r = requests.get(url_get, headers=_sb_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        estado = data[0].get("v", {}) if data else {}

        # 2) Atualiza a lista de ativos local
        ativos = estado.get("ativos", [])
        novo = {"ticker": ticker.upper().strip(), "operacao": operacao.lower().strip(), "preco": float(preco)}
        ativos.append(novo)
        estado["ativos"] = ativos

        # 3) Envia merge (não apaga nada)
        payload = {"k": STATE_KEY, "v": estado}
        r2 = requests.post(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
            headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates"},
            json=payload,
            timeout=15,
        )
        r2.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)

# -----------------------------
# ESTADO LOCAL (APENAS VISUAL)
# -----------------------------
# Persistência local do GRÁFICO (disparos/linhas) – arquivo no servidor (não nuvem)
VIS_STATE_FILE = "session_data/visual_state_clube.json"

def carregar_visual_state():
    os.makedirs("session_data", exist_ok=True)
    if os.path.exists(VIS_STATE_FILE):
        try:
            with open(VIS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # reconverte datas
            precos = {
                t: [(datetime.datetime.fromisoformat(dt), v) for dt, v in pares]
                for t, pares in data.get("precos_historicos", {}).items()
            }
            disparos = {
                t: [(datetime.datetime.fromisoformat(dt), v) for dt, v in pares]
                for t, pares in data.get("disparos", {}).items()
            }
            st.session_state.precos_historicos = precos
            st.session_state.disparos = disparos
        except Exception as e:
            st.sidebar.warning(f"⚠️ Visual state corrompido: {e}")

def salvar_visual_state():
    try:
        os.makedirs("session_data", exist_ok=True)
        data = {
            "precos_historicos": {
                t: [(dt.isoformat(), v) for dt, v in pares]
                for t, pares in st.session_state.get("precos_historicos", {}).items()
            },
            "disparos": {
                t: [(dt.isoformat(), v) for dt, v in pares]
                for t, pares in st.session_state.get("disparos", {}).items()
            }
        }
        with open(VIS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro salvando visual state: {e}")

def inicializar_estado():
    defaults = {
        "log_monitoramento": [],
        "ticker_colors": {},
        "tempo_acumulado": {},       # segundos em contagem POR TICKER (local)
        "em_contagem": {},           # bool por ticker
        "status": {},                # string por ticker (visual)
        "precos_historicos": {},     # {ticker: [(dt, preco), ...]}
        "disparos": {},              # {ticker: [(dt, preco), ...]}  -> estrelas
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    # carrega gráfico persistente local
    carregar_visual_state()

inicializar_estado()

# -----------------------------
# UTILITÁRIOS
# -----------------------------
def agora_lx():
    return datetime.datetime.now(TZ)

def dentro_pregao(dt):
    t = dt.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

@st.cache_data(ttl=5)
def obter_preco_atual(ticker_symbol: str):
    """
    Retorna float com o preço atual ou "-" quando não houver dado.
    Trata respostas inconsistentes do yahooquery.
    """
    if not ticker_symbol.endswith(".SA"):
        ticker_symbol = f"{ticker_symbol}.SA"

    tk = Ticker(ticker_symbol)

    # 1) tk.price
    try:
        price_data = tk.price
        if isinstance(price_data, dict):
            symbol_data = price_data.get(ticker_symbol)
            if not isinstance(symbol_data, dict) and price_data:
                for v in price_data.values():
                    if isinstance(v, dict):
                        symbol_data = v
                        break
            if isinstance(symbol_data, dict):
                p = (symbol_data.get("regularMarketPrice")
                     or symbol_data.get("postMarketPrice")
                     or symbol_data.get("preMarketPrice"))
                if isinstance(p, (int, float)):
                    return float(p)
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ {ticker_symbol}: erro price ({e})")

    # 2) history 1d
    try:
        hist = tk.history(period="1d")
        if isinstance(hist, pd.DataFrame) and not hist.empty and "close" in hist.columns:
            if isinstance(hist.index, pd.MultiIndex):
                try:
                    df_sym = hist.xs(ticker_symbol, level=0, drop_level=False)
                except Exception:
                    df_sym = hist
                if not df_sym.empty:
                    return float(df_sym["close"].dropna().iloc[-1])
            else:
                return float(hist["close"].dropna().iloc[-1])
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ {ticker_symbol}: erro history ({e})")

    return "-"

def color_for_ticker(ticker):
    if not ticker:
        return "#3b82f6"
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

def render_log_html(lines, selected_tickers=None, max_lines=250):
    if not lines:
        st.write("—")
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
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .ts { color: #9ca3af; min-width: 64px; text-align: right; }
      .badge {
        display: inline-block; padding: 1px 8px; font-size: 12px;
        border-radius: 9999px; color: white;
      }
      .msg { white-space: pre-wrap; }
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

# -----------------------------
# SIDEBAR (apenas ações locais + inserir novo ativo)
# -----------------------------
st.sidebar.header("⚙️ Configurações")


# Teste manual (não interfere no robô da nuvem)
async def testar_telegram():
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_clube", "")
    try:
        if tok and chat:
            bot = Bot(token=tok)
            await bot.send_message(chat_id=chat, text="✅ Teste de alerta clube funcionando!")
            return True, None
        return False, "token/chat_id não configurado"
    except Exception as e:
        return False, str(e)

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")


# -----------------------------
# BOTÃO: LIMPAR TABELA SUPABASE
# -----------------------------
def limpar_tabela_supabase():
    """
    Apaga todos os dados da chave clube_przo_v1 na tabela kv_state_clube (Supabase).
    Mantém a estrutura da linha, mas zera o campo v['ativos'].
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?k=eq.{STATE_KEY}"
        payload = {"v": {"ativos": []}}
        r = requests.patch(url, headers=_sb_headers(), json=payload, timeout=15)
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)

if st.sidebar.button("🧹 Limpar Banco de Dados"):
    st.sidebar.warning("Apagando todos os ativos da tabela...")
    ok, erro = limpar_tabela_supabase()
    if ok:
        st.sidebar.success("✅ Tabela limpa com sucesso na Supabase!")
        st.session_state.log_monitoramento.append(
            f"{agora_lx().strftime('%H:%M:%S')} | LIMPAR TABELA Supabase executado com sucesso."
        )
    else:
        st.sidebar.error(f"❌ Falha ao limpar tabela: {erro}")



if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    # Limpa apenas o estado LOCAL do gráfico
    st.session_state.disparos = {}
    st.session_state.precos_historicos = {}
    salvar_visual_state()
    st.sidebar.success("Marcadores e histórico do gráfico limpos!")

# Aviso temporário para limpar log
placeholder_log = st.sidebar.empty()
if st.sidebar.button("🧹 Limpar Monitoramento"):
    st.session_state["log_monitoramento"] = []
    with placeholder_log.container():
        st.success(f"🧹 Log limpo às {agora_lx().strftime('%H:%M:%S')}")
        time.sleep(3)
        placeholder_log.empty()



# -----------------------------
# CABEÇALHO / LAYOUT
# -----------------------------
now = agora_lx()
st.title("⚡ CLUBE - COMPRA E VENDA")
st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)

st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")

st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)
st.write("Insira os dados abaixo para enviar **um novo ativo** ao robô (Supabase) e monitore visualmente aqui na interface.")

st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)

# -----------------------------
# FORM DE INSERÇÃO (INSERE NA SUPABASE via v['ativos'])
# -----------------------------
col1, col2, col3 = st.columns(3)
with col1:
    ticker_input = st.text_input("Ticker (ex: PETR4)").upper().strip()
with col2:
    operacao_input = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco_input = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if not ticker_input:
        st.error("Digite um ticker válido.")
    else:
        ok, erro = inserir_ativo_na_supabase(ticker_input, operacao_input, preco_input)
        if ok:
            st.success(f"Ativo {ticker_input} inserido na Supabase. O robô da nuvem cuidará dos disparos.")
            st.session_state.log_monitoramento.append(
                f"{agora_lx().strftime('%H:%M:%S')} | Inserido no Banco de Dados: {ticker_input} {operacao_input} R$ {preco_input:.2f}"
            )
        else:
            st.error(f"Falha ao inserir na Supabase: {erro}")

# -----------------------------
# BLOCO PRINCIPAL (TABELA + GRÁFICO + LOG)
# -----------------------------
st.subheader("🧠 Banco de Dados")
tabela_status = st.empty()
grafico = st.empty()


# 1) Lê ativos da Supabase para monitorar visualmente
ativos = ler_ativos_da_supabase()

# Atualiza lista de filtros no sidebar (com os tickers encontrados)
if ativos:
    lista_tickers = sorted({a["ticker"] for a in ativos})
    # re-render da multiselect com opções corretas
    selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", lista_tickers, default=[])

# 2) Renderiza tabela e coleta preços para gráfico
now = agora_lx()
if ativos:
    linhas = []
    for a in ativos:
        t = a["ticker"]
        preco_alvo = a["preco"]
        op = a["operacao"].upper()

        preco_atual = "-"
        try:
            preco_atual = obter_preco_atual(t)
        except Exception as e:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {t}.SA erro: {e}")

        # 🧩 garante que o campo contagem_inicio exista
        if "contagem_inicio" not in st.session_state:
            st.session_state.contagem_inicio = {}
        
        # atualiza histórico local p/ gráfico
        if preco_atual != "-":
            st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))

        # contagem local (visual) quando entra na zona
        condicao = (
            (a["operacao"] == "compra" and preco_atual != "-" and preco_atual >= preco_alvo) or
            (a["operacao"] == "venda"  and preco_atual != "-" and preco_atual <= preco_alvo)
        )
        # contagem local (visual) baseada no momento em que ENTRA na zona
        condicao = (
            (a["operacao"] == "compra" and preco_atual != "-" and preco_atual >= preco_alvo) or
            (a["operacao"] == "venda"  and preco_atual != "-" and preco_atual <= preco_alvo)
        )
        
        # estado anterior (antes desta leitura)
        prev_em_contagem = st.session_state.em_contagem.get(t, False)
        
        if preco_atual == "-":
            # sem dado de preço: não conta
            st.session_state.em_contagem[t] = False
            st.session_state.contagem_inicio.pop(t, None)
            st.session_state.tempo_acumulado[t] = 0
            st.session_state.status[t] = "⚪ Sem dados"
        
        elif condicao:
            # entrou/agora está na zona
            if not prev_em_contagem:
                # TRANSIÇÃO: fora -> dentro => inicia contagem AGORA (0s)
                st.session_state.em_contagem[t] = True
                st.session_state.contagem_inicio[t] = now
                st.session_state.tempo_acumulado[t] = 0
            else:
                # já estava na zona: tempo real desde o início
                start = st.session_state.contagem_inicio.get(t)
                if isinstance(start, datetime.datetime):
                    st.session_state.tempo_acumulado[t] = int((now - start).total_seconds())
                else:
                    # segurança: se não havia start salvo, inicia agora
                    st.session_state.contagem_inicio[t] = now
                    st.session_state.tempo_acumulado[t] = 0
        
            st.session_state.status[t] = "🟡 Em contagem"
        
            # “disparo visual” (NÃO mexe na nuvem)
            if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                st.session_state.status[t] = "🚀 (visual) Disparo"
                st.session_state.disparos.setdefault(t, []).append((now, preco_atual))
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | {t}.SA DISPARO VISUAL — nuvem é quem envia alertas reais."
                )
        
        else:
            # saiu da zona (ou nunca esteve): zera contagem
            st.session_state.em_contagem[t] = False
            st.session_state.contagem_inicio.pop(t, None)
            st.session_state.tempo_acumulado[t] = 0
            st.session_state.status[t] = "🟢 Monitorando"


        minutos = int(st.session_state.tempo_acumulado.get(t, 0) / 60)
        linhas.append({
            "Ticker": t,
            "Operação": op,
            "Preço Alvo": f"R$ {preco_alvo:.2f}",
            "Preço Atual": f"R$ {preco_atual:.2f}" if preco_atual != "-" else "-",
            "Status": st.session_state.status.get(t, "🟢 Monitorando"),
            "Tempo Acumulado (local)": f"{minutos} min"
        })

    df = pd.DataFrame(linhas)
    tabela_status.dataframe(df, use_container_width=True, height=240)
else:
    tabela_status.info("Nenhum ativo encontrado na Supabase.")

# 3) GRÁFICO (linhas + estrelas que persistem localmente)
fig = go.Figure()
for t, dados in st.session_state.precos_historicos.items():
    if dados:
        xs, ys = zip(*dados)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers", name=t,
            line=dict(color=color_for_ticker(t), width=2)
        ))
for t, pontos in st.session_state.disparos.items():
    if pontos:
        xs, ys = zip(*pontos)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers", name=f"Disparou {t}",
            marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))
        ))

fig.update_layout(title="📉 Evolução dos Preços", template="plotly_dark")
grafico.plotly_chart(fig, use_container_width=True)

# -----------------------------
# MONITORAMENTO VISUAL (NO ESTILO DO LOG)
# -----------------------------
st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)
# Atualiza automaticamente a cada 2 minutos (120.000 ms)
if ativos:
    st.markdown("### 📡 Monitoramento dos Ativos")

    monitor_lines = []
    for a in ativos:
        t = a["ticker"]
        op = a["operacao"].upper()
        preco_alvo = a["preco"]
        preco_atual = obter_preco_atual(t)
        cor = color_for_ticker(t)

        if preco_atual == "-":
            status = "⚪ Sem dados"
        elif (a["operacao"] == "compra" and preco_atual >= preco_alvo) or \
             (a["operacao"] == "venda" and preco_atual <= preco_alvo):
            status = "🟡 Em contagem"
        elif t in st.session_state.disparos and st.session_state.disparos[t]:
            status = "🚀 Disparou"
        else:
            status = "🟢 Monitorando"

        minutos = int(st.session_state.tempo_acumulado.get(t, 0) / 60)
        ts = agora_lx().strftime("%H:%M:%S")

        line = (
            f"{ts} | "
            f"<span class='badge' style='background:{cor}'>{t}</span> "
            f"<b>{op}</b> • Alvo: R$ {preco_alvo:.2f} • Atual: "
            f"{('-' if preco_atual == '-' else f'R$ {preco_atual:.2f}')} "
            f"• {status} • ⏱️ {minutos} min"
        )
        monitor_lines.append(line)

    css_monitor = """
    <style>
      .log-card {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 10px;
        padding: 10px 12px;
        max-height: 300px;
        overflow-y: auto;
      }
      .log-line {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px;
        line-height: 1.35;
        margin: 2px 0;
        color: #e5e7eb;
      }
      .badge {
        display: inline-block; padding: 1px 8px; font-size: 12px;
        border-radius: 9999px; color: white;
      }
    </style>
    """
    html_monitor = [css_monitor, "<div class='log-card'>"]
    for l in monitor_lines:
        html_monitor.append(f"<div class='log-line'>{l}</div>")
    html_monitor.append("</div>")
    st.markdown("\n".join(html_monitor), unsafe_allow_html=True)
else:
    st.info("Nenhum ativo para monitorar.")

st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)

st.subheader("🕒 Dados Inseridos")
log_container = st.empty()

if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

# 4) LOG (com filtro opcional)
with log_container:
    render_log_html(st.session_state.log_monitoramento, selected_tickers, 250)

# 5) SALVA PERSISTÊNCIA LOCAL DO GRÁFICO (não mexe na nuvem)
salvar_visual_state()

# -----------------------------
# Rodapé / Ajuda rápida
# -----------------------------

st.markdown("<hr style='border:1px solid #2e2e2e;'>", unsafe_allow_html=True)
with st.expander("ℹ️ Como funciona esta interface?"):
    st.markdown("""
- **Leitura** de ativos é feita em **Supabase → kv_state_clube**, linha `k="clube_przo_v1"`, lendo `v["ativos"]`.  
- **Inserção** adiciona novos itens ao array `v["ativos"]` via *merge* — **não apaga nem atualiza** entradas existentes.  
- TUDO mais (gráfico, estrelas, contagens, log) é **local/visual** — **não altera a Supabase**.  
- As estrelas de “Disparou” ficam **fixas** no gráfico até você clicar em **“🧹 Limpar Gráfico ⭐”**.
- A interface **não remove** ativos na nuvem nem envia mensagens reais — quem faz isso é o **robô da nuvem**.
    """)
