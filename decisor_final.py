def decidir(candidato, validacao, macro, leitura):
    """
    Consolida as avaliações técnica, estrutural e macro.
    Não envia sinais nem executa operações.
    """

    itens = {
        "candidato": candidato,
        "validacao": validacao,
        "macro": macro,
        "leitura": leitura,
    }

    for nome, item in itens.items():
        if not isinstance(item, dict):
            raise ValueError(
                f"{nome.capitalize()} deve ser um dicionário."
            )

    direcao = candidato.get("direcao")
    motivos = []

    if direcao not in ("COMPRA", "VENDA"):
        motivos.append("Direção do candidato inválida.")

    if validacao.get("aprovado") is not True:
        motivos.append("O validador não aprovou o candidato.")

    preco = leitura.get("preco")
    if preco is None:
        preco = leitura.get("preco_fechamento")

    try:
        preco = float(preco)
        if preco <= 0:
            raise ValueError
    except (TypeError, ValueError):
        motivos.append("Preço de referência ausente ou inválido.")
        preco = None

    if macro.get("status") == "SEM_DADOS_MACRO_VALIDOS":
        contexto_macro = "INDISPONIVEL"
    else:
        contexto_macro = macro.get(
            "direcao_macro", "INDEFINIDA"
        )

    if motivos:
        decisao = "REJEITAR"
    else:
        # A integração dos módulos ainda precisa confirmar
        # stop, alvo, candles fechados e regras de risco.
        decisao = "AGUARDAR_INTEGRACAO"
        motivos.append(
            "Aguardando integração completa e validação de risco."
        )

    return {
        "decisao": decisao,
        "direcao_candidata": direcao,
        "preco_referencia": preco,
        "motivos": motivos,
        "contexto_macro": contexto_macro,
        "macro_decide_sozinho": False,
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo de decisao final carregado.")
    print("Funcao decidir() pronta para receber as avaliações.")
    print("Envio ao Telegram desativado neste modulo.")
    print("Execucao de ordens desativada.")
