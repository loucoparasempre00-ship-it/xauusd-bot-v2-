import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

STATE_FILE = Path("data/acompanhamento_autonomo_estado.json")
CANDLES_FILE = Path("data/xauusd_m5_fechados.csv")
RESULTS_FILE = Path("data/resultados_operacoes.csv")


def agora():
    return datetime.now(timezone.utc)


def data_utc(valor):
    dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def telegram(metodo, token, dados=None):
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    corpo = None

    if dados is not None:
        corpo = urllib.parse.urlencode(dados).encode("utf-8")

    pedido = urllib.request.Request(url, data=corpo)

    with urllib.request.urlopen(pedido, timeout=25) as resposta:
        resultado = json.loads(resposta.read().decode("utf-8"))

    if not resultado.get("ok"):
        raise RuntimeError(f"Falha no Telegram: {metodo}")

    return resultado["result"]


def avisar(token, chat_id, mensagem):
    telegram("sendMessage", token, {
        "chat_id": str(chat_id),
        "text": mensagem
    })


def carregar_estado():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if STATE_FILE.exists():
        estado = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        estado.setdefault("ultimo_update", 0)
        estado.setdefault("operacao", None)
        estado.setdefault("ultimo_candle", "")
        estado.setdefault("ultimo_alerta", "")
        return estado

    return {
        "ultimo_update": 0,
        "operacao": None,
        "ultimo_candle": "",
        "ultimo_alerta": ""
    }


def salvar_estado(estado):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def numero(texto):
    return float(texto.replace(",", "."))


def extrair_nivel(texto, rotulos):
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = texto.replace("&nbsp;", " ")

    for rotulo in rotulos:
        padrao = (
            rotulo
            + r"\s*[:=@\-]?\s*"
            + r"([0-9]+(?:[.,][0-9]+)?)"
        )
        achou = re.search(padrao, texto, re.IGNORECASE)

        if achou:
            return numero(achou.group(1))

    return None


def interpretar_sinal(texto):
    if not texto:
        return None

    texto_limpo = re.sub(r"<[^>]+>", " ", texto)
    texto_limpo = texto_limpo.replace("&nbsp;", " ")
    maiusculo = texto_limpo.upper()

    if "COMPRA" in maiusculo:
        direcao = "COMPRA"
    elif "VENDA" in maiusculo:
        direcao = "VENDA"
    else:
        return None

    entrada = extrair_nivel(
        texto_limpo,
        ["Entrada", "Preço de entrada", "Preco de entrada"]
    )

    stop = extrair_nivel(
        texto_limpo,
        ["Stop-loss", "Stop loss", "Stop", "SL"]
    )

    alvo = extrair_nivel(
        texto_limpo,
        ["Take-profit", "Take profit", "Alvo", "Target", "TP"]
    )

    if entrada is None or stop is None or alvo is None:
        return None

    if direcao == "COMPRA":
        if not stop < entrada < alvo:
            return None
    else:
        if not alvo < entrada < stop:
            return None

    return {
        "direcao": direcao,
        "entrada_sinal": entrada,
        "stop": stop,
        "alvo": alvo
    }


def registrar_resultado(op, resultado, preco, candle):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    campos = [
        "id",
        "direcao",
        "entrada_real",
        "entrada_sinal",
        "stop_original",
        "alvo_original",
        "resultado",
        "preco_referencia",
        "candle_utc",
        "ativado_utc",
        "encerrado_utc"
    ]

    existe = RESULTS_FILE.exists()

    with RESULTS_FILE.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        if not existe:
            escritor.writeheader()

        escritor.writerow({
            "id": op["id"],
            "direcao": op["direcao"],
            "entrada_real": op["entrada_real"],
            "entrada_sinal": op["entrada_sinal"],
            "stop_original": op["stop"],
            "alvo_original": op["alvo"],
            "resultado": resultado,
            "preco_referencia": round(preco, 3),
            "candle_utc": candle,
            "ativado_utc": op["ativado_utc"],
            "encerrado_utc": agora().isoformat()
        })


