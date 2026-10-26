
import csv
import os
from datetime import datetime, timezone, timedelta

ARQUIVO = "data/xauusd_m5_fechados.csv"
IDADE_MAXIMA_MINUTOS = 12


def validar_candles(
    candles,
    intervalo_minutos=5,
    idade_maxima_minutos=12,
    agora=None
):
    if agora is None:
        agora = datetime.now(timezone.utc)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)
    else:
        agora = agora.astimezone(timezone.utc)

    motivos = []
    tempos = []

    if not candles:
        return {
            "valido": False,
            "status": "DADOS_INVALIDOS",
            "quantidade": 0,
            "ultimo_candle_utc": None,
            "motivos": ["Lista de candles vazia."]
        }

    for numero, candle in enumerate(candles, start=1):
        timestamp = candle.get("timestamp") or candle.get("timestamp_utc")

        try:
            instante = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            )
            if instante.tzinfo is None:
                instante = instante.replace(tzinfo=timezone.utc)
            instante = instante.astimezone(timezone.utc)
            tempos.append(instante)
        except (ValueError, TypeError):
            motivos.append(f"Candle {numero}: horário inválido.")
            continue

        try:
            o = float(candle["open"])
            h = float(candle["high"])
            l = float(candle["low"])
            c = float(candle["close"])

            if min(o, h, l, c) <= 0:
                motivos.append(f"Candle {numero}: preço não positivo.")

            if h < max(o, c, l) or l > min(o, c, h):
                motivos.append(f"Candle {numero}: OHLC inconsistente.")

        except (KeyError, ValueError, TypeError):
            motivos.append(f"Candle {numero}: OHLC ausente ou inválido.")

    if len(tempos) != len(candles):
        motivos.append("Há candles com horários inválidos.")
    else:
        if tempos != sorted(tempos):
            motivos.append("Candles fora de ordem cronológica.")

        if len(set(tempos)) != len(tempos):
            motivos.append("Há horários duplicados.")

    if tempos:
        ultimo = tempos[-1]
        idade = (agora - ultimo).total_seconds() / 60

        if idade < 0:
            motivos.append("O último candle tem horário no futuro.")
        elif idade > idade_maxima_minutos:
            motivos.append(
                f"Dados antigos: último candle há {idade:.1f} minutos."
            )

        if ultimo + timedelta(minutes=intervalo_minutos) > agora:
            motivos.append("O último candle ainda pode estar em formação.")

    valido = len(motivos) == 0

    return {
        "valido": valido,
        "status": "DADOS_VALIDOS" if valido else "DADOS_INVALIDOS",
        "quantidade": len(candles),
        "ultimo_candle_utc": (
            tempos[-1].isoformat() if tempos else None
        ),
        "motivos": motivos
    }


def executar():
    if not os.path.exists(ARQUIVO):
        raise FileNotFoundError(f"Arquivo não encontrado: {ARQUIVO}")

    with open(ARQUIVO, newline="", encoding="utf-8-sig") as arquivo:
        candles = list(csv.DictReader(arquivo))

    resultado = validar_candles(candles)

    print(f"Candles validados: {resultado['quantidade']}")
    print(f"Status: {resultado['status']}")
    print(f"Último candle UTC: {resultado['ultimo_candle_utc']}")

    if not resultado["valido"]:
        for motivo in resultado["motivos"]:
            print(f"ERRO: {motivo}")
        raise SystemExit("Validação reprovada; sinais bloqueados.")

    print("Validação de mercado aprovada.")


if __name__ == "__main__":
    executar()
