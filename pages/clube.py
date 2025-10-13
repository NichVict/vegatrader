import streamlit as st
from yahooquery import Ticker
import datetime
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo  # fuso com DST
import re
import uuid
import streamlit.components.v1 as components
# ==== ADIÇÃO: persistência ====
import json
import os

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")                    # Lisboa (DST automático)
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)   # 14:00 Lisboa
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # 21:00 Lisboa

INTERVALO_VERIFICACAO = 300                       # 5 min
TEMPO_ACUMULADO_MAXIMO = 1500                     # 25 min (1500s)
LOG_MAX_LINHAS = 1000                             # limite de linhas do log

# Paleta de cores (rotaciona entre tickers)
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ==== PERSISTÊNCIA LOCAL ====
SAVE_DIR = "session_data"
os.makedirs(SAVE_DIR, exist_ok=True)
SAVE_PATH = os.path.join(SAVE_DIR, "state_clube_compra_venda.json")

def salvar_estado():
    """Salva os dados essenciais do app em JSON."""
    estado = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "disparos": st.session_state.get("disparos", {}),
        "tempo_acumulado": st.session_state.get("tempo_acumulado", {}),
        "em_contagem": st.session_state.get("em_contagem", {}),   # 👈 ADICIONADO
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "pausado": st.session_state.get("pausado", False),
        "ultimo_estado_pausa": st.session_state.get("ultimo_estado_pausa", None),
        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
        "avisou_abertura_pregao": st.session_state.get("avisou_abertura_pregao", False),
        "ultimo_update_tempo": st.session_state.get("ultimo_update_tempo", {}),
    }
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, default=str, indent=2)
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado: {e}")

def carregar_estado():
    """Restaura os dados do JSON (se existir), sem sobrescrever controles interativos."""
    if os.path.exists(SAVE_PATH):
        try:
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                estado = json.load(f)

            # 🚫 preserva o valor atual do checkbox, se já existir
            pausado_atual = st.session_state.get("pausado")

            for k, v in estado.items():
                if k == "pausado" and pausado_atual is not None:
                    continue  # mantém o valor clicado
                st.session_state[k] = v

            st.sidebar.info("💾 Estado restaurado com sucesso!")
        except Exception as e:
            st.sidebar.error(f"Erro ao carregar estado: {e}")

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    mensagem = MIMEMultipart()
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem["Subject"] = assunto
    mensagem.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(remetente, senha_ou_token)
        servidor.send_message(mensagem)

def enviar_notificacao(destinatario, assunto, corpo, remetente, senha_ou_token, token_telegram, chat_ids):
    """Envia e-mail e Telegram"""
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    async def send_telegram():
        try:
            bot = Bot(token=token_telegram)
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")
        except Exception as e:
            print(f"Erro Telegram: {e}")
    asyncio.run(send_telegram())

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol):
    tk = Ticker(ticker_symbol)
    # tenta preço em tempo real; fallback: fechamento
    try:
        p = tk.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    preco_atual = tk.history(period="1d")["close"].iloc[-1]
    return float(preco_atual)

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao):
    """Assinatura simplificada (4 args). Tokens/IDs vêm de st.secrets."""
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {msg_op} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = st.secrets.get("gmail_app_password", "anoe gegm boqj ldzo")
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {msg_op} em {ticker_symbol_sem_ext}"
    token_telegram = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
    chat_ids = [st.secrets.get("telegram_chat_id", "-1002533284493")]
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

async def testar_telegram():
    token = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
    chat = st.secrets.get("telegram_chat_id", "-1002533284493")
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat, text="✅ Teste de alerta CLUBE funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)

def agora_lx():
    return datetime.datetime.now(TZ)

def dentro_pregao(dt_now):
    t = dt_now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_abertura(dt_now):
    hoje_abre = dt_now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    hoje_fecha = dt_now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt_now < hoje_abre:
        return int((hoje_abre - dt_now).total_seconds()), hoje_abre
    elif dt_now > hoje_fecha:
        amanha_abre = hoje_abre + datetime.timedelta(days=1)
        return int((amanha_abre - dt_now).total_seconds()), amanha_abre
    else:
        return 0, hoje_abre

def fmt_hms(seg):
    h = seg // 3600
    m = (seg % 3600) // 60
    s = seg % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ---------- LOG: cor por ticker + box rolável + ordem decrescente ----------
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def color_for_ticker(ticker):
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

