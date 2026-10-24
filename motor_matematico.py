from datetime import datetime
from zoneinfo import ZoneInfo
from math import isfinite


def calcular_ema(valores, periodo):
    if periodo < 1 or len(valores) < periodo:
        raise ValueError("Valores insuficientes para EMA.")

    valores = [float(v) for v in valores]
    if not all(isfinite(v) for v in valores):
        raise ValueError("Valor inválido para EMA.")

    alpha = 2 / (periodo + 1)
    ema = valores[0]

    for valor in valores[1:]:
        ema = alpha * valor + (1 - alpha) * ema

    return ema


def calcular_macd(valores, periodo_rapido=12,
                  periodo_lento=26, periodo_sinal=9):
    if len(valores) < periodo_lento + periodo_sinal:
        raise ValueError("Valores insuficientes para MACD.")

    def serie_ema(lista, periodo):
        alpha = 2 / (periodo + 1)
        resultado = [lista[0]]
        for valor in lista[1:]:
            resultado.append(
                alpha * valor + (1 - alpha) * resultado[-1]
            )
        return resultado

    valores = [float(v) for v in valores]
    if not all(isfinite(v) for v in valores):
        raise ValueError("Valor inválido para MACD.")

    rapida = serie_ema(valores, periodo_rapido)
    lenta = serie_ema(valores, periodo_lento)
    linha = [a - b for a, b in zip(rapida, lenta)]
    sinal = serie_ema(linha, periodo_sinal)

    return {
        "linha": linha[-1],
        "sinal": sinal[-1],
        "histograma": linha[-1] - sinal[-1],
    }


def calcular_vwap_ancorada(candles, abertura_nova_york="09:30"):
    if not candles:
        raise ValueError("Nenhum candle recebido.")

    fuso = ZoneInfo("America/New_York")

    if "T" in abertura_nova_york:
        inicio = datetime.fromisoformat(abertura_nova_york)
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=fuso)
        else:
            inicio = inicio.astimezone(fuso)
    else:
        hora, minuto = map(int, abertura_nova_york.split(":"))
        inicio = None

    soma_pv = 0.0
    soma_volume = 0.0

    for candle in candles:
        instante = datetime.fromisoformat(
            candle["timestamp"].replace("Z", "+00:00")
        )
        if instante.tzinfo is None:
            raise ValueError("Timestamp sem fuso horário.")

        instante = instante.astimezone(fuso)

        if inicio is None:
            ancora = datetime(
                instante.year, instante.month, instante.day,
                hora, minuto, tzinfo=fuso
            )
        else:
            ancora = inicio

        if instante < ancora:
            continue

        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        volume = float(candle["volume"])

        if (
            not all(isfinite(v) for v in
                    (high, low, close, volume))
            or high < low
            or volume <= 0
        ):
            raise ValueError("Preço ou volume inválido.")

        tipico = (high + low + close) / 3
        soma_pv += tipico * volume
        soma_volume += volume

    if soma_volume == 0:
        raise ValueError("Sem volume desde a ancoragem.")

    return soma_pv / soma_volume


