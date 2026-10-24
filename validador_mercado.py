from datetime import datetime, timezone


def validar_candles(candles, intervalo_minutos=5, idade_maxima_minutos=12):
    erros = []

    if not isinstance(candles, list) or len(candles) < 2:
        return {
            "valido": False,
            "status": "DADOS_INSUFICIENTES",
            "erros": ["São necessários pelo menos 2 candles."]
        }

    dados = []

    for i, candle in enumerate(candles):
        try:
            timestamp_texto = candle["timestamp"]
            timestamp = datetime.fromisoformat(
                timestamp_texto.replace("Z", "+00:00")
            )

            if timestamp.tzinfo is None:
                raise ValueError("Timestamp sem fuso horário.")

            close = float(candle["close"])

            if close <= 0:
                erros.append(f"Candle {i}: preço inválido.")
                continue

            dados.append((timestamp, close))

        except (KeyError, TypeError, ValueError, AttributeError):
            erros.append(f"Candle {i}: dados inválidos.")

    if erros:
        return {
            "valido": False,
            "status": "DADOS_INVALIDOS",
            "erros": erros
        }

    for i in range(1, len(dados)):
        if dados[i][0] <= dados[i - 1][0]:
            erros.append("Horários duplicados ou fora de ordem.")
            break

    agora = datetime.now(timezone.utc)
    ultimo = dados[-1][0].astimezone(timezone.utc)

    if ultimo > agora:
        erros.append("O último candle está no futuro.")
    else:
        idade = (agora - ultimo).total_seconds() / 60
        if idade > idade_maxima_minutos:
            erros.append(f"Dados desatualizados: {idade:.1f} minutos.")

    intervalo = (
        dados[-1][0] - dados[-2][0]
    ).total_seconds() / 60

    if intervalo > intervalo_minutos * 2:
        erros.append(
            f"Intervalo excessivo entre candles: {intervalo:.1f} minutos."
        )

    return {
        "valido": len(erros) == 0,
        "status": "DADOS_VALIDOS" if not erros else "DADOS_INVALIDOS",
        "erros": erros,
        "ultimo_preco": dados[-1][1],
        "ultimo_timestamp": dados[-1][0].isoformat()
    }


if __name__ == "__main__":
    print("Validador de mercado V2 carregado.")
    print("Nenhuma API ou ordem será executada.")
