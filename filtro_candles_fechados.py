import csv
import os
from datetime import datetime, timezone, timedelta

ENTRADA = "data/xauusd_m5.csv"
SAIDA = "data/xauusd_m5_fechados.csv"
INTERVALO_MINUTOS = 5


def filtrar_candles_fechados(candles, intervalo_minutos=5, agora=None):
    """Mantém apenas candles cujo período de 5 minutos já terminou."""
    if agora is None:
        agora = datetime.now(timezone.utc)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)

    limite = agora.astimezone(timezone.utc)
    intervalo = timedelta(minutes=intervalo_minutos)
    fechados = []

    for candle in candles:
        timestamp = candle.get("timestamp")
        if not timestamp:
            timestamp = candle.get("timestamp_utc")
        if not timestamp:
            continue

        try:
            instante = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            )
            if instante.tzinfo is None:
                instante = instante.replace(tzinfo=timezone.utc)
            instante = instante.astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue

        if instante + intervalo <= limite:
            item = dict(candle)
            item["timestamp"] = instante.isoformat()
            fechados.append(item)

    fechados.sort(key=lambda item: item["timestamp"])
    return fechados


def executar():
    if not os.path.exists(ENTRADA):
        raise FileNotFoundError(f"Arquivo não encontrado: {ENTRADA}")

    with open(ENTRADA, "r", newline="", encoding="utf-8") as arquivo:
        leitor = csv.DictReader(arquivo)
        campos = leitor.fieldnames or []
        candles = list(leitor)

    if not candles:
        raise ValueError("O CSV de entrada não contém candles.")

    fechados = filtrar_candles_fechados(candles, INTERVALO_MINUTOS)

    if not fechados:
        raise ValueError("Nenhum candle fechado válido foi encontrado.")

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)

    with open(SAIDA, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
            extrasaction="ignore"
        )
        escritor.writeheader()

        for candle in fechados:
            escritor.writerow(candle)

    print(f"Candles recebidos: {len(candles)}")
    print(f"Candles fechados: {len(fechados)}")
    print(f"Arquivo gerado: {SAIDA}")


if __name__ == "__main__":
    executar()
