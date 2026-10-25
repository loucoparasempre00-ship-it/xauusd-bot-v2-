"""
Relatório consolidado da V2.

Reúne métricas e resultados já apurados.
Não gera sinais nem executa operações.
"""

from datetime import datetime, timezone


def gerar_relatorio(metricas, observacoes=None):
    """Gera um relatório textual a partir de métricas existentes."""
    if not isinstance(metricas, dict):
        raise ValueError("As métricas precisam ser um dicionário.")

    if observacoes is None:
        observacoes = []

    if not isinstance(observacoes, list):
        raise ValueError("As observações precisam estar em uma lista.")

    linhas = [
        "RELATÓRIO XAUUSD BOT V2",
        "=" * 32,
        "Gerado em: "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "",
        "MÉTRICAS",
        "-" * 32,
    ]

    campos = [
        ("total", "Total de resultados"),
        ("wins", "Vitórias"),
        ("losses", "Derrotas"),
        ("breakeven", "Ponto de equilíbrio"),
        ("taxa_acerto_percentual", "Taxa de acerto (%)"),
        ("expectativa_r_bruta", "Expectativa teórica (R)"),
    ]

    for chave, rotulo in campos:
        valor = metricas.get(chave, "N/D")
        if valor is None:
            valor = "N/D"
        linhas.append(f"{rotulo}: {valor}")

    linhas.extend(["", "OBSERVAÇÕES", "-" * 32])

    if observacoes:
        for observacao in observacoes:
            linhas.append(f"- {observacao}")
    else:
        linhas.append("- Nenhuma observação informada.")

    linhas.extend([
        "",
        "NOTA:",
        "Resultados históricos não garantem desempenho futuro.",
        "Custos, spread e slippage precisam ser considerados.",
    ])

    return "\n".join(linhas)


if __name__ == "__main__":
    print("Módulo de relatório carregado.")
    print("Nenhum relatório de desempenho foi gerado.")
