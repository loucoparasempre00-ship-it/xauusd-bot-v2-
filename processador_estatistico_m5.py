
import csv
import json
import os
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

ARQUIVO_TABULEIRO = "data/tabuleiro_m5.csv"
ARQUIVO_BORDAS = "data/eventos_bordas_m5.csv"

SAIDA_REGIOES = "data/estatisticas_regioes_m5.csv"
SAIDA_TRANSICOES = "data/transicoes_probabilidade_m5.csv"
SAIDA_RESUMO = "data/resumo_estatistico_m5.json"

MIN_AMOSTRAS = 20
Z = 1.96


def ler_csv(caminho):
    if not os.path.exists(caminho):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    with open(
        caminho, "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        return list(csv.DictReader(arquivo))


def wilson(informacoes, sucessos):
    """
    Limite inferior do intervalo de confiança de Wilson.
    Não representa garantia de resultado futuro.
    """
    n = informacoes

    if n == 0:
        return 0.0

    p = sucessos / n
    z2 = Z ** 2

    centro = p + z2 / (2 * n)

    margem = Z * math.sqrt(
        (p * (1 - p) / n) + z2 / (4 * n ** 2)
    )

    denominador = 1 + z2 / n

    return max(
        0.0,
        (centro - margem) / denominador
    )


def carregar_tabuleiro():
    registros = ler_csv(ARQUIVO_TABULEIRO)

    velas = []

    for linha in registros:
        try:
            velas.append({
                "timestamp": linha["timestamp"],
                "eixo_x": int(linha["eixo_x"]),
                "eixo_y": int(linha["eixo_y"]),
                "open": float(linha["open"]),
                "high": float(linha["high"]),
                "low": float(linha["low"]),
                "close": float(linha["close"]),
                "atr": float(linha["atr"]),
                "direcao": linha["direcao"]
            })
        except (ValueError, KeyError, TypeError):
            continue

    velas.sort(key=lambda x: x["timestamp"])

    return velas


def carregar_bordas():
    registros = ler_csv(ARQUIVO_BORDAS)

    eventos = []

    for linha in registros:
        try:
            eventos.append({
                "timestamp": linha["timestamp"],
                "evento": linha["evento"],
                "nivel_borda": float(linha["nivel_borda"])
            })
        except (ValueError, KeyError, TypeError):
            continue

    return eventos


def calcular_regioes(velas, eventos):
    regioes = defaultdict(lambda: {
        "visitas": 0,
        "toques": 0,
        "rejeicoes": 0,
        "rompimentos": 0,
        "rejeicoes_superiores": 0,
        "rejeicoes_inferiores": 0,
        "rompimentos_alta": 0,
        "rompimentos_baixa": 0
    })

    eventos_por_timestamp = defaultdict(list)

    for evento in eventos:
        eventos_por_timestamp[evento["timestamp"]].append(
            evento["evento"]
        )

    for vela in velas:
        regiao = vela["eixo_y"]
        dados = regioes[regiao]

        dados["visitas"] += 1

        eventos_vela = eventos_por_timestamp.get(
            vela["timestamp"], []
        )

        for evento in eventos_vela:
            if evento.startswith("TOQUE_"):
                dados["toques"] += 1

            elif evento.startswith("REJEICAO_"):
                dados["rejeicoes"] += 1

                if evento == "REJEICAO_SUPERIOR":
                    dados["rejeicoes_superiores"] += 1

                elif evento == "REJEICAO_INFERIOR":
                    dados["rejeicoes_inferiores"] += 1

            elif evento.startswith("ROMPIMENTO_"):
                dados["rompimentos"] += 1

                if evento == "ROMPIMENTO_ALTA":
                    dados["rompimentos_alta"] += 1

                elif evento == "ROMPIMENTO_BAIXA":
                    dados["rompimentos_baixa"] += 1

    resultados = []

    for regiao, dados in sorted(regioes.items()):
        toques = dados["toques"]
        rejeicoes = dados["rejeicoes"]
        rompimentos = dados["rompimentos"]

        testes = toques + rejeicoes + rompimentos

        prob_rejeicao = (
            rejeicoes / testes if testes else 0
        )

        prob_rompimento = (
            rompimentos / testes if testes else 0
        )

        resultados.append({
            "regiao": regiao,
            "visitas": dados["visitas"],
            "toques": toques,
            "rejeicoes": rejeicoes,
            "rompimentos": rompimentos,
            "prob_rejeicao": round(prob_rejeicao, 4),
            "prob_rompimento": round(prob_rompimento, 4),
            "limite_inferior_rejeicao": round(
                wilson(testes, rejeicoes), 4
            ),
            "amostras": testes,
            "amostra_suficiente": (
                "SIM" if testes >= MIN_AMOSTRAS else "NAO"
            ),
            "rejeicoes_superiores": dados[
                "rejeicoes_superiores"
            ],
            "rejeicoes_inferiores": dados[
                "rejeicoes_inferiores"
            ],
            "rompimentos_alta": dados[
                "rompimentos_alta"
            ],
            "rompimentos_baixa": dados[
                "rompimentos_baixa"
            ]
        })

    return resultados


def calcular_transicoes(velas):
    contagem = Counter()
    saidas = Counter()
    visitas = Counter()

    sequencia = []

    for vela in velas:
        regiao = vela["eixo_y"]
        visitas[regiao] += 1

        if not sequencia or sequencia[-1] != regiao:
            sequencia.append(regiao)

    for origem, destino in zip(
        sequencia[:-1], sequencia[1:]
    ):
        contagem[(origem, destino)] += 1
        saidas[origem] += 1

    resultados = []

    for (origem, destino), quantidade in sorted(
        contagem.items()
    ):
        total = saidas[origem]

        resultados.append({
            "origem": origem,
            "destino": destino,
            "transicoes": quantidade,
            "saidas_origem": total,
            "probabilidade": round(
                quantidade / total, 4
            ),
            "visitas_origem": visitas[origem]
        })

    return resultados


def salvar_csv(caminho, registros):
    os.makedirs("data", exist_ok=True)

    if not registros:
        print(f"Sem registros para: {caminho}")
        return

    with open(
        caminho, "w", newline="", encoding="utf-8"
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=list(registros[0].keys())
        )

        escritor.writeheader()
        escritor.writerows(registros)


def gerar_resumo(velas, regioes, transicoes):
    total_visitas = sum(
        r["visitas"] for r in regioes
    )

    regioes_validas = [
        r for r in regioes
        if r["amostra_suficiente"] == "SIM"
    ]

    melhores_rejeicoes = sorted(
        regioes_validas,
        key=lambda r: r["prob_rejeicao"],
        reverse=True
    )[:10]

    melhores_transicoes = sorted(
        transicoes,
        key=lambda t: (
            t["probabilidade"],
            t["transicoes"]
        ),
        reverse=True
    )[:15]

    return {
        "atualizado_em": datetime.now(
            timezone.utc
        ).isoformat(),
        "versao": 1,
        "total_velas": len(velas),
        "total_regioes": len(regioes),
        "total_visitas": total_visitas,
        "total_transicoes": sum(
            t["transicoes"] for t in transicoes
        ),
        "minimo_amostras": MIN_AMOSTRAS,
        "metodo_confianca": "Wilson 95%",
        "melhores_regioes_rejeicao": melhores_rejeicoes,
        "transicoes_mais_frequentes": melhores_transicoes
    }


def processar():
    print("Carregando histórico do tabuleiro...")

    velas = carregar_tabuleiro()
    eventos = carregar_bordas()

    if not velas:
        raise ValueError("Nenhuma vela válida encontrada.")

    print(f"Velas: {len(velas)}")
    print(f"Eventos de borda: {len(eventos)}")

    regioes = calcular_regioes(velas, eventos)
    transicoes = calcular_transicoes(velas)

    salvar_csv(SAIDA_REGIOES, regioes)
    salvar_csv(SAIDA_TRANSICOES, transicoes)

    resumo = gerar_resumo(
        velas, regioes, transicoes
    )

    with open(
        SAIDA_RESUMO, "w", encoding="utf-8"
    ) as arquivo:
        json.dump(
            resumo,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    print("\n======= PROCESSAMENTO ESTATÍSTICO =======")
    print(f"Velas analisadas: {len(velas)}")
    print(f"Regiões: {len(regioes)}")
    print(f"Transições: {len(transicoes)}")
    print(
        "Regiões com amostra suficiente:",
        len(regioes_validas := [
            r for r in regioes
            if r["amostra_suficiente"] == "SIM"
        ])
    )
    print(f"Arquivo: {SAIDA_REGIOES}")
    print(f"Arquivo: {SAIDA_TRANSICOES}")
    print(f"Arquivo: {SAIDA_RESUMO}")
    print("=========================================")


if __name__ == "__main__":
    processar()
