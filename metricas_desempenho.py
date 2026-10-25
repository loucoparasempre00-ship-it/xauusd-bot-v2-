"""
Métricas de desempenho da V2.

Calcula estatísticas a partir de resultados já apurados.
Não gera sinais, não envia mensagens e não executa operações.
"""


def calcular_metricas(resultados):
    """
    Recebe uma lista de dicionários com o campo 'resultado'.

    Valores aceitos:
    - "WIN": alvo atingido.
    - "LOSS": stop atingido.
    - "BE": resultado no ponto de equilíbrio.

    Exemplo:
    [
        {"resultado": "WIN"},
        {"resultado": "LOSS"},
        {"resultado": "BE"},
    ]
    """
    if not isinstance(resultados, list):
        raise ValueError("Os resultados precisam estar em uma lista.")

    contagem = {"WIN": 0, "LOSS": 0, "BE": 0}

    for item in resultados:
        if not isinstance(item, dict):
            raise ValueError("Cada resultado precisa ser um dicionário.")

        resultado = item.get("resultado")

        if not isinstance(resultado, str):
            raise ValueError("Resultado ausente ou inválido.")

        resultado = resultado.upper()

        if resultado not in contagem:
            raise ValueError(
                f"Resultado desconhecido: {resultado!r}."
            )

        contagem[resultado] += 1

    total = sum(contagem.values())
    decididos = contagem["WIN"] + contagem["LOSS"]

    taxa_acerto = (
        contagem["WIN"] / decididos * 100
        if decididos
        else None
    )

    expectativa_r = (
        (contagem["WIN"] * 1.5 - contagem["LOSS"]) / total
        if total
        else None
    )

    return {
        "total": total,
        "wins": contagem["WIN"],
        "losses": contagem["LOSS"],
        "breakeven": contagem["BE"],
        "taxa_acerto_percentual": (
            round(taxa_acerto, 2)
            if taxa_acerto is not None
            else None
        ),
        "expectativa_r_bruta": (
            round(expectativa_r, 4)
            if expectativa_r is not None
            else None
        ),
        "observacao": (
            "Estimativa teórica usando alvo de 1,5R e stop de 1R. "
            "Não desconta spread, slippage, comissões ou sinais "
            "incompletos."
        ),
    }


if __name__ == "__main__":
    print("Módulo de métricas carregado.")
    print("Aguardando resultados apurados.")
    print("Nenhum backtest foi executado.")