# regex atualizado: aceita letras e números intercalados (ex: B3SA3.SA)
TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\.SA\b")   # ex: B3SA3.SA, PETR4.SA, ITUB4.SA
PLAIN_TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\b")  # ex: B3SA3, PETR4, VALE3

def extract_ticker(line):
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    m2 = PLAIN_TICKER_PAT.search(line)
    return m2.group(1) if m2 else None

def render_log_html(lines, selected_tickers=None, max_lines=200):
    """Renderiza o log com cores por ticker, box rolável e ordem decrescente (sem animação para evitar piscar)."""
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]  # mais novo no topo
    if selected_tickers:
        subset = [l for l in subset if (extract_ticker(l) in selected_tickers)]

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
      .log-line{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px; line-height: 1.35; margin: 2px 0; color: #e5e7eb;
        display: flex; align-items: baseline; gap: 8px;
      }
      .ts{ color:#9ca3af; min-width:64px; text-align:right; }
      .badge{ display:inline-block; padding:1px 8px; font-size:12px; border-radius:9999px; color:white; }
      .msg{ white-space: pre-wrap; }
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
# ESTADOS GLOBAIS (defaults)
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"] else []

# Modo edição/pausa (COMEÇA ATIVO = False)
if "pausado" not in st.session_state:
    st.session_state.pausado = False
# Último estado de pausa (para evitar spam)
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None
# Pontos de disparo (para marcar ⭐ no gráfico)
if "disparos" not in st.session_state:
    st.session_state.disparos = {}  # { 'TICKER': [(datetime, preco), ...] }

ensure_color_map()

# 🟢 ADICIONE ESTA LINHA AQUI:
carregar_estado()

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES
# -----------------------------
st.sidebar.header("⚙️ Configurações")

# Botão de reset total da tabela/estado
if st.sidebar.button("🧹 Apagar estado salvo (reset total)"):
    try:
        if os.path.exists(SAVE_PATH):
            os.remove(SAVE_PATH)
        # zera tudo e deixa ATIVO por padrão
        st.session_state.clear()
        st.session_state.pausado = False
        st.session_state.ultimo_estado_pausa = None
        st.session_state.ativos = []
        st.session_state.historico_alertas = []
        st.session_state.log_monitoramento = []
        st.session_state.tempo_acumulado = {}
        st.session_state.em_contagem = {}
        st.session_state.status = {}
        st.session_state.precos_historicos = {}
        st.session_state.disparos = {}
        st.session_state.ultimo_update_tempo = {}
        # registra no novo log “limpo”
        now_tmp = datetime.datetime.now(TZ)
        st.session_state.log_monitoramento.append(
            f"{now_tmp.strftime('%H:%M:%S')} | 🧹 Reset manual do estado executado"
        )
        salvar_estado()
        st.sidebar.success("✅ Estado salvo apagado e reiniciado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

# Botão único de teste do Telegram (sem mostrar token/chat)
if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste (usando st.secrets)...")
    ok, erro = asyncio.run(testar_telegram())
    if ok:
        st.sidebar.success("✅ Mensagem enviada com sucesso!")
    else:
        st.sidebar.error(f"❌ Falha: {erro}")

st.sidebar.checkbox("⏸️ Pausar monitoramento (modo edição)", key="pausado")

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
col_limp, col_limp2 = st.sidebar.columns(2)
if col_limp.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")
if col_limp2.button("🧽 Limpar LOG"):
    st.session_state.log_monitoramento.clear()
    st.sidebar.success("Log limpo!")
if st.sidebar.button("🧼 Limpar marcadores ⭐"):
    st.session_state.disparos = {}
    st.sidebar.success("Marcadores limpos!")

# Filtro por ticker no LOG
tickers_existentes = sorted(set([a["ticker"] for a in st.session_state.ativos])) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CLUBE - COMPRA E VENDA")
st.caption(
    f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}"
)
st.write("Cadastre tickers, operações e preços alvo. O monitor roda automaticamente no horário do pregão (ou quando você despausar).")

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
        st.session_state.ativos.append(ativo)
        st.session_state.tempo_acumulado[ticker] = 0
        st.session_state.em_contagem[ticker] = False
        st.session_state.status[ticker] = "🟢 Monitorando"
        st.session_state.precos_historicos[ticker] = []
        st.session_state.ultimo_update_tempo[ticker] = None
        st.success(f"Ativo {ticker} adicionado com sucesso!")
        salvar_estado()


# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()

if st.session_state.ativos:
    data = []
    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        preco_atual = "-"
        try:
            preco_atual = obter_preco_atual(f"{t}.SA")
        except Exception:
            pass
        tempo = st.session_state.tempo_acumulado.get(t, 0)
        minutos = tempo / 60
        data.append({
            "Ticker": t,
            "Operação": ativo["operacao"].upper(),
            "Preço Alvo": f"R$ {ativo['preco']:.2f}",
            "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
            "Status": st.session_state.status.get(t, "🟢 Monitorando"),
            "Tempo Acumulado": f"{int(minutos)} min"
        })
    df = pd.DataFrame(data)
    tabela_status.dataframe(df, use_container_width=True, height=220)  # menos flicker
else:
    st.info("Nenhum ativo cadastrado ainda.")

st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()

st.subheader("🕒 Log de Monitoramento")
# Cartão único de contagem regressiva (fora do pregão)
countdown_container = st.empty()
# Log estilizado
log_container = st.empty()

# -----------------------------
# CICLO ÚNICO + REEXECUÇÃO AUTOMÁTICA
# -----------------------------
sleep_segundos = 60  # padrão fora do pregão / pausado

# evita spam de log quando alterna pausa
if st.session_state.pausado != st.session_state.ultimo_estado_pausa:
    st.session_state.ultimo_estado_pausa = st.session_state.pausado

if st.session_state.pausado:
    pass
else:
    # ✅ FIX: acumula tempo entre re-runs (logo no início do loop)
    for t in list(st.session_state.tempo_acumulado.keys()):
        if st.session_state.em_contagem.get(t, False):
            ultimo = st.session_state.ultimo_update_tempo.get(t)
            if ultimo:
                try:
                    dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                    if dt_ultimo.tzinfo is None:
                        dt_ultimo = dt_ultimo.replace(tzinfo=TZ)
                except Exception:
                    dt_ultimo = agora_lx()
                delta = (agora_lx() - dt_ultimo).total_seconds()
                if delta > 0:
                    st.session_state.tempo_acumulado[t] += delta
                    st.session_state.ultimo_update_tempo[t] = agora_lx().isoformat()
                    st.session_state.log_monitoramento.append(
                        f"{agora_lx().strftime('%H:%M:%S')} | ⏳ {t}: +{int(delta)}s acumulados (entre ciclos)"
                    )

    if dentro_pregao(now):        

        # ---- Notificação única na abertura do pregão ----
        if not st.session_state.get("avisou_abertura_pregao", False):
            st.session_state["avisou_abertura_pregao"] = True
            try:
                token = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY").strip()
                chat = st.secrets.get("telegram_chat_id", "-1002533284493").strip()
                if not token or not chat:
                    raise ValueError("Token ou chat_id ausente em st.secrets")
                bot = Bot(token=token)
                asyncio.run(bot.send_message(chat_id=chat, text="🤖 Robô ativo — Pregão Aberto! 📈"))
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | 📣 Mensagem Telegram enviada: Pregão Aberto"
                )
            except Exception as e:
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | ⚠️ Erro ao enviar notificação de abertura: {e}"
                )

        # Esconde o cartão de countdown quando entra no pregão
        countdown_container.empty()

        # 1) Atualiza tabela/gráfico e monitora
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            st.session_state.em_contagem.setdefault(t, False)
            st.session_state.status.setdefault(t, "🟢 Monitorando")
            st.session_state.ultimo_update_tempo.setdefault(t, None)

            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")

            if preco_atual != "-":
                st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))

            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            data.append({
                "Ticker": t,
                "Operação": ativo["operacao"].upper(),
                "Preço Alvo": f"R$ {ativo['preco']:.2f}",
                "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(minutos)} min"
            })
        if data:
            tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)

        # Lógica por ativo
        tickers_para_remover = []  # <- para tirar da busca após disparo
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao_atv = ativo["operacao"]
            tk_full = f"{t}.SA"

            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
                continue

            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco_atual:.2f}")

            condicao = (
                (operacao_atv == "compra" and preco_atual >= preco_alvo) or
                (operacao_atv == "venda"  and preco_atual <= preco_alvo)
            )

            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"

                # Entrou na zona: inicia do zero se ainda não estava em contagem
                if not st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = True
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando contagem..."
                    )
                else:
                    # já estava em contagem: acumula pelo delta real de tempo
                    ultimo = st.session_state.ultimo_update_tempo.get(t)
                    if ultimo:
                        try:
                            dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                            # 👇 Aqui está a correção de timezone:
                            if dt_ultimo.tzinfo is None:
                                dt_ultimo = dt_ultimo.replace(tzinfo=TZ)
                        except Exception:
                            dt_ultimo = now
                    else:
                        dt_ultimo = now
                
                    delta = (now - dt_ultimo).total_seconds()
                    if delta < 0:
                        delta = 0
                
                    st.session_state.tempo_acumulado[t] = st.session_state.tempo_acumulado.get(t, 0) + delta
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⏱ {t}: {int(st.session_state.tempo_acumulado[t])}s acumulados (+{int(delta)}s)"
                    )


                # dispara alerta após tempo máximo acumulado
                if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                    alerta_msg = notificar_preco_alvo_alcancado(tk_full, preco_alvo, preco_atual, operacao_atv)
                    st.warning(alerta_msg)
                    st.session_state.historico_alertas.append({
                        "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "ticker": t,
                        "operacao": operacao_atv,
                        "preco_alvo": preco_alvo,
                        "preco_atual": preco_atual
                    })

                    # ⭐ guarda o ponto do disparo p/ marcar no gráfico
                    st.session_state.disparos.setdefault(t, []).append((now, preco_atual))

                    # marca para remover da busca após o loop
                    tickers_para_remover.append(t)

            else:
                # Se saiu da zona, zera a contagem
                if st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora da zona"
                    st.session_state.ultimo_update_tempo[t] = None
                    st.session_state.log_monitoramento.append(
                        f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada."
                    )

        # Remove da busca os tickers disparados
        if tickers_para_remover:
            st.session_state.ativos = [a for a in st.session_state.ativos if a["ticker"] not in tickers_para_remover]
            for t in tickers_para_remover:
                st.session_state.tempo_acumulado.pop(t, None)
                st.session_state.em_contagem.pop(t, None)
                st.session_state.status[t] = "✅ Disparado (removido)"
                st.session_state.ultimo_update_tempo.pop(t, None)
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 🧹 Removidos após disparo: {', '.join(tickers_para_remover)}"
            )
            # 🚨 ADICIONE ESTA LINHA:
            salvar_estado()

        # Gráfico: linhas por ticker (cor consistente) + marcadores de disparo ⭐
        fig = go.Figure()
        for t, dados in st.session_state.precos_historicos.items():
            if len(dados) > 1:
                xs, ys = zip(*dados)
                fig.add_trace(go.Scatter(
                    x=xs, y=ys,
                    mode="lines+markers",
                    name=t,
                    line=dict(color=color_for_ticker(t), width=2)
                ))

        # ⭐ marcadores de disparo
        for t, pontos in st.session_state.disparos.items():
            if not pontos:
                continue
            xs, ys = zip(*pontos)
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="markers",
                name=f"Disparo {t}",
                marker=dict(
                    symbol="star",
                    size=12,
                    color=color_for_ticker(t),
                    line=dict(width=2, color="white")
                ),
                hovertemplate=(
                    f"{t}<br>%{{x|%Y-%m-%d %H:%M:%S}}"
                    "<br><b>DISPARO</b>"
                    "<br>Preço: R$ %{y:.2f}<extra></extra>"
                ),
            ))

        fig.update_layout(
            title="📉 Evolução dos Preços (com disparos ⭐)",
            xaxis_title="Tempo", yaxis_title="Preço (R$)",
            legend_title="Legenda",
            template="plotly_dark"
        )
        grafico.plotly_chart(fig, use_container_width=True)

        sleep_segundos = INTERVALO_VERIFICACAO  # 5 min

    else:
        # ---- Reset do aviso de abertura ----
        st.session_state["avisou_abertura_pregao"] = False

        # ======= FORA DO PREGÃO: CARTÃO COM COUNTDOWN EM JS (sem rerun por segundo) =======
        faltam, prox_abertura = segundos_ate_abertura(now)
        # id único para não conflitar entre reruns
        elem_id = f"cd-{uuid.uuid4().hex[:8]}"
        components.html(
            f"""
<div style="background:#0b1220;border:1px solid #1f2937;border-radius:10px;padding:12px 14px;">
  <span style="color:#9ca3af;">⏸️ Pregão fechado.</span>
  <span style="margin-left:8px; color:#e5e7eb;">
  Reabre em <b id="{elem_id}" style="color:#ffffff;">--:--:--</b>
  (às {prox_abertura.strftime('%H:%M')}). 
</span>
</div>
<script>
(function(){{
  var total={faltam};
  function fmt(s){{
    var h=Math.floor(s/3600), m=Math.floor((s%3600)/60), ss=s%60;
    return String(h).padStart(2,'0')+":"+String(m).padStart(2,'0')+":"+String(ss).padStart(2,'0');
  }}
  function tick(){{
    var el=document.getElementById("{elem_id}");
    if(!el) return;
    el.textContent=fmt(total);
    if(total>0) setTimeout(function(){{ total--; tick(); }}, 1000);
  }}
  tick();
}})();
</script>
            """,
            height=70
        )

        # ---- MANTER O APP VIVO (keep-alive fora do pregão, com controle de tempo) ----
        try:
            if not dentro_pregao(now):
                APP_URL = "https://robozinho.streamlit.app"  # substitua pela URL real do seu app
                intervalo_ping = 15 * 60  # envia keep-alive a cada 15 minutos
                ultimo_ping = st.session_state.get("ultimo_ping_keepalive")

                # Se veio do JSON como string, converte
                if isinstance(ultimo_ping, str):
                    try:
                        ultimo_ping = datetime.datetime.fromisoformat(ultimo_ping)
                    except Exception:
                        ultimo_ping = None

                # Envia ping apenas se já passou o intervalo definido
                if not ultimo_ping or (now - ultimo_ping).total_seconds() > intervalo_ping:
                    requests.get(APP_URL, timeout=5)
                    st.session_state["ultimo_ping_keepalive"] = now.isoformat()  # salva compatível com JSON
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | 🔄 Keep-alive ping enviado para {APP_URL}"
                    )
        except Exception as e:
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | ⚠️ Erro no keep-alive: {e}"
            )

        # ---- Intervalo de reexecução fora do pregão (adaptativo) ----
        if faltam > 3600:  # falta mais de 1 hora para o pregão
            sleep_segundos = 900   # 15 minutos
        elif faltam > 600:  # entre 10min e 1h
            sleep_segundos = 300   # 5 minutos
        else:  # menos de 10min até o pregão
            sleep_segundos = 180   # 3 minutos

