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
from streamlit.components.v1 import html  # contador ao vivo (sem recarregar)

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")  # DST automático
HORARIO_INICIO_PREGAO = dt.time(10, 45, 0)   # ajuste se precisar testar
HORARIO_FIM_PREGAO    = dt.time(21, 0, 0)
INTERVALO_VERIFICACAO = 300                 # 5 min
TEMPO_ACUMULADO_MAXIMO = 900                # 15 min (use 1500 = 25 min em produção)

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
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
    try:
        p = tk.price[ticker_symbol].get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    return float(tk.history(period="1d")["close"].iloc[-1])

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    mensagem_operacao = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {mensagem_operacao} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = "anoe gegm boqj ldzo"
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {mensagem_operacao} em {ticker_symbol_sem_ext}"
    chat_ids = ["-1002533284493"]
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

async def testar_telegram(token_telegram, chat_id):
    try:
        bot = Bot(token=token_telegram)
        await bot.send_message(chat_id=chat_id, text="✅ Teste de alerta CLUBE funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)

def dentro_pregao(now: dt.datetime) -> bool:
    t = now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_proxima_abertura(now: dt.datetime) -> int:
    hoje_abre = now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=HORARIO_INICIO_PREGAO.minute,
                            second=0, microsecond=0)
    fecha = now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=HORARIO_FIM_PREGAO.minute,
                        second=0, microsecond=0)
    if now < hoje_abre:
        return int((hoje_abre - now).total_seconds())
    if now > fecha:
        amanha = hoje_abre + dt.timedelta(days=1)
        return int((amanha - now).total_seconds())
    return 0

# -----------------------------
# ESTADOS GLOBAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "monitorando", "last_run"]:
    if var not in st.session_state:
        if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"]:
            st.session_state[var] = {}
        elif var == "monitorando":
            st.session_state[var] = False
        elif var == "last_run":
            st.session_state[var] = None
        else:
            st.session_state[var] = []

# -----------------------------
# SIDEBAR – TELEGRAM & HISTÓRICO
# -----------------------------
st.sidebar.header("⚙️ Configurações")
token_telegram = st.sidebar.text_input("Token do Bot Telegram", type="password",
                                       value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
chat_id_teste = st.sidebar.text_input("Chat ID (grupo ou usuário)", value="-1002533284493")
if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram(token_telegram, chat_id_teste))
    st.sidebar.success("✅ Mensagem enviada!") if ok else st.sidebar.error(f"❌ {erro}")

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
if st.sidebar.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
st.title("📈 CLUBE - COMPRA E VENDA")
st.write("Cadastre tickers, operações e preços alvo. Use **Iniciar** para começar o monitoramento reativo.")

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
        st.success(f"Ativo {ticker} adicionado com sucesso!")

# -----------------------------
# ÁREAS DINÂMICAS
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()
st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()
st.subheader("🕒 Log de Monitoramento")
log_box = st.empty()

# -----------------------------
# CONTROLES
# -----------------------------
colA, colB = st.columns(2)
if colA.button("🚀 Iniciar monitoramento"):
    st.session_state.monitorando = True
if colB.button("🛑 Parar monitoramento"):
    st.session_state.monitorando = False
    st.warning("⏹ Monitoramento interrompido manualmente.")

# -----------------------------
# RENDER TABELA + GRÁFICO (sempre)
# -----------------------------
def render_tabela_e_grafico():
    if not st.session_state.ativos:
        st.info("Nenhum ativo cadastrado ainda.")
        return

    now = dt.datetime.now(TZ)
    data_rows = []
    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        try:
            preco_atual = obter_preco_atual(f"{t}.SA")
        except Exception:
            preco_atual = "-"
        if preco_atual != "-":
            st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))
        tempo = st.session_state.tempo_acumulado.get(t, 0)
        data_rows.append({
            "Ticker": t,
            "Operação": ativo["operacao"].upper(),
            "Preço Alvo": f"R$ {ativo['preco']:.2f}",
            "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
            "Status": st.session_state.status.get(t, "🟢 Monitorando"),
            "Tempo Acumulado": f"{int(tempo/60)} min"
        })
    df = pd.DataFrame(data_rows)
    tabela_status.table(df)

    fig = go.Figure()
    for t, dados in st.session_state.precos_historicos.items():
        if len(dados) > 1:
            tempos, precos = zip(*dados)
            status = st.session_state.status.get(t, "🟢 Monitorando")
            cor = "green" if "🟢" in status else "orange" if "🟡" in status else "red"
            fig.add_trace(go.Scatter(x=tempos, y=precos, mode="lines+markers",
                                     name=t, line=dict(color=cor)))
    fig.update_layout(title="📉 Evolução dos Preços Monitorados",
                      xaxis_title="Tempo", yaxis_title="Preço (R$)",
                      legend_title="Ticker")
    grafico.plotly_chart(fig, use_container_width=True)

