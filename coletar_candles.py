import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


API_URL = "https://api.twelvedata.com/time_series"
MAX_CANDLES = 5000
CSV_PATH = Path("data/xauusd_m5.csv")


def baixar_candles():
    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        raise RuntimeError("Secret TWELVE_DATA_API_KEY não encontrado.")

    parametros = {
        "symbol": "XAU/USD",
        "interval": "5min",
        "outputsize": MAX_CANDLES,
        "format": "JSON",
        "timezone": "UTC",
        "apikey": api_key,
    }

    url = API_URL + "?" + urlencode(parametros)
    requisicao = Request(
        url,
        headers={"User-Agent": "XAUUSD-Backtest-Collector/1.0"},
    )

    try:
        with urlopen(requisicao, timeout=30) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        raise RuntimeError(
            f"Erro HTTP {erro.code} ao consultar a Twelve Data."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError(
            "Falha de conexão com a Twelve Data."
        ) from None
    except json.JSONDecodeError:
        raise RuntimeError("Resposta JSON inválida.") from None

    if "status" in dados and dados["status"] == "error":
        mensagem = str(dados.get("message", "erro não especificado"))
        raise RuntimeError(f"Twelve Data recusou a consulta: {mensagem}")

    valores = dados.get("values")

    if not isinstance(valores, list) or not valores:
        raise RuntimeError("A resposta não contém candles.")

    candles = []
    vistos = set()

    for item in valores:
        try:
            instante = datetime.strptime(
                item["datetime"], "%Y-%m-%d %H:%M:%S"
            )
            timestamp = instante.strftime("%Y-%m-%d %H:%M:%S")

            abertura = float(item["open"])
            maxima = float(item["high"])
            minima = float(item["low"])
            fechamento = float(item["close"])

            precos = [abertura, maxima, minima, fechamento]

            if not all(
                valor > 0 and valor < float("inf")
                for valor in precos
            ):
                raise ValueError("Preço inválido.")

            if maxima < max(abertura, fechamento, minima):
                raise ValueError("Máxima inconsistente.")

            if minima > min(abertura, fechamento, maxima):
                raise ValueError("Mínima inconsistente.")

            if timestamp in vistos:
                raise ValueError("Candle duplicado.")

            vistos.add(timestamp)

            volume = item.get("volume", "")
            if volume not in ("", None):
                volume_num = float(volume)
                if volume_num < 0 or volume_num == float("inf"):
                    raise ValueError("Volume inválido.")
                volume = volume_num
            else:
                volume = ""

            candles.append({
                "timestamp_utc": timestamp,
                "open": abertura,
                "high": maxima,
                "low": minima,
                "close": fechamento,
                "volume": volume,
            })

        except (KeyError, TypeError, ValueError) as erro:
            raise RuntimeError(
                f"Candle inválido; coleta cancelada: {erro}"
            ) from None

    candles.sort(key=lambda candle: candle["timestamp_utc"])

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as arquivo:
        campos = [
            "timestamp_utc", "open", "high",
            "low", "close", "volume"
        ]
        escritor = csv.DictWriter(arquivo, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(candles)

    print(f"Validação concluída: {len(candles)} candles.")
    print(f"Primeiro candle UTC: {candles[0]['timestamp_utc']}")
    print(f"Último candle UTC: {candles[-1]['timestamp_utc']}")
    print(f"CSV gerado: {CSV_PATH}")


if __name__ == "__main__":
    try:
        baixar_candles()
    except RuntimeError as erro:
        print(f"FALHA: {erro}")
        sys.exit(1)
