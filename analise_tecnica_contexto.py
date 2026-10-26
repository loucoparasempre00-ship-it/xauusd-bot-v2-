import csv
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

ARQUIVO_ENTRADA = "data/xauusd_m5_fechados.csv"
ARQUIVO_SAIDA = "data/analise_tecnica_contexto.json"

ATR_PERIODO = 14
EMA_CURTA = 9
EMA_LONGA = 21
RSI_PERIODO = 14

# Critérios equilibrados: não gera sinal só porque uma média cruzou.
PONTUACAO_MINIMA = 4
DIFERENCA_MINIMA = 2


def numero(valor):
    return float(str(valor).strip().replace(",", "."))


def ler_data(valor):
    valor = valor.strip()

    formatos = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
    )

    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        data = None
        for formato in formatos:
            try:
                data = datetime.strptime(valor, formato)
                break
            except ValueError:
                continue

    if data is None:
        raise ValueError(f"Data não reconhecida: {valor}")

    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)

    return data.astimezone(timezone.utc)


def localizar_coluna(cabecalhos, alternativas):
    mapa = {
        nome.strip().lower(): nome
        for nome in cabecalhos
    }

    for alternativa in alternativas:
        if alternativa in mapa:
            return mapa[alternativa]

    raise ValueError(
        "Coluna não encontrada. Procuradas: "
        + ", ".join(alternativas)
        + ". Encontradas: "
        + ", ".join(cabecalhos)
    )


def carregar_candles():
    if not os.path.exists(ARQUIVO_ENTRADA):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {ARQUIVO_ENTRADA}. "
            "Execute primeiro o filtro de candles fechados."
        )

    with open(
        ARQUIVO_ENTRADA,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo)

        if not leitor.fieldnames:
            raise ValueError("CSV sem cabeçalho.")

        cabecalhos = leitor.fieldnames

        coluna_data = localizar_coluna(
            cabecalhos,
            ["datetime", "timestamp", "time", "date", "data", "hora"]
        )
        coluna_abertura = localizar_coluna(
            cabecalhos, ["open", "abertura"]
        )
        coluna_maxima = localizar_coluna(
            cabecalhos, ["high", "maxima", "máxima"]
        )
        coluna_minima = localizar_coluna(
            cabecalhos, ["low", "minima", "mínima"]
        )
        coluna_fechamento = localizar_coluna(
            cabecalhos, ["close", "fechamento", "preco_fechamento"]
        )

        candles = []

        for linha in leitor:
            try:
                candle = {
                    "tempo": ler_data(linha[coluna_data]),
                    "open": numero(linha[coluna_abertura]),
                    "high": numero(linha[coluna_maxima]),
                    "low": numero(linha[coluna_minima]),
                    "close": numero(linha[coluna_fechamento]),
                }

                if (
                    candle["high"] < candle["low"]
                    or candle["high"] < max(
                        candle["open"], candle["close"]
                    )
                    or candle["low"] > min(
                        candle["open"], candle["close"]
                    )
                ):
                    continue

                candles.append(candle)

            except (ValueError, TypeError, KeyError):
                continue

    candles.sort(key=lambda c: c["tempo"])

    # Remove registros repetidos pelo horário.
    unicos = {}
    for candle in candles:
        unicos[candle["tempo"]] = candle

    candles = list(unicos.values())

    if len(candles) < 100:
        raise ValueError(
            f"Poucos candles válidos: {len(candles)}. "
            "São necessários pelo menos 100."
        )

    return candles


def ema(valores, periodo):
    if len(valores) < periodo:
        return None

    multiplicador = 2 / (periodo + 1)
    resultado = sum(valores[:periodo]) / periodo

    for valor in valores[periodo:]:
        resultado = (
            valor * multiplicador
            + resultado * (1 - multiplicador)
        )

    return resultado


