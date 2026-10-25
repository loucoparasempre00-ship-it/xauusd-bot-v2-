from math import isfinite


def avaliar_sinal(sinal, candles):
    """
    Avalia um sinal contra candles posteriores à entrada.
    Cada candle precisa conter high, low e timestamp.
    Não envia mensagens nem executa operações.
    """

    if not isinstance(sinal, dict):
        raise ValueError("Sinal inválido.")

    if not isinstance(candles, list):
        raise ValueError("Candles devem ser uma lista.")

    direcao = sinal.get("direcao")

    if direcao not in ("COMPRA", "VENDA"):
        raise ValueError("Direção deve ser COMPRA ou VENDA.")

    try:
        entrada = float(sinal["entrada_referencia"])
        stop = float(sinal["stop"])
        alvo = float(sinal["alvo"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Entrada, stop ou alvo inválido.")

    if not all(
        isfinite(v) and v > 0 for v in (entrada, stop, alvo)
    ):
        raise ValueError("Os níveis de preço devem ser positivos e finitos.")

    if direcao == "COMPRA" and not stop < entrada < alvo:
        raise ValueError("Níveis inválidos para uma COMPRA.")

    if direcao == "VENDA" and not alvo < entrada < stop:
        raise ValueError("Níveis inválidos para uma VENDA.")

    for i, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise ValueError(f"Candle {i} inválido.")

        try:
            maxima = float(candle["high"])
            minima = float(candle["low"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Máxima ou mínima inválida no candle {i}.")

        if (
            not isfinite(maxima)
            or not isfinite(minima)
            or maxima <= 0
            or minima <= 0
            or maxima < minima
        ):
            raise ValueError(f"OHLC inválido no candle {i}.")

    for candle in candles:
        maxima = float(candle["high"])
        minima = float(candle["low"])

        if direcao == "COMPRA":
            atingiu_stop = minima <= stop
            atingiu_alvo = maxima >= alvo
        else:
            atingiu_stop = maxima >= stop
            atingiu_alvo = minima <= alvo

        timestamp = candle.get("timestamp")

        if atingiu_stop and atingiu_alvo:
            return {
                "resultado": "INDETERMINADO",
                "motivo": "Stop e alvo tocados no mesmo candle.",
                "timestamp": timestamp,
            }

        if atingiu_stop:
            return {
                "resultado": "STOP",
                "motivo": "Stop atingido.",
                "timestamp": timestamp,
            }

        if atingiu_alvo:
            return {
                "resultado": "ALVO",
                "motivo": "Alvo atingido.",
                "timestamp": timestamp,
            }

    return {
        "resultado": "EM_ABERTO",
        "motivo": "Nenhum nível foi atingido nos candles analisados.",
        "timestamp": None,
    }


if __name__ == "__main__":
    print("Modulo de acompanhamento de resultados carregado.")
    print("Aguardando sinais e candles posteriores.")
    print("Sem envio ao Telegram ou execução de ordens.")