render_tabela_e_grafico()

# -----------------------------
# UM CICLO DE MONITORAMENTO (reativo)
# -----------------------------
def ciclo_monitoramento():
    now = dt.datetime.now(TZ)
    last_run = st.session_state.last_run
    dt_secs = 0 if last_run is None else max(0, int((now - last_run).total_seconds()))
    st.session_state.last_run = now

    if not st.session_state.monitorando:
        return

    if not dentro_pregao(now):
        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⏸ Fora do horário de pregão.")
        return

    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        preco_alvo = ativo["preco"]
        operacao = ativo["operacao"]
        tk_full = f"{t}.SA"

        try:
            preco_atual = obter_preco_atual(tk_full)
        except Exception as e:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
            continue

        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco_atual:.2f}"
        )

        cond = (
            (operacao == "compra" and preco_atual >= preco_alvo) or
            (operacao == "venda"  and preco_atual <= preco_alvo)
        )

        if cond:
            st.session_state.status[t] = "🟡 Em contagem"
            if not st.session_state.em_contagem[t]:
                st.session_state.em_contagem[t] = True
                st.session_state.tempo_acumulado[t] = 0
                st.session_state.log_monitoramento.append(
                    f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando contagem..."
                )
            incremento = INTERVALO_VERIFICACAO if dt_secs == 0 else min(dt_secs, INTERVALO_VERIFICACAO)
            st.session_state.tempo_acumulado[t] += incremento
            st.session_state.log_monitoramento.append(
                f"⏱ {t}: {st.session_state.tempo_acumulado[t]}s acumulados"
            )
            if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                alerta_msg = notificar_preco_alvo_alcancado(
                    tk_full, preco_alvo, preco_atual, operacao, token_telegram
                )
                st.warning(alerta_msg)
                st.session_state.historico_alertas.append({
                    "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "ticker": t,
                    "operacao": operacao,
                    "preco_alvo": preco_alvo,
                    "preco_atual": preco_atual
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

# -----------------------------
# CONTADOR AO VIVO (sem recarregar) + AUTO-REFRESH INTELIGENTE
# -----------------------------
now = dt.datetime.now(TZ)

if not dentro_pregao(now):
    seg_ate_abertura = max(1, segundos_ate_proxima_abertura(now))
    abertura_str = HORARIO_INICIO_PREGAO.strftime('%H:%M')
    target_ts_ms = int((now + dt.timedelta(seconds=seg_ate_abertura)).timestamp() * 1000)

    # Contador ao vivo apenas visual (NÃO recarrega)
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

# Agendamento do próximo refresh:
# - Em pregão: a cada INTERVALO_VERIFICACAO
# - Fora do pregão: se faltar <= 60s, atualiza a cada 1s;
#                   se faltar <= 10min, atualiza a cada 5s;
#                   se faltar <= 60min, atualiza a cada 60s;
#                   senão, a cada 300s
if st.session_state.monitorando:
    if dentro_pregao(now):
        prox_segundos = INTERVALO_VERIFICACAO
    else:
        s = max(1, segundos_ate_proxima_abertura(now))
        if s <= 60:
            prox_segundos = 1
        elif s <= 600:
            prox_segundos = 5
        elif s <= 3600:
            prox_segundos = 60
        else:
            prox_segundos = 300
else:
    prox_segundos = 600  # monitoramento parado

st.caption(f"🔄 Próxima atualização automática em ~{prox_segundos} segundos.")
# Auto-refresh via JS (intervalo variável) — NÃO recarrega no zero, evitando "piscar"
st.markdown(
    f"<script>setTimeout(function(){{ window.location.reload(); }}, {prox_segundos*1000});</script>",
    unsafe_allow_html=True
)