def calcular_rsi(fechamentos, periodo=RSI_PERIODO):
    if len(fechamentos) <= periodo:
        return None

    ganhos = []
    perdas = []

    for i in range(1, len(fechamentos)):
        diferenca = fechamentos[i] - fechamentos[i - 1]
        ganhos.append(max(diferenca, 0))
        perdas.append(max(-diferenca, 0))

    ganho_medio = sum(ganhos[:periodo]) / periodo
    perda_media = sum(perdas[:periodo]) / periodo

    for i in range(periodo, len(ganhos)):
        ganho_medio = (
            ganho_medio * (periodo - 1) + ganhos[i]
        ) / periodo

        perda_media = (
            perda_media * (periodo - 1) + perdas[i]
        ) / periodo

    if perda_media == 0:
        return 100.0

    return 100 - (100 / (1 + ganho_medio / perda_media))


def calcular_atr(candles, periodo=ATR_PERIODO):
    if len(candles) <= periodo:
        return None

    amplitudes = []

    for i in range(1, len(candles)):
        atual = candles[i]
        anterior = candles[i - 1]

        amplitude = max(
            atual["high"] - atual["low"],
            abs(atual["high"] - anterior["close"]),
            abs(atual["low"] - anterior["close"]),
        )

        amplitudes.append(amplitude)

    atr = sum(amplitudes[:periodo]) / periodo

    for amplitude in amplitudes[periodo:]:
        atr = (
            atr * (periodo - 1) + amplitude
        ) / periodo

    return atr


