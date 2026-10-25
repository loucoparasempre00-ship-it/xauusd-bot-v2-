from math import isfinite

from leitura_mercado import ler_mercado
from motor_matematico import (
    calcular_ema,
    calcular_macd,
    detectar_consolidacao,
)


def integrar_leitura(candles, tolerancia_zona=1.0):
    """
    Combina estrutura de mercado e indicadores matemáticos.
    Não envia sinais nem executa ordens.
    Recebe candles em ordem cronológica, do mais antigo ao mais recente.
    """
    if not isinstance(candles, list) or len(candles) < 35:
        raise ValueError("São necessários pelo menos 35 candles.")

    fechamentos = []

    for i, candle in enumerate(candles):
        try:
            fechamento = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"Fechamento inválido no candle {i}."
            )

        if not isfinite(fechamento) or fechamento <= 0:
            raise ValueError(
                f"Fechamento inválido no candle {i}."
            )

        fechamentos.append(fechamento)

    # Calcula as duas EMAs sobre o mesmo histórico completo.
    ema6 = calcular_ema(fechamentos, 6)
    ema20 = calcular_ema(fechamentos, 20)

    # Indicadores e estrutura de mercado.
    estrutura = ler_mercado(
        candles,
        lado_pivo=2,
        tolerancia_zona=tolerancia_zona,
    )

    macd = calcular_macd(fechamentos)
    consolidacao = detectar_consolidacao(candles)

    # Compara as EMAs calculadas na mesma série.
    if ema6 > ema20:
        alinhamento_emas = "ALTA"
    elif ema6 < ema20:
        alinhamento_emas = "BAIXA"
    else:
        alinhamento_emas = "NEUTRO"

    return {
        "timeframe": "M5",
        "preco": fechamentos[-1],
        "estrutura": estrutura,
        "indicadores": {
            "ema6": ema6,
            "ema20": ema20,
            "alinhamento_emas": alinhamento_emas,
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
    print("Sem envio de Telegram ou execucao de ordens.")
