import streamlit as st
from yahooquery import Ticker
import datetime as dt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")          # DST automático
HORARIO_INICIO_PREGAO = dt.time(14, 0)  # 14:00
HORARIO_FIM_PREGAO    = dt.time(21, 0)  # 21:00
INTERVALO_VERIFICACAO = 300             # 5 min durante pregão
TEMPO_ACUMULADO_MAXIMO = 900            # 15 min (use 1500 = 25 min se quiser)
KEEPALIVE_SECONDS = 60                  # fora do pregão: mantém a página viva

# =========================
# Funções auxiliares
# =========================
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"] = remetente
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(remetente, senha_ou_token)
        s.send_message(msg)

def enviar_notificacao(destinatario, assunto, corpo, remetente, senha_ou_token, token_telegram, chat_ids):
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    async def send_telegram():
        try:
            bot = Bot(token=token_telegram)
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")
        except Exception as e:
            print(f"Erro Telegram: {e}")
    asyncio.run(send_telegram())

@retry(stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol: str) -> float:
    tk = Ticker(ticker_symbol)
    # tenta preço em tempo real
    try:
        p = tk.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    # fallback: último fechamento
    return float(tk.history(period="1d")["close"].iloc[-1])

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    oper_str = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    msg = (
        f"Operação de {oper_str} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = "anoe gegm boqj ldzo"
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {oper_str} em {ticker_symbol_sem_ext}"
    chat_ids = ["-1002533284493"]
    enviar_notificacao(destinatario, assunto, msg, remetente, senha_ou_token, token_telegram, chat_ids)
    return msg

async def testar_telegram(token_telegram, chat_id):
    try:
        bot = Bot(token=token_telegram)
        await bot.send_message(chat_id=chat_id, text="✅ Teste de alerta CLUBE funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)

# ---- horário robusto (datetime, mesmo dia/fuso) ----
def _abre_fecha_dt(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    now = now.astimezone(TZ)
    abre = now.replace(hour=HORARIO_INICIO_PREGAO.hour,
                       minute=HORARIO_INICIO_PREGAO.minute,
                       second=0, microsecond=0)
    fecha = now.replace(hour=HORARIO_FIM_PREGAO.hour,
                        minute=HORARIO_FIM_PREGAO.minute,
                        second=0, microsecond=0)
    return abre, fecha

def dentro_pregao(now: dt.datetime) -> bool:
    now = now.astimezone(TZ)
    abre, fecha = _abre_fecha_dt(now)
    return abre <= now <= fecha

def segundos_ate_proxima_abertura(now: dt.datetime) -> int:
    now = now.astimezone(TZ)
    abre, fecha = _abre_fecha_dt(now)
    if now < abre:
        return int((abre - now).total_seconds())
    if now > fecha:
        amanha = abre + dt.timedelta(days=1)
        return int((amanha - now).total_seconds())
    return 0  # dentro do pregão

# ---- garante auto-start/stop com rerun (chamado 2x) ----
def ensure_monitoring(now: dt.datetime):
    auto_start = st.session_state.get("auto_start_open", True)
    auto_stop  = st.session_state.get("auto_stop_close", True)
    monitorando_flag = st.session_state.get("monitorando", False)

    # STOP primeiro (caso acorde fora do pregão)
    if auto_stop and monitorando_flag and not dentro_pregao(now):
        st.session_state.monitorando = False
        st.toast("⏹ Monitoramento parado automaticamente (fechamento).", icon="🛑")
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    # START quando entrar na janela
    if auto_start and not st.session_state.get("monitorando", False) and dentro_pregao(now):
        st.session_state.monitorando = True
        st.toast("▶️ Monitoramento iniciado automaticamente (abertura do pregão).", icon="✅")
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

# =========================
# Estado inicial
# =========================
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "monitorando",
            "last_run", "auto_start_open", "auto_stop_close"]:
    if var not in st.session_state:
        if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"]:
            st.session_state[var] = {}
        elif var in ["monitorando"]:
            st.session_state[var] = False
        elif var in ["last_run"]:
            st.session_state[var] = None
        elif var in ["auto_start_open", "auto_stop_close"]:
            st.session_state[var] = True  # ligados por padrão
        else:
            st.session_state[var] = []

# =========================
# Sidebar
# =========================
st.sidebar.header("⚙️ Configurações")
token_telegram = st.sidebar.text_input("Token do Bot Telegram", type="password",
                                       value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
chat_id_teste = st.sidebar.text_input("Chat ID (grupo ou usuário)", value="-1002533284493")
if st.sidebar.button("📤 Testar Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram(token_telegram, chat_id_teste))
    st.sidebar.success("✅ Mensagem enviada!") if ok else st.sidebar.error(f"❌ {erro}")

st.sidebar.divider()
st.sidebar.checkbox("▶️ Iniciar automaticamente na abertura", key="auto_start_open", value=st.session_state.auto_start_open)
st.sidebar.checkbox("⏹ Parar automaticamente no fechamento", key="auto_stop_close", value=st.session_state.auto_stop_close)

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for a in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{a['ticker']}** — {a['operacao'].upper()}")
        st.sidebar.caption(f"{a['hora']} | Alvo: {a['preco_alvo']:.2f} | Atual: {a['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
if st.sidebar.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")

# =========================
# ENFORCE auto-start/stop (1ª checagem)
# =========================
now_global = dt.datetime.now(TZ)
ensure_monitoring(now_global)

# =========================
# UI principal
# =========================
st.title("📈 CLUBE - COMPRA E VENDA")
st.caption(f"Agora: {now_global.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now_global) else '🟥 Fora do pregão'}")

c1, c2, c3 = st.columns(3)
with c1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with c2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with c3:
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
        st.success(f"Ativo {ticker} adicionado.")

st.subheader("📊 Status dos Ativos")
tabela_status = st.empty()
st.subheader("📉 Gráfico (preços recentes)")
grafico = st.empty()
st.subheader("🕒 Log de Monitoramento")
log_box = st.empty()

# Controles manuais
b1, b2 = st.columns(2)
if b1.button("🚀 Iniciar monitoramento"):
    st.session_state.monitorando = True
if b2.button("🛑 Parar monitoramento"):
    st.session_state.monitorando = False
    st.warning("⏹ Monitoramento interrompido manualmente.")

# =========================
# Render: tabela + gráfico
# =========================
def render_tabela_e_grafico():
    if not st.session_state.ativos:
        st.info("Nenhum ativo cadastrado.")
        return

    now = dt.datetime.now(TZ)
    rows = []
    for a in st.session_state.ativos:
        t = a["ticker"]
        try:
            p_atual = obter_preco_atual(f"{t}.SA")
        except Exception:
            p_atual = "-"
        if p_atual != "-":
            st.session_state.precos_historicos.setdefault(t, []).append((now, p_atual))
        tempo = st.session_state.tempo_acumulado.get(t, 0)
        rows.append({
            "Ticker": t,
            "Operação": a["operacao"].upper(),
            "Preço Alvo": f"R$ {a['preco']:.2f}",
            "Preço Atual": f"R$ {p_atual}" if p_atual != "-" else "-",
            "Status": st.session_state.status.get(t, "🟢 Monitorando"),
            "Tempo Acumulado": f"{int(tempo/60)} min"
        })
    tabela_status.table(pd.DataFrame(rows))

    fig = go.Figure()
    for t, dados in st.session_state.precos_historicos.items():
        if len(dados) > 1:
            xs, ys = zip(*dados)
            status = st.session_state.status.get(t, "🟢 Monitorando")
            cor = "green" if "🟢" in status else "orange" if "🟡" in status else "red"
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t, line=dict(color=cor)))
    fig.update_layout(title="📉 Evolução dos Preços", xaxis_title="Tempo", yaxis_title="Preço (R$)")
    grafico.plotly_chart(fig, use_container_width=True)

render_tabela_e_grafico()

# =========================
# Um ciclo reativo de monitoramento
# =========================
def ciclo_monitoramento():
    now = dt.datetime.now(TZ)
    last = st.session_state.last_run
    dt_secs = 0 if last is None else max(0, int((now - last).total_seconds()))
    st.session_state.last_run = now

    if not st.session_state.monitorando:
        return
    if not dentro_pregao(now):
        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⏸ Fora do horário de pregão.")
        return

    for a in st.session_state.ativos:
        t = a["ticker"]
        alvo = a["preco"]
        op = a["operacao"]
        tk_full = f"{t}.SA"

        try:
            preco = obter_preco_atual(tk_full)
        except Exception as e:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
            continue

        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco:.2f}")

        cond = (op == "compra" and preco >= alvo) or (op == "venda" and preco <= alvo)
        if cond:
            st.session_state.status[t] = "🟡 Em contagem"
            if not st.session_state.em_contagem[t]:
                st.session_state.em_contagem[t] = True
                st.session_state.tempo_acumulado[t] = 0
                st.session_state.log_monitoramento.append(
                    f"⚠️ {t} atingiu o alvo ({alvo:.2f}). Iniciando contagem..."
                )
            inc = INTERVALO_VERIFICACAO if dt_secs == 0 else min(dt_secs, INTERVALO_VERIFICACAO)
            st.session_state.tempo_acumulado[t] += inc
            st.session_state.log_monitoramento.append(
                f"⏱ {t}: {st.session_state.tempo_acumulado[t]}s acumulados"
            )
            if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                alerta_msg = notificar_preco_alvo_alcancado(tk_full, alvo, preco, op, token_telegram)
                st.warning(alerta_msg)
                st.session_state.historico_alertas.append({
                    "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "ticker": t,
                    "operacao": op,
                    "preco_alvo": alvo,
                    "preco_atual": preco
                })
                st.session_state.status[t] = "🟢 Monitorando"
                st.session_state.em_contagem[t] = False
                st.session_state.tempo_acumulado[t] = 0
        else:
            if st.session_state.em_contagem[t]:
                st.session_state.em_contagem[t] = False
                st.session_state.tempo_acumulado[t] = 0
                st.session_state.status[t] = "🔴 Fora da zona"
                st.session_state.log_monitoramento.append(
                    f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada."
                )

ciclo_monitoramento()
if st.session_state.log_monitoramento:
    log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))

