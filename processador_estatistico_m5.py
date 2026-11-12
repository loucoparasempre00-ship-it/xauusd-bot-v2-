
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PROCESSADOR ESTATISTICO M5 - TABULEIRO ESTAVEL

Responsabilidades:
- Ler a matriz XY do Tabuleiro.
- Ler os eventos identificados pelo detector de bordas.
- Calcular visitas e comportamento das regiões.
- Calcular transições entre regiões.
- Calcular probabilidades históricas.
- Gerar arquivos de entrega para o Coletor.

REGRAS:
- Eixo X = tempo cronológico M5.
- Eixo Y = regiões fixas de preço.
- Largura da região = US$ 1,50.
- ATR não altera a largura das regiões.
- Não cria banco paralelo.
- Não grava diretamente nos bancos oficiais.
"""

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ARQUIVO_TABULEIRO = DATA / "tabuleiro_m5.csv"
ARQUIVO_BORDAS = DATA / "eventos_bordas_m5.csv"

SAIDA_REGIOES = DATA / "estatisticas_regioes_m5.csv"
SAIDA_TRANSICOES = DATA / "transicoes_probabilidade_m5.csv"
SAIDA_RESUMO = DATA / "resumo_estatistico_m5.json"

MIN_AMOSTRAS = 20
Z = 1.96
LARGURA_REGIAO = 1.50


def agora():
    return datetime.now(timezone.utc).isoformat()


def numero(valor, campo):
    try:
        return float(
            str(valor).strip().replace(",", ".")
        )
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor inválido no campo {campo}: {valor}"
        )


def inteiro(valor, campo):
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor inválido no campo {campo}: {valor}"
        )


def ler_csv(caminho):
    if not caminho.is_file():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    with caminho.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:

        leitor = csv.DictReader(arquivo)

        if not leitor.fieldnames:
            raise ValueError(
                f"Arquivo sem cabeçalho: {caminho}"
            )

        return list(leitor)


def wilson(total, sucessos):
    """
    Limite inferior do intervalo de confiança de Wilson.
    Não representa garantia de resultado futuro.
    """

    if total <= 0:
        return 0.0

    p = sucessos / total
    z2 = Z ** 2

    centro = p + z2 / (2 * total)

    margem = Z * math.sqrt(
        (p * (1 - p) / total)
        + z2 / (4 * total ** 2)
    )

    denominador = 1 + z2 / total

    return max(
        0.0,
        (centro - margem) / denominador
    )


def carregar_tabuleiro():
    registros = ler_csv(ARQUIVO_TABULEIRO)

    velas_por_tempo = {}

    obrigatorias = [
        "timestamp",
        "eixo_x",
        "eixo_y",
        "open",
        "high",
        "low",
        "close",
        "atr",
        "direcao",
    ]

    if not registros:
        raise ValueError(
            "A matriz do Tabuleiro está vazia."
        )

    campos = list(registros[0].keys())

    ausentes = [
        campo for campo in obrigatorias
        if campo not in campos
    ]

    if ausentes:
        raise ValueError(
            f"Colunas ausentes no Tabuleiro: {ausentes}"
        )

    descartados = 0

    for linha in registros:
        try:
            timestamp = str(
                linha["timestamp"] or ""
            ).strip()

            if not timestamp:
                descartados += 1
                continue

            vela = {
                "timestamp": timestamp,
                "eixo_x": inteiro(
                    linha["eixo_x"], "eixo_x"
                ),
                "eixo_y": inteiro(
                    linha["eixo_y"], "eixo_y"
                ),
                "open": numero(
                    linha["open"], "open"
                ),
                "high": numero(
                    linha["high"], "high"
                ),
                "low": numero(
                    linha["low"], "low"
                ),
                "close": numero(
                    linha["close"], "close"
                ),
                "atr": numero(
                    linha["atr"], "atr"
                ),
                "direcao": str(
                    linha["direcao"] or "NEUTRA"
                ).strip().upper(),
            }

            if vela["high"] < vela["low"]:
                descartados += 1
                continue

            velas_por_tempo[timestamp] = vela

        except (ValueError, TypeError, KeyError):
            descartados += 1

    velas = sorted(
        velas_por_tempo.values(),
        key=lambda item: item["eixo_x"]
    )

    if not velas:
        raise ValueError(
            "Nenhuma vela válida encontrada."
        )

    for anterior, atual in zip(velas, velas[1:]):
        if atual["eixo_x"] <= anterior["eixo_x"]:
            raise ValueError(
                "Eixo X duplicado ou fora de ordem."
            )

    return velas, descartados


def carregar_bordas():
    if not ARQUIVO_BORDAS.is_file():
        raise FileNotFoundError(
            f"Eventos de borda não encontrados: {ARQUIVO_BORDAS}"
        )

    registros = ler_csv(ARQUIVO_BORDAS)

    eventos = []
    descartados = 0

    for linha in registros:
        try:
            timestamp = str(
                linha["timestamp"] or ""
            ).strip()

            tipo = str(
                linha["evento"] or ""
            ).strip().upper()

            if not timestamp or not tipo:
                descartados += 1
                continue

            evento = {
                "timestamp": timestamp,
                "evento": tipo,
                "nivel_borda": numero(
                    linha["nivel_borda"],
                    "nivel_borda"
                ),
            }

            # O detector novo informa a região testada.
            # Mantém compatibilidade com entregas antigas.
            if linha.get("regiao_anterior") not in (
                None, ""
            ):
                evento["regiao"] = inteiro(
                    linha["regiao_anterior"],
                    "regiao_anterior"
                )
            else:
                evento["regiao"] = None

            eventos.append(evento)

        except (ValueError, TypeError, KeyError):
            descartados += 1

    return eventos, descartados


def associar_eventos_regioes(velas, eventos):
    """
    Associa cada evento à região da borda testada.

    Quando a entrega contém regiao_anterior,
    utiliza esse valor.

    Para arquivos antigos, utiliza a região da vela
    correspondente ao timestamp.
    """

    regiao_por_tempo = {
        vela["timestamp"]: vela["eixo_y"]
        for vela in velas
    }

    eventos_por_regiao = defaultdict(list)

    for evento in eventos:
        regiao = evento["regiao"]

        if regiao is None:
            regiao = regiao_por_tempo.get(
                evento["timestamp"]
            )

        if regiao is None:
            continue

        eventos_por_regiao[regiao].append(
            evento["evento"]
        )

    return eventos_por_regiao


def calcular_regioes(velas, eventos_por_regiao):
    regioes = defaultdict(lambda: {
        "visitas": 0,
        "toques": 0,
        "rejeicoes": 0,
        "rompimentos": 0,
        "rejeicoes_superiores": 0,
        "rejeicoes_inferiores": 0,
        "rompimentos_alta": 0,
        "rompimentos_baixa": 0,
        "toques_superiores": 0,
        "toques_inferiores": 0,
    })

    for vela in velas:
        regiao = vela["eixo_y"]
        regioes[regiao]["visitas"] += 1

    for regiao, eventos in eventos_por_regiao.items():
        dados = regioes[regiao]

        for evento in eventos:
            if evento.startswith("TOQUE_"):
                dados["toques"] += 1

                if evento == "TOQUE_SUPERIOR":
                    dados["toques_superiores"] += 1

                elif evento == "TOQUE_INFERIOR":
                    dados["toques_inferiores"] += 1

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
            rejeicoes / testes if testes else 0.0
        )

        prob_rompimento = (
            rompimentos / testes if testes else 0.0
        )

        resultados.append({
            "regiao": regiao,
            "limite_inferior": round(
                regiao * LARGURA_REGIAO, 5
            ),
            "limite_superior": round(
                (regiao + 1) * LARGURA_REGIAO, 5
            ),
            "visitas": dados["visitas"],
            "toques": toques,
            "rejeicoes": rejeicoes,
            "rompimentos": rompimentos,
            "prob_rejeicao": round(
                prob_rejeicao, 4
            ),
            "prob_rompimento": round(
                prob_rompimento, 4
            ),
            "limite_inferior_rejeicao": round(
                wilson(testes, rejeicoes), 4
            ),
            "amostras": testes,
            "amostra_suficiente": (
                "SIM" if testes >= MIN_AMOSTRAS
                else "NAO"
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
            ],
            "toques_superiores": dados[
                "toques_superiores"
            ],
            "toques_inferiores": dados[
                "toques_inferiores"
            ],
        })

    return resultados


def calcular_transicoes(velas):
    contagem = Counter()
    saidas = Counter()
    visitas = Counter()

    # Conta todas as visitas, mas comprime velas
    # consecutivas que permaneceram na mesma região.
    sequencia = []

    for vela in velas:
        regiao = vela["eixo_y"]
        visitas[regiao] += 1

        if not sequencia or sequencia[-1] != regiao:
            sequencia.append(regiao)

    for origem, destino in zip(
        sequencia[:-1],
        sequencia[1:]
    ):
        contagem[(origem, destino)] += 1
        saidas[origem] += 1

    resultados = []

    for (origem, destino), quantidade in sorted(
        contagem.items()
    ):
        total = saidas[origem]

        probabilidade = (
            quantidade / total if total else 0.0
        )

        resultados.append({
            "origem": origem,
            "destino": destino,
            "transicoes": quantidade,
            "saidas_origem": total,
            "probabilidade": round(
                probabilidade, 4
            ),
            "visitas_origem": visitas[origem],
            "amostra_suficiente": (
                "SIM" if total >= MIN_AMOSTRAS
                else "NAO"
            ),
        })

    return resultados


def salvar_csv(caminho, registros, campos):
    DATA.mkdir(parents=True, exist_ok=True)

    with caminho.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(registros)


def gerar_resumo(velas, regioes, transicoes,
                 eventos, descartados_velas,
                 descartados_eventos):

    total_visitas = sum(
        regiao["visitas"]
        for regiao in regioes
    )

    regioes_validas = [
        regiao for regiao in regioes
        if regiao["amostra_suficiente"] == "SIM"
    ]

    melhores_rejeicoes = sorted(
        regioes_validas,
        key=lambda item: (
            item["prob_rejeicao"],
            item["amostras"]
        ),
        reverse=True
    )[:10]

    melhores_transicoes = sorted(
        [
            item for item in transicoes
            if item["amostra_suficiente"] == "SIM"
        ],
        key=lambda item: (
            item["probabilidade"],
            item["transicoes"]
        ),
        reverse=True
    )[:15]

    contagem_eventos = Counter(
        evento["evento"]
        for evento in eventos
    )

    return {
        "versao": 2,
        "modulo": "processador_estatistico_m5",
        "atualizado_em": agora(),

        "metodo": {
            "eixo_x": "Tempo cronológico M5",
            "eixo_y": "Regiões fixas de preço",
            "largura_regiao_usd": LARGURA_REGIAO,
            "atr_altera_largura": False,
            "confianca": "Wilson 95%",
            "minimo_amostras": MIN_AMOSTRAS,
        },

        "resumo": {
            "total_velas": len(velas),
            "total_regioes": len(regioes),
            "total_visitas": total_visitas,
            "total_eventos_borda": len(eventos),
            "total_transicoes": sum(
                item["transicoes"]
                for item in transicoes
            ),
            "regioes_com_amostra_suficiente": len(
                regioes_validas
            ),
            "transicoes_com_amostra_suficiente": sum(
                1 for item in transicoes
                if item["amostra_suficiente"] == "SIM"
            ),
            "velas_descartadas": descartados_velas,
            "eventos_descartados": descartados_eventos,
        },

        "eventos": dict(contagem_eventos),

        "melhores_regioes_rejeicao": melhores_rejeicoes,

        "transicoes_mais_frequentes": melhores_transicoes,

        "observacao": (
            "As frequências representam o histórico observado. "
            "Amostras pequenas não devem ser tratadas como "
            "probabilidades confiáveis nem como garantia futura."
        ),
    }


def processar():
    print("======================================")
    print("PROCESSADOR ESTATISTICO TABULEIRO M5")
    print("======================================")

    velas, descartados_velas = carregar_tabuleiro()

    eventos, descartados_eventos = carregar_bordas()

    print("Velas válidas:", len(velas))
    print("Eventos válidos:", len(eventos))

    eventos_por_regiao = associar_eventos_regioes(
        velas,
        eventos
    )

    regioes = calcular_regioes(
        velas,
        eventos_por_regiao
    )

    transicoes = calcular_transicoes(velas)

    campos_regioes = [
        "regiao",
        "limite_inferior",
        "limite_superior",
        "visitas",
        "toques",
        "rejeicoes",
        "rompimentos",
        "prob_rejeicao",
        "prob_rompimento",
        "limite_inferior_rejeicao",
        "amostras",
        "amostra_suficiente",
        "rejeicoes_superiores",
        "rejeicoes_inferiores",
        "rompimentos_alta",
        "rompimentos_baixa",
        "toques_superiores",
        "toques_inferiores",
    ]

    campos_transicoes = [
        "origem",
        "destino",
        "transicoes",
        "saidas_origem",
        "probabilidade",
        "visitas_origem",
        "amostra_suficiente",
    ]

    salvar_csv(
        SAIDA_REGIOES,
        regioes,
        campos_regioes
    )

    salvar_csv(
        SAIDA_TRANSICOES,
        transicoes,
        campos_transicoes
    )

    resumo = gerar_resumo(
        velas,
        regioes,
        transicoes,
        eventos,
        descartados_velas,
        descartados_eventos
    )

    DATA.mkdir(parents=True, exist_ok=True)

    SAIDA_RESUMO.write_text(
        json.dumps(
            resumo,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("--------------------------------------")
    print("Regiões analisadas:", len(regioes))
    print("Transições identificadas:", len(transicoes))
    print(
        "Regiões com amostra suficiente:",
        resumo["resumo"]["regioes_com_amostra_suficiente"]
    )
    print("--------------------------------------")
    print("Arquivos de entrega:")
    print(SAIDA_REGIOES)
    print(SAIDA_TRANSICOES)
    print(SAIDA_RESUMO)
    print("--------------------------------------")
    print("Banco paralelo criado: NÃO")
    print("Gravação direta nos bancos oficiais: NÃO")
    print("Persistência: Coletor e Organizador")
    print("PROCESSAMENTO CONCLUÍDO")
    print("======================================")


if __name__ == "__main__":
    processar()
