from datetime import datetime, timezone


def filtrar_candles_fechados(candles, intervalo_minutos=5, agora=None):
    """
    Mantém apenas candles cujo intervalo já terminou.
    Espera timestamps ISO 8601 com fuso horário.
    Não altera a lista original.
    """

    if not isinstance(candles, list):
        raise ValueError("Candles devem ser fornecidos em uma lista.")

    if not isinstance(intervalo_minutos, int) or intervalo_minutos <= 0:
        raise ValueError("O intervalo deve ser um inteiro positivo.")

    if agora is None:
        agora = datetime.now(timezone.utc)

    if agora.tzinfo is None:
        raise ValueError("O horário atual precisa ter fuso horário.")

    resultado = []

    for i, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise ValueError(f"Candle {i} inválido.")

        timestamp = candle.get("timestamp")

        if not isinstance(timestamp, str):
            raise ValueError(
                f"Candle {i} sem timestamp ISO 8601 válido."
            )

        try:
            inicio = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError:
            raise ValueError(f"Timestamp inválido no candle {i}.")

        if inicio.tzinfo is None:
            raise ValueError(
                f"Timestamp do candle {i} sem informação de fuso."
            )

        fim = inicio.astimezone(timezone.utc).timestamp()
        fim += intervalo_minutos * 60

        if fim <= agora.astimezone(timezone.utc).timestamp():
            resultado.append(candle.copy())

    return resultado


if __name__ == "__main__":
    print("Filtro de candles fechados carregado.")
    print("Nenhum candle foi coletado ou alterado.")
