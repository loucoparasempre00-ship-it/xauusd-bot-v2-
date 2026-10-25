from datetime import datetime, timezone


def verificar_saude(candles, intervalo_minutos=5, agora=None):
    """
    Verifica a qualidade básica dos candles recebidos.
    Espera timestamp ISO 8601 com fuso horário.
    Não coleta dados nem envia sinais.
    """

    problemas = []

    if not isinstance(candles, list) or not candles:
        return {
            "saude": "RUIM",
            "problemas": ["Lista de candles vazia ou inválida."],
            "pode_avancar": False,
        }

    if not isinstance(intervalo_minutos, int) or intervalo_minutos <= 0:
        raise ValueError("Intervalo inválido.")

    if agora is None:
        agora = datetime.now(timezone.utc)

    if agora.tzinfo is None:
        raise ValueError("O horário atual precisa ter fuso horário.")

    timestamps = []
    precos_validos = True

    for i, candle in enumerate(candles):
        if not isinstance(candle, dict):
            problemas.append(f"Candle {i} não é um dicionário.")
            precos_validos = False
            continue

        try:
            abertura = float(candle["open"])
            maxima = float(candle["high"])
            minima = float(candle["low"])
            fechamento = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            problemas.append(f"Candle {i} possui OHLC inválido.")
            precos_validos = False
            continue

        valores = (abertura, maxima, minima, fechamento)

        if any(
            not (v > 0 and v < float("inf"))
            for v in valores
        ):
            problemas.append(f"Candle {i} possui preço inválido.")
            precos_validos = False
            continue

        if maxima < max(abertura, fechamento, minima):
            problemas.append(f"Candle {i} possui máxima inconsistente.")
            precos_validos = False

        if minima > min(abertura, fechamento):
            problemas.append(f"Candle {i} possui mínima inconsistente.")
            precos_validos = False

        timestamp = candle.get("timestamp")

        if not isinstance(timestamp, str):
            problemas.append(f"Candle {i} sem timestamp.")
            continue

        try:
            data = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError:
            problemas.append(f"Candle {i} com timestamp inválido.")
            continue

        if data.tzinfo is None:
            problemas.append(f"Candle {i} sem fuso horário.")
            continue

        timestamps.append(data.astimezone(timezone.utc))

    if len(timestamps) == len(candles):
        for i in range(1, len(timestamps)):
            if timestamps[i] <= timestamps[i - 1]:
                problemas.append(
                    "Candles duplicados ou fora de ordem."
                )
                break

        agora_utc = agora.astimezone(timezone.utc)

        if timestamps[-1] > agora_utc:
            problemas.append("O último candle está no futuro.")
        elif (
            agora_utc - timestamps[-1]
        ).total_seconds() > intervalo_minutos * 120:
            problemas.append("O último candle está muito antigo.")

    if not precos_validos:
        problemas.append("Há problemas nos preços OHLC.")

    return {
        "saude": "BOA" if not problemas else "ATENCAO",
        "problemas": list(dict.fromkeys(problemas)),
        "quantidade_candles": len(candles),
        "pode_avancar": not problemas,
    }


if __name__ == "__main__":
    print("Monitor de saude carregado.")
    print("Aguardando dados de mercado.")
    print("Nenhum sinal será enviado.")
