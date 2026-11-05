
import os
import re
import json
import zipfile
import io
import requests

from pathlib import Path
from datetime import datetime, timezone, timedelta

# =========================================================
# CONFIGURAÇÃO
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
API_KEY = os.environ["TWELVE_DATA_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]

TELEGRAM = f"https://api.telegram.org/bot{TOKEN}"
GITHUB = "https://api.github.com"

PASTA = Path("data")
PASTA.mkdir(exist_ok=True)

ARQUIVO_ESTADO = PASTA / "acompanhamento_autonomo_estado.json"
ARQUIVO_HISTORICO = PASTA / "resultados_operacoes.json"

estado = {
    "ultimo_update_id": 0,
    "aguardando_entrada": False,
    "operacao": None
}

historico = []


# =========================================================
# PERSISTÊNCIA
# =========================================================

def carregar():
    global estado, historico

    if ARQUIVO_ESTADO.exists():
        try:
            estado.update(
                json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))
            )
        except Exception:
            pass

    if ARQUIVO_HISTORICO.exists():
        try:
            historico = json.loads(
                ARQUIVO_HISTORICO.read_text(encoding="utf-8")
            )
        except Exception:
            historico = []


def salvar():
    ARQUIVO_ESTADO.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    ARQUIVO_HISTORICO.write_text(
        json.dumps(historico, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================================================
# TELEGRAM
# =========================================================

def enviar(texto):
    resposta = requests.post(
        f"{TELEGRAM}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": texto
        },
        timeout=20
    )
    resposta.raise_for_status()


def buscar_atualizacoes():
    resposta = requests.get(
        f"{TELEGRAM}/getUpdates",
        params={
            "offset": estado["ultimo_update_id"] + 1,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"])
        },
        timeout=20
    )

    resposta.raise_for_status()
    dados = resposta.json()

    if not dados.get("ok"):
        raise RuntimeError(f"Erro Telegram: {dados}")

    return dados.get("result", [])


# =========================================================
# BUSCAR O ÚLTIMO SINAL DO AGENTE GEMINI
# =========================================================

def github_get(url):
    resposta = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        },
        timeout=30
    )
    resposta.raise_for_status()
    return resposta


def ultimo_sinal():
    # Busca a execução bem-sucedida mais recente do agente.
    url = (
        f"{GITHUB}/repos/{REPO}/actions/workflows/"
        "agente-gemini-xau.yml/runs"
    )

    dados = github_get(url + "?status=success&per_page=10").json()
    execucoes = dados.get("workflow_runs", [])

    if not execucoes:
        raise RuntimeError(
            "Não encontrei execução bem-sucedida do Agente Gemini."
        )

    # Percorre execuções recentes até encontrar o artefato esperado.
    for execucao in execucoes:
        run_id = execucao["id"]

        artefatos = github_get(
            f"{GITHUB}/repos/{REPO}/actions/runs/"
            f"{run_id}/artifacts"
        ).json().get("artifacts", [])

        artefato = next(
            (
                item for item in artefatos
                if item["name"] == "resultado-agente-gemini"
                and not item.get("expired", False)
            ),
            None
        )

        if not artefato:
            continue

        conteudo = github_get(
            f"{GITHUB}/repos/{REPO}/actions/artifacts/"
            f"{artefato['id']}/zip"
        ).content

        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
            nomes = arquivo.namelist()

            caminho = next(
                (
                    nome for nome in nomes
                    if nome.endswith("decisao_gemini.json")
                ),
                None
            )

            if not caminho:
                continue

            sinal = json.loads(
                arquivo.read(caminho).decode("utf-8")
            )

            return sinal

    raise RuntimeError(
        "Não encontrei decisao_gemini.json no artefato "
        "resultado-agente-gemini."
    )


def campo(dados, nomes):
    for nome in nomes:
        if nome in dados and dados[nome] is not None:
            return dados[nome]
    return None


def preparar_sinal(dados):
    direcao = campo(
        dados,
        ["decisao_gemini", "direcao", "decisao", "sinal"]
    )

    entrada = campo(
        dados,
        ["entrada", "preco_entrada", "entry"]
    )

    stop = campo(
        dados,
        ["stop_loss", "stop", "sl"]
    )

    alvo = campo(
        dados,
        ["take_profit", "alvo", "target", "tp"]
    )

    if not direcao or stop is None or alvo is None:
        raise RuntimeError(
            "O último sinal não contém direção, stop e alvo "
            "nos campos esperados."
        )

    direcao = str(direcao).upper()

    if "COMPRA" in direcao or "BUY" in direcao:
        direcao = "COMPRA"
    elif "VENDA" in direcao or "SELL" in direcao:
        direcao = "VENDA"
    else:
        raise RuntimeError(f"Direção não reconhecida: {direcao}")

    return {
        "direcao": direcao,
        "entrada_sinal": float(entrada) if entrada is not None else None,
        "stop": float(stop),
        "alvo": float(alvo)
    }