def detectar_consolidacao(candles, periodo=20,
                          limite_percentual=0.002):
    if len(candles) < periodo:
        return False

    recentes = candles[-periodo:]

    try:
        maximo = max(float(c["high"]) for c in recentes)
        minimo = min(float(c["low"]) for c in recentes)
        fechamento = float(recentes[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return False

    if (
        not all(isfinite(v) for v in
                (maximo, minimo, fechamento))
        or fechamento <= 0
        or maximo < minimo
    ):
        return False

    return (maximo - minimo) / fechamento <= limite_percentual


def calcular_niveis_risco(direcao, preco_entrada, atr,
                          stop_estrutura=None, multiplo_atr=1.5):
    valores = [preco_entrada, atr, multiplo_atr]
    if not all(isfinite(float(v)) for v in valores):
        raise ValueError("Valores de risco inválidos.")

    preco_entrada = float(preco_entrada)
    atr = float(atr)
    multiplo_atr = float(multiplo_atr)

    if preco_entrada <= 0 or atr <= 0 or multiplo_atr <= 0:
        raise ValueError("Entrada, ATR e multiplicador devem ser positivos.")

    direcao = direcao.upper()

    if direcao == "COMPRA":
        stop = preco_entrada - atr * multiplo_atr
        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)
            if not isfinite(stop_estrutura) or stop_estrutura >= preco_entrada:
                raise ValueError("Stop estrutural inválido para compra.")
            stop = min(stop, stop_estrutura)
        risco = preco_entrada - stop
        alvo_15 = preco_entrada + risco * 1.5
        alvo_2 = preco_entrada + risco * 2

    elif direcao == "VENDA":
        stop = preco_entrada + atr * multiplo_atr
        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)
            if not isfinite(stop_estrutura) or stop_estrutura <= preco_entrada:
                raise ValueError("Stop estrutural inválido para venda.")
            stop = max(stop, stop_estrutura)
        risco = stop - preco_entrada
        alvo_15 = preco_entrada - risco * 1.5
        alvo_2 = preco_entrada - risco * 2

    else:
        raise ValueError("Direção deve ser COMPRA ou VENDA.")

    return {
        "stop": round(stop, 5),
        "risco": round(risco, 5),
        "alvo_1_5R": round(alvo_15, 5),
        "alvo_2R": round(alvo_2, 5),
    }


def detectar_cruzamento(ema6, ema20, macd_histograma, preco, vwap,
                        ema6_anterior, ema20_anterior,
                        consolidacao=False):
    if consolidacao:
        return None

    valores = [
        ema6, ema20, macd_histograma, preco, vwap,
        ema6_anterior, ema20_anterior
    ]
    if not all(isfinite(float(v)) for v in valores):
        return None

    if (
        ema6_anterior <= ema20_anterior
        and ema6 > ema20
        and macd_histograma > 0
        and preco > vwap
    ):
        return "COMPRA"

    if (
        ema6_anterior >= ema20_anterior
        and ema6 < ema20
        and macd_histograma < 0
        and preco < vwap
    ):
        return "VENDA"

    return None


def detectar_pullback(candles, ema6, ema20, macd_histograma,
                      vwap, consolidacao=False,
                      tolerancia_atr=0.25, atr=None):
    if consolidacao or len(candles) < 3 or atr is None:
        return None

    try:
        atr = float(atr)
        if not isfinite(atr) or atr <= 0:
            return None

        anterior = candles[-2]
        ultimo = candles[-1]

        o_ant = float(anterior["open"])
        c_ant = float(anterior["close"])
        abertura = float(ultimo["open"])
        maxima = float(ultimo["high"])
        minima = float(ultimo["low"])
        fechamento = float(ultimo["close"])

        valores = [
            ema6, ema20, macd_histograma, vwap,
            o_ant, c_ant, abertura, maxima, minima, fechamento
        ]
        if not all(isfinite(float(v)) for v in valores):
            return None
        if maxima < minima:
            return None

    except (KeyError, TypeError, ValueError):
        return None

    tolerancia = atr * tolerancia_atr
    tocou_media = (
        minima <= max(ema6, ema20) + tolerancia
        and maxima >= min(ema6, ema20) - tolerancia
    )

    if (
        tocou_media
        and ema6 > ema20
        and fechamento > abertura
        and fechamento > c_ant
        and fechamento > ema6
        and macd_histograma > 0
        and fechamento > vwap
    ):
        return "COMPRA"

    if (
        tocou_media
        and ema6 < ema20
        and fechamento < abertura
        and fechamento < c_ant
        and fechamento < ema6
        and macd_histograma < 0
        and fechamento < vwap
    ):
        return "VENDA"

    return None


if __name__ == "__main__":
    print("Motor matemático V2 carregado.")
    print("Sem conexão com corretora e sem execução de ordens.")
