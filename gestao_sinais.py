from math import isfinite


def calcular_atr(candles, periodo=14):
    """
    Calcula o ATR simples usando candles em ordem cronológica.
    Cada candle deve conter high, low e close.
    """
    if not isinstance(candles, list):
        raise ValueError("Candles devem ser fornecidos em uma lista.")

    if periodo < 1 or len(candles) < periodo + 1:
        raise ValueError(
            "São necessários pelo menos periodo + 1 candles para o ATR."
        )

    verdadeiros_ranges = []

    for i in range(1, len(candles)):
        anterior = candles[i - 1]
        atual = candles[i]

        try:
            maxima = float(atual["high"])
            minima = float(atual["low"])
            fechamento_anterior = float(anterior["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"OHLC inválido nos candles próximos ao índice {i}.")

        valores = (maxima, minima, fechamento_anterior)

        if not all(isfinite(v) and v > 0 for v in valores):
            raise ValueError(f"Preço inválido próximo ao índice {i}.")

        if maxima < minima:
            raise ValueError(f"Máxima menor que mínima no candle {i}.")

        tr = max(
            maxima - minima,
            abs(maxima - fechamento_anterior),
            abs(minima - fechamento_anterior),
        )

        verdadeiros_ranges.append(tr)

    return sum(verdadeiros_ranges[-periodo:]) / periodo


def criar_niveis_sinal(
    candles,
    direcao,
    periodo_atr=14,
    multiplicador_stop=1.5,
    risco_retorno=1.5,
):
    """
    Calcula entrada, stop e alvo a partir do último fechamento recebido.
    Pressupõe que o chamador forneceu apenas candles fechados.
    Não executa operações.
    """
    if direcao not in ("COMPRA", "VENDA"):
        raise ValueError("Direção deve ser COMPRA ou VENDA.")

    if (
        not isfinite(multiplicador_stop)
        or multiplicador_stop <= 0
        or not isfinite(risco_retorno)
        or risco_retorno <= 0
    ):
        raise ValueError("Multiplicadores devem ser positivos e finitos.")

    if not isinstance(candles, list) or not candles:
        raise ValueError("Lista de candles vazia ou inválida.")

    try:
        entrada = float(candles[-1]["close"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Fechamento do último candle inválido.")

    if not isfinite(entrada) or entrada <= 0:
        raise ValueError("Preço de entrada inválido.")

    atr = calcular_atr(candles, periodo=periodo_atr)
    distancia_stop = atr * multiplicador_stop
    distancia_alvo = distancia_stop * risco_retorno

    if direcao == "COMPRA":
        stop = entrada - distancia_stop
        alvo = entrada + distancia_alvo
    else:
        stop = entrada + distancia_stop
        alvo = entrada - distancia_alvo

    if stop <= 0 or alvo <= 0:
        raise ValueError("Stop ou alvo calculado fora de uma faixa válida.")

    return {
        "direcao": direcao,
        "entrada_referencia": round(entrada, 3),
        "atr": round(atr, 5),
        "stop": round(stop, 3),
        "alvo": round(alvo, 3),
        "multiplicador_stop_atr": multiplicador_stop,
        "risco_retorno": f"1:{risco_retorno}",
        "metodo_entrada": "FECHAMENTO_DO_ULTIMO_CANDLE",
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo de gestao de sinais carregado.")
    print("Calcula ATR, stop e alvo; nao envia nem executa ordens.")
