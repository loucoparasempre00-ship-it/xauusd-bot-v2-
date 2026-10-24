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

def detectar_cruzamento(ema6, ema20, macd_histograma, preco, vwap,
                        ema6_anterior, ema20_anterior,
                        consolidacao=False):
    """
    Detecta cruzamento confirmado pelas condições atuais.
    Os valores devem vir de candles fechados.
    """
    if consolidacao:
        return None

    valores = [
        ema6, ema20, macd_histograma, preco, vwap,
        ema6_anterior, ema20_anterior
    ]

    if not all(isfinite(float(v)) for v in valores):
        return None

    cruzou_alta = (
        ema6_anterior <= ema20_anterior
        and ema6 > ema20
        and macd_histograma > 0
        and preco > vwap
    )

    cruzou_baixa = (
        ema6_anterior >= ema20_anterior
        and ema6 < ema20
        and macd_histograma < 0
        and preco < vwap
    )

    if cruzou_alta:
        return "COMPRA"

    if cruzou_baixa:
        return "VENDA"

    return None


def detectar_pullback(candles, ema6, ema20, macd_histograma, vwap,
                      consolidacao=False, tolerancia_atr=0.25,
                      atr=None):
    """
    Procura toque próximo da EMA 6/20 e retomada direcional.
    O último candle deve estar fechado.
    """
    if consolidacao or len(candles) < 3 or atr is None:
        return None

    if not isfinite(float(atr)) or atr <= 0:
        return None

    try:
        anterior = candles[-2]
        ultimo = candles[-1]

        o_anterior = float(anterior["open"])
        c_anterior = float(anterior["close"])
        o_ultimo = float(ultimo["open"])
        h_ultimo = float(ultimo["high"])
        l_ultimo = float(ultimo["low"])
        c_ultimo = float(ultimo["close"])

        valores = [
            ema6, ema20, macd_histograma, vwap,
            o_anterior, c_anterior, o_ultimo,
            h_ultimo, l_ultimo, c_ultimo
        ]

        if not all(isfinite(float(v)) for v in valores):
            return None

        if h_ultimo < l_ultimo:
            return None

    except (KeyError, TypeError, ValueError):
        return None

    tolerancia = float(atr) * tolerancia_atr
    tocou_media = (
        l_ultimo <= max(ema6, ema20) + tolerancia
        and h_ultimo >= min(ema6, ema20) - tolerancia
    )

    tendencia_alta = ema6 > ema20
    tendencia_baixa = ema6 < ema20

    retomada_alta = (
        c_ultimo > o_ultimo
        and c_ultimo > c_anterior
        and c_ultimo > ema6
        and macd_histograma > 0
        and c_ultimo > vwap
    )

    retomada_baixa = (
        c_ultimo < o_ultimo
        and c_ultimo < c_anterior
        and c_ultimo < ema6
        and macd_histograma < 0
        and c_ultimo < vwap
    )

    if tocou_media and tendencia_alta and retomada_alta:
        return "COMPRA"

    if tocou_media and tendencia_baixa and retomada_baixa:
        return "VENDA"

    return None