def agregar_candles(candles, minutos):
    grupos = defaultdict(list)

    for candle in candles:
        timestamp = int(candle["tempo"].timestamp())
        tamanho = minutos * 60
        inicio = (timestamp // tamanho) * tamanho
        grupos[inicio].append(candle)

    agregados = []

    for inicio in sorted(grupos):
        grupo = sorted(
            grupos[inicio],
            key=lambda c: c["tempo"]
        )

        # Só aceita candles agregados com todos os M5 esperados.
        if len(grupo) != minutos // 5:
            continue

        agregados.append({
            "tempo": datetime.fromtimestamp(
                inicio, tz=timezone.utc
            ),
            "open": grupo[0]["open"],
            "high": max(c["high"] for c in grupo),
            "low": min(c["low"] for c in grupo),
            "close": grupo[-1]["close"],
        })

    return agregados


def avaliar_tendencia(candles):
    if len(candles) < 30:
        return {
            "tendencia": "INDEFINIDA",
            "ema_curta": None,
            "ema_longa": None,
        }

    fechamentos = [c["close"] for c in candles]
    curta = ema(fechamentos, EMA_CURTA)
    longa = ema(fechamentos, EMA_LONGA)

    if curta is None or longa is None:
        tendencia = "INDEFINIDA"
    elif curta > longa:
        tendencia = "ALTA"
    elif curta < longa:
        tendencia = "BAIXA"
    else:
        tendencia = "LATERAL"

    return {
        "tendencia": tendencia,
        "ema_curta": curta,
        "ema_longa": longa,
    }


def analisar(candles):
    fechamentos = [c["close"] for c in candles]
    atual = candles[-1]
    anterior = candles[-2]

    tendencia_m5 = avaliar_tendencia(candles)

    candles_m15 = agregar_candles(candles, 15)
    candles_h1 = agregar_candles(candles, 60)

    tendencia_m15 = avaliar_tendencia(candles_m15)
    tendencia_h1 = avaliar_tendencia(candles_h1)

    rsi = calcular_rsi(fechamentos)
    atr = calcular_atr(candles)

    if atr is None or atr <= 0 or rsi is None:
        raise ValueError("Não foi possível calcular ATR ou RSI.")

    corpo = abs(atual["close"] - atual["open"])
    amplitude = atual["high"] - atual["low"]

    impulso_alta = (
        atual["close"] > atual["open"]
        and corpo >= atr * 0.45
    )

    impulso_baixa = (
        atual["close"] < atual["open"]
        and corpo >= atr * 0.45
    )

    rompeu_maxima = atual["close"] > max(
        c["high"] for c in candles[-6:-1]
    )

    rompeu_minima = atual["close"] < min(
        c["low"] for c in candles[-6:-1]
    )

    compra = 0
    venda = 0
    motivos_compra = []
    motivos_venda = []

    # Tendência principal.
    if tendencia_m5["tendencia"] == "ALTA":
        compra += 1
        motivos_compra.append("EMA9 acima da EMA21 no M5")

    if tendencia_m5["tendencia"] == "BAIXA":
        venda += 1
        motivos_venda.append("EMA9 abaixo da EMA21 no M5")

    # Contexto de timeframes superiores.
    if tendencia_m15["tendencia"] == "ALTA":
        compra += 1
        motivos_compra.append("Contexto M15 de alta")

    if tendencia_m15["tendencia"] == "BAIXA":
        venda += 1
        motivos_venda.append("Contexto M15 de baixa")

    if tendencia_h1["tendencia"] == "ALTA":
        compra += 1
        motivos_compra.append("Contexto H1 de alta")

    if tendencia_h1["tendencia"] == "BAIXA":
        venda += 1
        motivos_venda.append("Contexto H1 de baixa")

    # Força do candle atual.
    if impulso_alta:
        compra += 1
        motivos_compra.append("Impulso comprador")

    if impulso_baixa:
        venda += 1
        motivos_venda.append("Impulso vendedor")

    # Rompimento de estrutura recente.
    if rompeu_maxima:
        compra += 1
        motivos_compra.append("Rompimento da máxima recente")

    if rompeu_minima:
        venda += 1
        motivos_venda.append("Rompimento da mínima recente")

    # RSI é usado como filtro, não como sinal isolado.
    if 50 <= rsi <= 70:
        compra += 1
        motivos_compra.append("RSI favorável à compra")

    if 30 <= rsi <= 50:
        venda += 1
        motivos_venda.append("RSI favorável à venda")

    direcao = "SEM_SINAL"
    motivos = []
    entrada = atual["close"]
    stop = None
    alvo = None

    if (
        compra >= PONTUACAO_MINIMA
        and compra - venda >= DIFERENCA_MINIMA
    ):
        direcao = "COMPRA"
        motivos = motivos_compra
        stop = entrada - atr
        alvo = entrada + (atr * 1.5)

    elif (
        venda >= PONTUACAO_MINIMA
        and venda - compra >= DIFERENCA_MINIMA
    ):
        direcao = "VENDA"
        motivos = motivos_venda
        stop = entrada + atr
        alvo = entrada - (atr * 1.5)

    return {
        "ativo": "XAUUSD",
        "timeframe": "M5",
        "candle_utc": atual["tempo"].isoformat(),
        "preco": round(entrada, 3),
        "decisao": direcao,
        "pontuacao_compra": compra,
        "pontuacao_venda": venda,
        "tendencia_m5": tendencia_m5["tendencia"],
        "tendencia_m15": tendencia_m15["tendencia"],
        "tendencia_h1": tendencia_h1["tendencia"],
        "rsi_14": round(rsi, 2),
        "atr_14": round(atr, 3),
        "stop": round(stop, 3) if stop is not None else None,
        "alvo": round(alvo, 3) if alvo is not None else None,
        "relacao_risco_retorno": "1:1.5",
        "motivos": motivos,
        "envio_telegram": False,
        "execucao_ordens": False,
        "probabilidade_validada": None,
        "aviso": (
            "Análise técnica experimental. "
            "Não representa probabilidade comprovada de lucro."
        ),
    }


def main():
    candles = carregar_candles()
    resultado = analisar(candles)

    os.makedirs(os.path.dirname(ARQUIVO_SAIDA), exist_ok=True)

    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(
            resultado,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    print(f"Candles analisados: {len(candles)}")
    print(f"Último candle UTC: {candles[-1]['tempo'].isoformat()}")
    print(f"Tendência M5: {resultado['tendencia_m5']}")
    print(f"Tendência M15: {resultado['tendencia_m15']}")
    print(f"Tendência H1: {resultado['tendencia_h1']}")
    print(f"RSI: {resultado['rsi_14']}")
    print(f"ATR: {resultado['atr_14']}")
    print(f"Decisão: {resultado['decisao']}")
    print(f"Arquivo gerado: {ARQUIVO_SAIDA}")


if __name__ == "__main__":
    main()