# Limita crescimento do log (memória)
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

# Renderiza LOG estilizado (descendente, cores, box rolável, filtro por ticker)
with log_container:
    render_log_html(st.session_state.log_monitoramento, selected_tickers, max_lines=250)

# -----------------------------
# 🧪 PAINEL DE DEBUG / BACKUP DO JSON
# -----------------------------
with st.expander("🧪 Debug / Backup do estado (JSON)", expanded=False):
    st.caption(f"Arquivo: `{SAVE_PATH}`")
    try:
        if os.path.exists(SAVE_PATH):
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                state_preview = json.load(f)
            st.json(state_preview)
            st.download_button(
                "⬇️ Baixar state_clube.json",
                data=json.dumps(state_preview, ensure_ascii=False, indent=2),
                file_name="state_clube.json",
                mime="application/json",
            )
        else:
            st.info("Ainda não existe arquivo salvo.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")

# ==== Atualiza timestamp antes de salvar ====
# garante que o próximo delta inclua o tempo dormido
time.sleep(sleep_segundos)

# ==== Atualiza timestamps e salva estado (mantém progresso real) ====
now = agora_lx()

# garante que nenhum ticker em contagem perca tempo entre refreshs
for t in list(st.session_state.tempo_acumulado.keys()):
    if st.session_state.em_contagem.get(t, False):
        ultimo = st.session_state.ultimo_update_tempo.get(t)
        if ultimo:
            try:
                dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                if dt_ultimo.tzinfo is None:
                    dt_ultimo = dt_ultimo.replace(tzinfo=TZ)
            except Exception:
                dt_ultimo = now
            delta = (now - dt_ultimo).total_seconds()
            if delta > 0:
                st.session_state.tempo_acumulado[t] += delta
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | 🕓 {t}: +{int(delta)}s persistidos no refresh"
                )
        st.session_state.ultimo_update_tempo[t] = now.isoformat()

# salva estado completo (protegido)
salvar_estado()
time.sleep(sleep_segundos)
st.rerun()