# =========================
# Card de contador (visual) + auto-refresh
# =========================
now = dt.datetime.now(TZ)
if not dentro_pregao(now):
    seg = max(1, segundos_ate_proxima_abertura(now))
    abertura_str = HORARIO_INICIO_PREGAO.strftime('%H:%M')
    target_ts_ms = int((now + dt.timedelta(seconds=seg)).timestamp() * 1000)

    html(f"""
    <div style="font-family:system-ui,Segoe UI,Roboto,Arial;color:#d1d5db;">
      <div style="background:#0b2a43;padding:12px 14px;border-radius:8px;margin-top:8px;">
        <span style="font-size:14px;">⏳ Pregão fechado. Reabre em
          <b><span id="left">--:--:--</span></b> (às {abertura_str}).</span>
      </div>
      <div style="font-size:15px;margin:10px 2px 0;">
        ⏱️ <b>Contagem regressiva: <span id="cd">--:--:--</span></b>
      </div>
    </div>
    <script>
      (function(){{
        const target = {target_ts_ms};
        function pad(n) {{ return String(n).padStart(2,'0'); }}
        function tick(){{
          const now = Date.now();
          let diff = Math.max(0, Math.floor((target - now)/1000));
          const h = Math.floor(diff/3600);
          diff %= 3600;
          const m = Math.floor(diff/60);
          const s = diff % 60;
          const text = pad(h)+":"+pad(m)+":"+pad(s);
          const cd = document.getElementById('cd');
          const left = document.getElementById('left');
          if (cd) cd.textContent = text;
          if (left) left.textContent = text;
        }}
        tick();
        setInterval(tick, 1000);
      }})();
    </script>
    """, height=100)

# =========================
# ENFORCE auto-start/stop (2ª checagem) + próximo refresh
# =========================
now_final = dt.datetime.now(TZ)
ensure_monitoring(now_final)

faltam = segundos_ate_proxima_abertura(now_final)
if st.session_state.get("monitorando") and dentro_pregao(now_final):
    prox = INTERVALO_VERIFICACAO
else:
    if 0 < faltam <= 60:
        prox = 1
    elif 0 < faltam <= 600:
        prox = 5
    else:
        prox = KEEPALIVE_SECONDS

st.caption(f"🔄 Próxima atualização automática em ~{prox} segundos.")
st.markdown(
    f"<script>setTimeout(function(){{ window.location.reload(); }}, {prox*1000});</script>",
    unsafe_allow_html=True
)