# =========================================================
# INDICADORES M5
# =========================================================

def ema(valores, periodo):
    if len(valores) < periodo:
        return None

    fator = 2 / (periodo + 1)
    resultado = sum(valores[:periodo]) / periodo

    for valor in valores[periodo:]:
        resultado = valor * fator + resultado * (1 - fator)

    return resultado


def rsi(valores, periodo=14):
    if len(valores) <= periodo:
        return None

    ganhos = []
    perdas = []

    for i in range(1, len(valores)):
        variacao = valores[i] - valores[i - 1]
        ganhos.append(max(variacao, 0))
        perdas.append(max(-variacao, 0))

    media_ganho = sum(ganhos[:periodo]) / periodo
    media_perda = sum(perdas[:periodo]) / periodo

    for i in range(periodo, len(ganhos)):
        media_ganho = (
            media_ganho * (periodo - 1) + ganhos[i]
        ) / periodo

        media_perda = (
            media_perda * (periodo - 1) + perdas[i]
        ) / periodo

    if media_perda == 0:
        return 100.0

    forca = media_ganho / media_perda
    return 100 - (100 / (1 + forca))


def buscar_velas():
    resposta = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD",
            "interval": "5min",
            "outputsize": 100,
            "apikey": API_KEY,
            "timezone": "UTC"
        },
        timeout=30
    )

    resposta.raise_for_status()
    dados = resposta.json()

    if "values" not in dados:
        raise RuntimeError(f"Erro Twelve Data: {dados}")

    agora = datetime.now(timezone.utc)
    velas = []

    for item in dados["values"]:
        horario = datetime.strptime(
            item["datetime"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        # Só utiliza candles já fechados.
        if horario + timedelta(minutes=5) <= agora:
            velas.append({
                "horario": horario,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

    velas.sort(key=lambda x: x["horario"])
    return velas


# =========================================================
# ATIVAÇÃO DA OPERAÇÃO
# =========================================================

def ativar_operacao(preco):
    dados = ultimo_sinal()
    sinal = preparar_sinal(dados)

    operacao = {
        **sinal,
        "entrada_real": preco,
        "ativada_em": datetime.now(timezone.utc).isoformat(),
        "ultima_vela": None,
        "alertas": [],
        "status": "ATIVA"
    }

    estado["operacao"] = operacao
    estado["aguardando_entrada"] = False

    enviar(
        "ACOMPANHAMENTO ATIVADO\n\n"
        f"Direção: {operacao['direcao']}\n"
        f"Sua entrada: {preco:.3f}\n"
        f"Entrada do sinal: {operacao['entrada_sinal']}\n"
        f"Stop original: {operacao['stop']:.3f}\n"
        f"Alvo original: {operacao['alvo']:.3f}\n\n"
        "A condução inteligente M5 está ativa.\n"
        "Vou avisar sobre perda de força, possível reversão, "
        "stop ou alvo."
    )


# =========================================================
# RECEBER ENTREI E PREÇO
# =========================================================

def processar_mensagem(mensagem):
    chat = mensagem.get("chat", {})

    if str(chat.get("id")) != CHAT_ID:
        return

    texto = (mensagem.get("text") or "").strip()
    texto_upper = texto.upper()

    if texto_upper == "CANCELAR":
        if estado["aguardando_entrada"]:
            estado["aguardando_entrada"] = False
            enviar("Ativação cancelada.")
        return

    if texto_upper == "ENCERRAR":
        if estado.get("operacao"):
            operacao = estado["operacao"]
            operacao["status"] = "ENCERRADA MANUALMENTE"
            operacao["encerrada_em"] = datetime.now(
                timezone.utc
            ).isoformat()

            historico.append(operacao.copy())
            estado["operacao"] = None
            enviar("Acompanhamento encerrado manualmente.")
        else:
            enviar("Não existe operação ativa.")
        return

    if texto_upper == "ENTREI":
        if estado.get("operacao"):
            enviar("Já existe uma operação em acompanhamento.")
            return

        estado["aguardando_entrada"] = True
        enviar(
            "ENTRADA REGISTRADA COMO PENDENTE.\n\n"
            "Qual foi seu preço real de entrada?\n"
            "Exemplo: 4095.212\n\n"
            "Para cancelar, envie CANCELAR."
        )
        return

    if estado.get("aguardando_entrada"):
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", texto):
            enviar("Envie somente o preço. Exemplo: 4095.212")
            return

        preco = float(texto.replace(",", "."))

        try:
            ativar_operacao(preco)
        except Exception as erro:
            enviar(
                "Não consegui ativar o acompanhamento.\n"
                f"Motivo: {erro}\n\n"
                "Sua solicitação continua pendente. "
                "Tente novamente ou envie CANCELAR."
            )
        return


# =========================================================
# CONDUÇÃO INTELIGENTE
# =========================================================

def avisar_uma_vez(chave, mensagem):
    operacao = estado["operacao"]

    if chave not in operacao["alertas"]:
        enviar(mensagem)
        operacao["alertas"].append(chave)


def conduzir(velas):
    operacao = estado.get("operacao")

    if not operacao or operacao["status"] != "ATIVA":
        return

    if len(velas) < 30:
        return

    vela = velas[-1]
    horario = vela["horario"].isoformat()

    if operacao["ultima_vela"] == horario:
        return

    operacao["ultima_vela"] = horario

    direcao = operacao["direcao"]
    stop = operacao["stop"]
    alvo = operacao["alvo"]

    fechamentos = [v["close"] for v in velas]

    ema9 = ema(fechamentos, 9)
    ema21 = ema(fechamentos, 21)
    valor_rsi = rsi(fechamentos, 14)

    # -----------------------------------------
    # STOP E ALVO ORIGINAIS
    # -----------------------------------------

    if direcao == "COMPRA":
        tocou_stop = vela["low"] <= stop
        tocou_alvo = vela["high"] >= alvo
    else:
        tocou_stop = vela["high"] >= stop
        tocou_alvo = vela["low"] <= alvo

    if tocou_stop and tocou_alvo:
        operacao["status"] = "AMBIGUO"
        operacao["encerrada_em"] = datetime.now(
            timezone.utc
        ).isoformat()

        historico.append(operacao.copy())
        estado["operacao"] = None

        enviar(
            "ATENÇÃO: RESULTADO AMBÍGUO.\n"
            "A mesma vela M5 tocou stop e alvo. "
            "Não é possível determinar qual ocorreu primeiro."
        )
        return

    if tocou_stop:
        operacao["status"] = "STOP"
        operacao["encerrada_em"] = datetime.now(
            timezone.utc
        ).isoformat()

        historico.append(operacao.copy())
        estado["operacao"] = None

        enviar(
            "STOP ORIGINAL ATINGIDO.\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Stop: {stop:.3f}"
        )
        return

    if tocou_alvo:
        operacao["status"] = "ALVO"
        operacao["encerrada_em"] = datetime.now(
            timezone.utc
        ).isoformat()

        historico.append(operacao.copy())
        estado["operacao"] = None

        enviar(
            "ALVO ORIGINAL ATINGIDO.\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Alvo: {alvo:.3f}"
        )
        return

    # -----------------------------------------
    # ALERTAS DE CONDUÇÃO
    # -----------------------------------------

    preco = vela["close"]

    if direcao == "COMPRA":
        cruzamento_contrario = preco < ema21 and ema9 < ema21
        perda_forca = valor_rsi is not None and valor_rsi < 45 and preco < ema9
    else:
        cruzamento_contrario = preco > ema21 and ema9 > ema21
        perda_forca = valor_rsi is not None and valor_rsi > 55 and preco > ema9

    if cruzamento_contrario:
        avisar_uma_vez(
            "possivel_reversao",
            "ALERTA DE POSSÍVEL REVERSÃO\n\n"
            f"Operação: {direcao}\n"
            f"Preço M5: {preco:.3f}\n"
            f"EMA9: {ema9:.3f}\n"
            f"EMA21: {ema21:.3f}\n"
            "A estrutura curta está contra a operação. "
            "Avalie proteger o resultado."
        )

    elif perda_forca:
        avisar_uma_vez(
            "perda_forca",
            "ALERTA DE PERDA DE FORÇA\n\n"
            f"Operação: {direcao}\n"
            f"Preço M5: {preco:.3f}\n"
            f"RSI: {valor_rsi:.2f}\n"
            "O momentum está enfraquecendo. "
            "Observe a próxima vela."
        )


# =========================================================
# EXECUÇÃO
# =========================================================

def main():
    carregar()

    try:
        atualizacoes = buscar_atualizacoes()

        for atualizacao in atualizacoes:
            estado["ultimo_update_id"] = max(
                estado["ultimo_update_id"],
                atualizacao["update_id"]
            )

            mensagem = atualizacao.get("message")
            if mensagem:
                processar_mensagem(mensagem)

        if estado.get("operacao"):
            velas = buscar_velas()
            conduzir(velas)

    finally:
        salvar()


if __name__ == "__main__":
    main()
