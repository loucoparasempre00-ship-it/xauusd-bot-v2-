import os
import re
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

PASTA = Path("data")
PASTA.mkdir(exist_ok=True)

ARQUIVO_ESTADO = PASTA / "acompanhamento_autonomo_estado.json"
ARQUIVO_HISTORICO = PASTA / "resultados_operacoes.json"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
API_KEY = os.environ["TWELVE_DATA_API_KEY"]

TELEGRAM = f"https://api.telegram.org/bot{TOKEN}"


def carregar(caminho, padrao):
    if caminho.exists():
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except Exception:
            pass
    return padrao


def salvar(caminho, dados):
    caminho.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


estado = carregar(ARQUIVO_ESTADO, {
    "ultimo_update_id": 0,
    "operacao": None
})

historico = carregar(ARQUIVO_HISTORICO, [])


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


def numero(texto):
    valor = str(texto).strip().replace(" ", "")

    if "," in valor and "." in valor:
        valor = valor.replace(",", "")
    elif "," in valor:
        partes = valor.split(",")
        if len(partes[-1]) == 3:
            valor = valor.replace(",", "")
        else:
            valor = valor.replace(",", ".")

    return float(valor)


def extrair_nivel(texto, rotulos):
    for rotulo in rotulos:
        padrao = (
            rf"(?:{rotulo})\s*[:=\-]?\s*"
            rf"(?:US\$|\$)?\s*([\d.,]+)"
        )
        encontrado = re.search(padrao, texto, re.IGNORECASE)

        if encontrado:
            try:
                return numero(encontrado.group(1))
            except ValueError:
                pass

    return None


def interpretar_sinal(texto):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = texto.upper()

    if "COMPRA" in texto or "BUY" in texto:
        direcao = "COMPRA"
    elif "VENDA" in texto or "SELL" in texto:
        direcao = "VENDA"
    else:
        return None

    entrada_sinal = extrair_nivel(
        texto, [r"ENTRADA", r"ENTRY"]
    )

    stop = extrair_nivel(
        texto, [
            r"STOP[\s-]*LOSS",
            r"STOP",
            r"\bSL\b"
        ]
    )

    alvo = extrair_nivel(
        texto, [
            r"TAKE[\s-]*PROFIT",
            r"ALVO",
            r"TARGET",
            r"\bTP\b"
        ]
    )

    if stop is None or alvo is None:
        return None

    return {
        "direcao": direcao,
        "entrada_sinal": entrada_sinal,
        "stop": stop,
        "alvo": alvo
    }


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
        raise RuntimeError(f"Telegram retornou erro: {dados}")

    return dados.get("result", [])


def processar_mensagem(mensagem):
    chat = mensagem.get("chat", {})
    if str(chat.get("id")) != CHAT_ID:
        return

    texto = (mensagem.get("text") or "").strip()
    resposta = mensagem.get("reply_to_message")

    comando = re.match(
        r"^ENTREI\s+([\d.,]+)\s*$",
        texto,
        re.IGNORECASE
    )

    if not comando:
        return

    if estado.get("operacao"):
        enviar(
            "Já existe uma operação em acompanhamento. "
            "Aguarde o encerramento dela antes de ativar outra."
        )
        return

    if not resposta or not resposta.get("text"):
        enviar(
            "Para ativar, responda diretamente à mensagem original "
            "do sinal com: ENTREI 4065.250"
        )
        return

    sinal = interpretar_sinal(resposta["text"])

    if not sinal:
        enviar(
            "Não consegui identificar COMPRA/VENDA, stop e alvo "
            "na mensagem original. Nenhuma operação foi ativada."
        )
        return

    try:
        entrada_real = numero(comando.group(1))
    except ValueError:
        enviar("Preço de entrada inválido.")
        return

    estado["operacao"] = {
        "direcao": sinal["direcao"],
        "entrada_real": entrada_real,
        "entrada_sinal": sinal["entrada_sinal"],
        "stop": sinal["stop"],
        "alvo": sinal["alvo"],
        "ativada_em": datetime.now(timezone.utc).isoformat(),
        "status": "ATIVA",
        "ultima_vela": None
    }

    enviar(
        "ACOMPANHAMENTO ATIVADO\n\n"
        f"Direção: {sinal['direcao']}\n"
        f"Sua entrada: {entrada_real}\n"
        f"Stop original: {sinal['stop']}\n"
        f"Alvo original: {sinal['alvo']}\n\n"
        "Vou acompanhar a operação. Nenhuma ordem será enviada."
    )


def buscar_vela_fechada():
    resposta = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD",
            "interval": "5min",
            "outputsize": 5,
            "apikey": API_KEY,
            "timezone": "UTC"
        },
        timeout=25
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

        if horario + timedelta(minutes=5) <= agora:
            velas.append({
                "horario": horario,
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

    if not velas:
        return None

    velas.sort(key=lambda x: x["horario"])
    return velas[-1]


def acompanhar():
    operacao = estado.get("operacao")

    if not operacao or operacao.get("status") != "ATIVA":
        return

    vela = buscar_vela_fechada()

    if not vela:
        return

    horario = vela["horario"].isoformat()

    if operacao.get("ultima_vela") == horario:
        return

    operacao["ultima_vela"] = horario

    direcao = operacao["direcao"]
    stop = operacao["stop"]
    alvo = operacao["alvo"]

    tocou_stop = False
    tocou_alvo = False

    if direcao == "COMPRA":
        tocou_stop = vela["low"] <= stop
        tocou_alvo = vela["high"] >= alvo
    else:
        tocou_stop = vela["high"] >= stop
        tocou_alvo = vela["low"] <= alvo

    resultado = None

    if tocou_stop and tocou_alvo:
        resultado = "AMBIGUO"
        mensagem = (
            "OPERAÇÃO COM RESULTADO AMBÍGUO\n"
            "A mesma vela tocou stop e alvo. "
            "Não é possível saber qual foi atingido primeiro."
        )

    elif tocou_stop:
        resultado = "STOP"
        mensagem = (
            "STOP ATINGIDO\n"
            f"Direção: {direcao}\n"
            f"Entrada real: {operacao['entrada_real']}\n"
            f"Stop: {stop}"
        )

    elif tocou_alvo:
        resultado = "ALVO"
        mensagem = (
            "ALVO ATINGIDO\n"
            f"Direção: {direcao}\n"
            f"Entrada real: {operacao['entrada_real']}\n"
            f"Alvo: {alvo}"
        )

    if resultado:
        operacao["status"] = resultado
        operacao["encerrada_em"] = datetime.now(
            timezone.utc
        ).isoformat()

        historico.append(operacao.copy())
        estado["operacao"] = None

        enviar(mensagem)


def main():
    atualizacoes = buscar_atualizacoes()

    for atualizacao in atualizacoes:
        estado["ultimo_update_id"] = max(
            estado["ultimo_update_id"],
            atualizacao["update_id"]
        )

        mensagem = atualizacao.get("message")
        if mensagem:
            processar_mensagem(mensagem)

    acompanhar()

    salvar(ARQUIVO_ESTADO, estado)
    salvar(ARQUIVO_HISTORICO, historico)


if __name__ == "__main__":
    main()