def ativar_operacao(update, estado, token, chat_configurado):
    mensagem = update.get("message", {})
    chat_id = str(mensagem.get("chat", {}).get("id", ""))

    if chat_id != str(chat_configurado):
        return

    texto = str(mensagem.get("text", "")).strip()

    confirmacao = re.fullmatch(
        r"ENTREI\s+([0-9]+(?:[.,][0-9]+)?)",
        texto,
        re.IGNORECASE
    )

    if not confirmacao:
        return

    resposta = mensagem.get("reply_to_message", {})
    texto_original = resposta.get("text", "")

    sinal = interpretar_sinal(texto_original)

    if sinal is None:
        avisar(
            token,
            chat_id,
            "Não consegui identificar os níveis do sinal original. "
            "Responda diretamente à mensagem do sinal COMPRA/VENDA."
        )
        return

    if estado.get("operacao"):
        avisar(
            token,
            chat_id,
            "Já existe uma operação em acompanhamento. "
            "A atual precisa terminar antes de ativar outra."
        )
        return

    entrada_real = numero(confirmacao.group(1))

    if entrada_real <= 0:
        avisar(token, chat_id, "Preço de entrada inválido.")
        return

    if sinal["direcao"] == "COMPRA":
        valido = sinal["stop"] < entrada_real < sinal["alvo"]
    else:
        valido = sinal["alvo"] < entrada_real < sinal["stop"]

    if not valido:
        avisar(
            token,
            chat_id,
            "Sua entrada está fora da faixa entre o stop e o alvo "
            "originais. Confira o preço informado."
        )
        return

    agora_local = agora()
    proximo_minuto = (
        (agora_local.minute // 5) + 1
    ) * 5

    proxima_virada = agora_local.replace(
        second=0,
        microsecond=0
    )

    if proximo_minuto >= 60:
        proxima_virada = (
            proxima_virada.replace(minute=0)
            + timedelta(hours=1)
        )
    else:
        proxima_virada = proxima_virada.replace(
            minute=proximo_minuto
        )

    operacao = {
        "id": str(update["update_id"]),
        "direcao": sinal["direcao"],
        "entrada_real": entrada_real,
        "entrada_sinal": sinal["entrada_sinal"],
        "stop": sinal["stop"],
        "alvo": sinal["alvo"],
        "ativado_utc": agora_local.isoformat(),
        "primeiro_candle": proxima_virada.isoformat()
    }

    estado["operacao"] = operacao
    estado["ultimo_candle"] = ""
    estado["ultimo_alerta"] = ""

    avisar(
        token,
        chat_id,
        "🟢 ACOMPANHAMENTO ATIVADO\n\n"
        f"Direção: {operacao['direcao']}\n"
        f"Sua entrada: {entrada_real:.3f}\n"
        f"Entrada do sinal: {operacao['entrada_sinal']:.3f}\n"
        f"Stop original: {operacao['stop']:.3f}\n"
        f"Alvo original: {operacao['alvo']:.3f}\n\n"
        "O monitor está ativo. Não envia ordens e não altera os níveis."
    )


def ler_candles():
    if not CANDLES_FILE.exists():
        raise FileNotFoundError(
            "Não encontrei data/xauusd_m5_fechados.csv"
        )

    with CANDLES_FILE.open(
        newline="",
        encoding="utf-8-sig"
    ) as arquivo:
        linhas = list(csv.DictReader(arquivo))

    candles = []

    for linha in linhas:
        horario = linha.get("timestamp") or linha.get("timestamp_utc")

        if not horario:
            continue

        candles.append({
            "horario": data_utc(horario),
            "high": float(linha["high"]),
            "low": float(linha["low"]),
            "close": float(linha["close"])
        })

    return sorted(candles, key=lambda x: x["horario"])


def acompanhar(estado, token):
    op = estado.get("operacao")

    if not op:
        print("Nenhuma operação ativa.")
        return

    candles = ler_candles()
    inicio = data_utc(op["primeiro_candle"])
    ultimo = estado.get("ultimo_candle", "")

    for candle in candles:
        horario = candle["horario"]
        horario_texto = horario.isoformat()

        if horario < inicio:
            continue

        if ultimo and horario_texto <= ultimo:
            continue

        if op["direcao"] == "COMPRA":
            stop_atingido = candle["low"] <= op["stop"]
            alvo_atingido = candle["high"] >= op["alvo"]
        else:
            stop_atingido = candle["high"] >= op["stop"]
            alvo_atingido = candle["low"] <= op["alvo"]

        if stop_atingido and alvo_atingido:
            registrar_resultado(
                op, "AMBIGUO", candle["close"], horario_texto
            )

            avisar(
                token,
                op_chat(estado),
                "⚠️ RESULTADO AMBÍGUO\n"
                "O mesmo candle M5 alcançou stop e alvo. "
                "Não é possível saber a ordem dos movimentos apenas "
                "pelos dados OHLC. Confira sua corretora."
            )

            estado["operacao"] = None
            estado["ultimo_candle"] = horario_texto
            return

        if stop_atingido or alvo_atingido:
            resultado = "STOP" if stop_atingido else "ALVO"
            preco = op["stop"] if stop_atingido else op["alvo"]

            registrar_resultado(
                op, resultado, preco, horario_texto
            )

            simbolo = "🛑" if resultado == "STOP" else "🎯"

            avisar(
                token,
                op_chat(estado),
                f"{simbolo} OPERAÇÃO ENCERRADA PELO NÍVEL ORIGINAL\n\n"
                f"Resultado detectado: {resultado}\n"
                f"Direção: {op['direcao']}\n"
                f"Entrada real: {op['entrada_real']:.3f}\n"
                f"Nível atingido: {preco:.3f}\n"
                f"Candle: {horario_texto}\n\n"
                "Confirme o resultado na corretora. "
                "O monitor não executa ordens."
            )

            estado["operacao"] = None
            estado["ultimo_candle"] = horario_texto
            return

        risco = abs(op["entrada_real"] - op["stop"])
        ganho = abs(op["alvo"] - op["entrada_real"])
        fechamento = candle["close"]

        if risco > 0 and ganho > 0:
            if op["direcao"] == "COMPRA":
                progresso_stop = (
                    op["entrada_real"] - fechamento
                ) / risco
                progresso_alvo = (
                    fechamento - op["entrada_real"]
                ) / ganho
            else:
                progresso_stop = (
                    fechamento - op["entrada_real"]
                ) / risco
                progresso_alvo = (
                    op["entrada_real"] - fechamento
                ) / ganho

            alerta = ""

            if progresso_stop >= 0.75:
                alerta = "PERTO_STOP"
            elif progresso_alvo >= 0.75:
                alerta = "PERTO_ALVO"

            if alerta and alerta != estado["ultimo_alerta"]:
                texto_alerta = (
                    "⚠️ PREÇO SE APROXIMANDO DO STOP"
                    if alerta == "PERTO_STOP"
                    else "🎯 PREÇO SE APROXIMANDO DO ALVO"
                )

                avisar(
                    token,
                    op_chat(estado),
                    f"{texto_alerta}\n"
                    f"Fechamento M5: {fechamento:.3f}\n"
                    f"Stop original: {op['stop']:.3f}\n"
                    f"Alvo original: {op['alvo']:.3f}"
                )

                estado["ultimo_alerta"] = alerta

        estado["ultimo_candle"] = horario_texto
        print(
            f"Candle acompanhado: {horario_texto} "
            f"Fechamento: {fechamento:.3f}"
        )


def op_chat(estado):
    return estado.get("chat_id")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        raise RuntimeError("Secrets do Telegram não encontrados.")

    estado = carregar_estado()

    atualizacoes = telegram(
        "getUpdates",
        token,
        {
            "offset": str(int(estado["ultimo_update"]) + 1),
            "timeout": "0",
            "allowed_updates": json.dumps(["message"])
        }
    )

    for update in atualizacoes:
        try:
            ativar_operacao(
                update,
                estado,
                token,
                chat_id
            )
        finally:
            estado["ultimo_update"] = max(
                int(estado["ultimo_update"]),
                int(update["update_id"])
            )

    if estado.get("operacao"):
        acompanhar(estado, token)

    salvar_estado(estado)

    print("Acompanhamento executado.")
    print("Operação ativa:", bool(estado.get("operacao")))


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO NO ACOMPANHAMENTO: {erro}", file=sys.stderr)
        raise
