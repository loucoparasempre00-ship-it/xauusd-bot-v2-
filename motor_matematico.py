from datetime import datetime
from zoneinfo import ZoneInfo
from math import isfinite


def calcular_ema(valores, periodo):
    if periodo < 1 or len(valores) < periodo:
        raise ValueError("Valores insuficientes para calcular a EMA.")

    valores = [float(v) for v in valores]

    if not all(isfinite(v) for v in valores):
        raise ValueError("Existem valores inválidos.")

    alpha = 2 / (periodo + 1)
    ema = valores[0]

    for valor in valores[1:]:
        ema = alpha * valor + (1 - alpha) * ema

    return ema


def calcular_macd(
    valores,
    periodo_rapido=12,
    periodo_lento=26,
    periodo_sinal=9,
):
    if len(valores) < periodo_lento + periodo_sinal:
        raise ValueError("Valores insuficientes para o MACD.")

    valores = [float(v) for v in valores]

    def serie_ema(lista, periodo):
        alpha = 2 / (periodo + 1)
        resultado = [lista[0]]

        for valor in lista[1:]:
            resultado.append(
                alpha * valor + (1 - alpha) * resultado[-1]
            )

        return resultado

    ema_rapida = serie_ema(valores, periodo_rapido)
    ema_lenta = serie_ema(valores, periodo_lento)

    linha_macd = [
        rapida - lenta
        for rapida, lenta in zip(ema_rapida, ema_lenta)
    ]

    linha_sinal = serie_ema(linha_macd, periodo_sinal)

    return {
        "linha": linha_macd[-1],
        "sinal": linha_sinal[-1],
        "histograma": linha_macd[-1] - linha_sinal[-1],
    }


def calcular_vwap_ancorada(
    candles,
    abertura_nova_york="09:30",
):
    fuso_ny = ZoneInfo("America/New_York")

    if not candles:
        raise ValueError("Nenhum candle recebido.")

    if "T" in abertura_nova_york:
        abertura = datetime.fromisoformat(abertura_nova_york)

        if abertura.tzinfo is None:
            abertura = abertura.replace(tzinfo=fuso_ny)
        else:
            abertura = abertura.astimezone(fuso_ny)
    else:
        hora, minuto = map(int, abertura_nova_york.split(":"))
        abertura = None

    soma_preco_volume = 0.0
    soma_volume = 0.0

    for candle in candles:
        instante = datetime.fromisoformat(
            candle["timestamp"].replace("Z", "+00:00")
        )

        if instante.tzinfo is None:
            raise ValueError("Timestamp precisa conter fuso horário.")

        instante_ny = instante.astimezone(fuso_ny)

        if abertura is None:
            inicio = datetime(
                instante_ny.year,
                instante_ny.month,
                instante_ny.day,
                hora,
                minuto,
                tzinfo=fuso_ny,
            )
        else:
            inicio = abertura

        if instante_ny < inicio:
            continue

        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        volume = float(candle["volume"])

        if (
            not all(isfinite(v) for v in (high, low, close, volume))
            or high < low
            or volume <= 0
        ):
            raise ValueError("Preço ou volume inválido.")

        preco_tipico = (high + low + close) / 3
        soma_preco_volume += preco_tipico * volume
        soma_volume += volume

    if soma_volume <= 0:
        raise ValueError(
            "Não existem candles válidos desde a abertura ancorada."
        )

    return soma_preco_volume / soma_volume


def detectar_consolidacao(candles, periodo=20, limite_percentual=0.002):
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
        not all(isfinite(v) for v in (maximo, minimo, fechamento))
        or fechamento <= 0
        or maximo < minimo
    ):
        return False

    amplitude_percentual = (maximo - minimo) / fechamento

    return amplitude_percentual <= limite_percentual


def calcular_niveis_risco(
    direcao,
    preco_entrada,
    atr,
    stop_estrutura=None,
    multiplo_atr=1.5,
):
    if (
        not all(
            isfinite(float(v))
            for v in (preco_entrada, atr, multiplo_atr)
        )
        or preco_entrada <= 0
        or atr <= 0
        or multiplo_atr <= 0
    ):
        raise ValueError("Entrada, ATR ou multiplicador inválido.")

    direcao = direcao.upper()
    distancia_atr = atr * multiplo_atr

    if direcao == "COMPRA":
        stop_atr = preco_entrada - distancia_atr

        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)
            if not isfinite(stop_estrutura) or stop_estrutura >= preco_entrada:
                raise ValueError("Stop estrutural inválido para compra.")
            stop = min(stop_atr, stop_estrutura)
        else:
            stop = stop_atr

        risco = preco_entrada - stop
        alvo_1_5r = preco_entrada + risco * 1.5
        alvo_2r = preco_entrada + risco * 2.0

    elif direcao == "VENDA":
        stop_atr = preco_entrada + distancia_atr

        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)
            if not isfinite(stop_estrutura) or stop_estrutura <= preco_entrada:
                raise ValueError("Stop estrutural inválido para venda.")
            stop = max(stop_atr, stop_estrutura)
        else:
            stop = stop_atr

        risco = stop - preco_entrada
        alvo_1_5r = preco_entrada - risco * 1.5
        alvo_2r = preco_entrada - risco * 2.0

    else:
        raise ValueError("Direção deve ser COMPRA ou VENDA.")

    return {
        "stop": round(stop, 5),
        "risco": round(risco, 5),
        "alvo_1_5R": round(alvo_1_5r, 5),
        "alvo_2R": round(alvo_2r, 5),
    }


if __name__ == "__main__":
    print("Motor matemático V2 carregado.")
    print("Sem conexão com corretora e sem execução de ordens.")
