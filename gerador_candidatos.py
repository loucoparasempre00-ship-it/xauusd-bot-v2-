from math import isfinite


def gerar_candidato(leitura):
    """
    Gera um candidato preliminar a partir da leitura técnica.
    Não aprova sinais, não envia mensagens e não executa ordens.
    """

    if not isinstance(leitura, dict):
        raise ValueError("A leitura deve ser um dicionário.")

    indicadores = leitura.get("indicadores", {})
    estrutura = leitura.get("estrutura", {})

    if not isinstance(indicadores, dict):
        indicadores = {}

    if not isinstance(estrutura, dict):
        estrutura = {}

    try:
        preco = float(leitura["preco"])
        ema6 = float(indicadores["ema6"])
        ema20 = float(indicadores["ema20"])
        macd_histograma = float(indicadores["macd_histograma"])
    except (KeyError, TypeError, ValueError):
        return {
            "existe_candidato": False,
            "motivo": "Dados técnicos ausentes ou inválidos.",
        }

    valores = (preco, ema6, ema20, macd_histograma)

    if not all(isfinite(v) for v in valores) or preco <= 0:
        return {
            "existe_candidato": False,
            "motivo": "Dados técnicos não finitos ou preço inválido.",
        }

    tendencia = estrutura.get("tendencia", "INDEFINIDA")
    rompimentos = estrutura.get("rompimentos", [])

    if not isinstance(rompimentos, list):
        rompimentos = []

    compra = (
        ema6 > ema20
        and macd_histograma > 0
        and tendencia == "ALTA"
    )

    venda = (
        ema6 < ema20
        and macd_histograma < 0
        and tendencia == "BAIXA"
    )

    if compra:
        direcao = "COMPRA"
    elif venda:
        direcao = "VENDA"
    else:
        return {
            "existe_candidato": False,
            "motivo": "Indicadores e estrutura sem alinhamento suficiente.",
        }

    return {
        "existe_candidato": True,
        "direcao": direcao,
        "preco_referencia": preco,
        "timeframe": leitura.get("timeframe", "M5"),
        "tendencia": tendencia,
        "rompimentos": rompimentos,
        "macd_histograma": macd_histograma,
        "estado": "CANDIDATO_NAO_APROVADO",
        "decisao_final": "PENDENTE_DE_VALIDACAO",
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo gerador de candidatos carregado.")
    print("Nao aprova nem publica sinais.")
