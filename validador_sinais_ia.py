from math import isfinite


def validar_sinal(candidato, leitura):
    """
    Faz uma primeira triagem de um sinal candidato.
    Não envia mensagens nem executa operações.
    A validação por IA externa será conectada posteriormente.
    """

    if not isinstance(candidato, dict):
        raise ValueError("O candidato deve ser um dicionário.")

    if not isinstance(leitura, dict):
        raise ValueError("A leitura de mercado deve ser um dicionário.")

    direcao = candidato.get("direcao")

    if direcao not in ("COMPRA", "VENDA"):
        return {
            "aprovado": False,
            "motivos": ["Direção ausente ou inválida."],
            "validador": "REGRAS_INICIAIS",
        }

    try:
        preco = float(leitura["preco"])
    except (KeyError, TypeError, ValueError):
        return {
            "aprovado": False,
            "motivos": ["Preço da leitura ausente ou inválido."],
            "validador": "REGRAS_INICIAIS",
        }

    if not isfinite(preco) or preco <= 0:
        return {
            "aprovado": False,
            "motivos": ["Preço inválido."],
            "validador": "REGRAS_INICIAIS",
        }

    estrutura = leitura.get("estrutura", {})
    indicadores = leitura.get("indicadores", {})

    if not isinstance(estrutura, dict):
        estrutura = {}

    if not isinstance(indicadores, dict):
        indicadores = {}

    motivos = []

    tendencia = estrutura.get("tendencia", "INDEFINIDA")
    alinhamento = indicadores.get(
        "alinhamento_emas", "INDEFINIDO"
    )

    if tendencia == "INDEFINIDA":
        motivos.append("Estrutura de tendência indefinida.")

    if direcao == "COMPRA":
        if tendencia == "BAIXA":
            motivos.append(
                "Compra contrária à tendência estrutural."
            )

        if alinhamento == "BAIXA":
            motivos.append(
                "EMAs alinhadas para baixo."
            )

    if direcao == "VENDA":
        if tendencia == "ALTA":
            motivos.append(
                "Venda contrária à tendência estrutural."
            )

        if alinhamento == "ALTA":
            motivos.append(
                "EMAs alinhadas para cima."
            )

    if indicadores.get("consolidacao") is True:
        motivos.append(
            "Mercado em consolidação; sinal requer análise adicional."
        )

    # Por segurança, esta primeira versão só aprova candidatos
    # sem conflitos detectados pelas regras acima.
    aprovado = len(motivos) == 0

    return {
        "aprovado": aprovado,
        "motivos": motivos,
        "direcao": direcao,
        "preco_referencia": preco,
        "validador": "REGRAS_INICIAIS",
        "validacao_ia_externa": False,
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo de validacao de sinais carregado.")
    print("Validacao inicial por regras; IA externa ainda nao conectada.")
    print("Sem envio ao Telegram ou execucao de ordens.")
