def decidir(candidato, validacao, macro, leitura):
    """
    Consolida as avaliações técnica, estrutural e macro.
    Não envia sinais nem executa operações.
    """

    if not all(
        isinstance(item, dict)
        for item in (candidato, validacao, macro, leitura)
    ):
        raise ValueError(
            "Candidato, validação, macro e leitura devem ser dicionários."
        )

    direcao = candidato.get("direcao")

    if direcao not in ("COMPRA", "VENDA"):
        return {
            "decisao": "REJEITAR",
            "motivos": ["Direção do candidato inválida."],
            "envio_telegram": False,
            "execucao_ordens": False,
        }

    motivos = []

    if validacao.get("aprovado") is not True:
        motivos.append("O validador não aprovou o candidato.")

    if leitura.get("preco") is None:
        motivos.append("Leitura de mercado sem preço de referência.")

    if macro.get("status") == "SEM_DADOS_MACRO_VALIDOS":
        contexto_macro = "INDISPONIVEL"
    else:
        contexto_macro = macro.get("direcao_macro", "INDEFINIDA")

    if motivos:
        decisao = "REJEITAR"
    else:
        decisao = "AGUARDAR_INTEGRACAO"

    return {
        "decisao": decisao,
        "direcao_candidata": direcao,
        "motivos": motivos,
        "contexto_macro": contexto_macro,
        "macro_decide_sozinho": False,
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo de decisao final carregado.")
    print("Aguardando avaliações dos módulos anteriores.")
    print("Sem envio ao Telegram ou execução de ordens.")
