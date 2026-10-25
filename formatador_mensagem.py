from math import isfinite


def formatar_sinal(sinal, resultado_decisao):
    """
    Formata a mensagem de um sinal previamente aprovado.
    Não envia mensagens nem executa operações.
    """

    if not isinstance(sinal, dict):
        raise ValueError("Sinal inválido.")

    if not isinstance(resultado_decisao, dict):
        raise ValueError("Resultado da decisão inválido.")

    if resultado_decisao.get("decisao") != "APROVADO":
        raise ValueError(
            "A mensagem só pode ser formatada após aprovação final."
        )

    direcao = sinal.get("direcao")

    if direcao not in ("COMPRA", "VENDA"):
        raise ValueError("Direção do sinal inválida.")

    try:
        entrada = float(sinal["entrada_referencia"])
        stop = float(sinal["stop"])
        alvo = float(sinal["alvo"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Entrada, stop ou alvo inválido.")

    if not all(
        isfinite(v) and v > 0 for v in (entrada, stop, alvo)
    ):
        raise ValueError("Os preços devem ser positivos e finitos.")

    if direcao == "COMPRA" and not stop < entrada < alvo:
        raise ValueError("Níveis inválidos para COMPRA.")

    if direcao == "VENDA" and not alvo < entrada < stop:
        raise ValueError("Níveis inválidos para VENDA.")

    risco = abs(entrada - stop)
    retorno = abs(alvo - entrada)
    relacao = retorno / risco

    identificador = str(sinal.get("id", "NAO_INFORMADO"))
    horario = str(sinal.get("timestamp", "NAO_INFORMADO"))

    mensagem = (
        "XAUUSD | M5\n"
        f"SINAL: {direcao}\n"
        f"Referência: {entrada:.3f}\n"
        f"Stop: {stop:.3f}\n"
        f"Alvo: {alvo:.3f}\n"
        f"Risco/retorno: 1:{relacao:.2f}\n"
        f"ID: {identificador}\n"
        f"Candle: {horario}\n\n"
        "Alerta informativo; não executa ordens."
    )

    return {
        "mensagem": mensagem,
        "id_sinal": identificador,
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo formatador de mensagens carregado.")
    print("Prepara texto; nao envia mensagens.")
