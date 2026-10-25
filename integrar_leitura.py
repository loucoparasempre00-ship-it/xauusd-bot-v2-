
from math import isfinite

from leitura_mercado import ler_mercado
from motor_matematico import (
    calcular_ema,
    calcular_macd,
    detectar_consolidacao,
)


def integrar_leitura(candles, tolerancia_zona=1.0):
    """
    Reúne estrutura de mercado e indicadores matemáticos.
    Não envia sinais nem executa ordens.
    """
    if not isinstance(candles, list) or len(candles) < 35:
        raise ValueError("São necessários pelo menos 35 candles.")

    fechamentos = []
    for i, candle in enumerate(candles):
        try:
            fechamento = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Fechamento inválido no candle {i}.")

        if not isfinite(fechamento) or fechamento <= 0:
            raise ValueError(f"Fechamento inválido no candle {i}.")

        fechamentos.append(fechamento)

    estrutura = ler_mercado(
        candles,
        lado_pivo=2,
        tolerancia_zona=tolerancia_zona,
    )

    ema6 = calcular_ema(fechamentos[-6:], 6)
    ema20 = calcular_ema(fechamentos[-20:], 20)
    macd = calcular_macd(fechamentos)

    consolidacao = detectar_consolidacao(candles)

    return {
        "timeframe": "M5",
        "preco": fechamentos[-1],
        "estrutura": estrutura,
        "indicadores": {
            "ema6": ema6,
            "ema20": ema20,
            "macd_linha": macd["linha"],
            "macd_sinal": macd["sinal"],
            "macd_histograma": macd["histograma"],
            "consolidacao": consolidacao,
            "vwap": None,
            "vwap_status": "INDISPONIVEL_SEM_VOLUME_VALIDO",
        },
        "candidato_sinal": None,
        "decisao_final": "PENDENTE_DE_VALIDACAO",
        "envio_telegram": False,
    }


if __name__ == "__main__":
    print("Modulo de integracao carregado.")
    print("Aguardando candles para produzir a leitura.")
    print("Sem envio de Telegram ou execução de ordens.")
