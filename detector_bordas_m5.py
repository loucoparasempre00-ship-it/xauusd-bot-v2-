
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DETECTOR DE BORDAS M5 - TABULEIRO ESTAVEL

Responsabilidades:
- Ler a matriz XY produzida pelo Tabuleiro.
- Identificar toques nas bordas das regiões.
- Identificar rejeições.
- Identificar rompimentos confirmados.
- Gerar arquivo de entrega para o Coletor.

REGRAS:
- Eixo X = tempo cronológico M5.
- Eixo Y = regiões fixas de preço.
- Largura padrão da região = US$ 1,50.
- ATR mede distância de confirmação.
- ATR não altera as regiões.
- Não cria bancos de dados.
- Não grava nos bancos oficiais.
"""

import csv
import os
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ENTRADA = DATA / "tabuleiro_m5.csv"
SAIDA = DATA / "eventos_bordas_m5.csv"

LARGURA_REGIAO_PADRAO = 1.50

# Tolerância fixa para identificar aproximação da borda.
TOLERANCIA_USD = 0.10

# ATR utilizado somente para confirmar rompimentos e rejeições.
CONFIRMACAO_ATR = 0.15

MIN_ATR = 0.00001


def numero(valor, campo):
    try:
        resultado = float(
            str(valor).strip().replace(",", ".")
        )
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor inválido em {campo}: {valor}"
        )

    return resultado


def inteiro(valor, campo):
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor inválido em {campo}: {valor}"
        )


def carregar_dados():
    if not ENTRADA.is_file():
        raise FileNotFoundError(
            f"Matriz do Tabuleiro não encontrada: {ENTRADA}"
        )

    dados_por_tempo = {}

    with ENTRADA.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:

        leitor = csv.DictReader(arquivo)
        campos = leitor.fieldnames or []

        obrigatorias = [
            "timestamp",
            "eixo_x",
            "eixo_y",
            "open",
            "high",
            "low",
            "close",
            "atr",
            "largura_regiao",
        ]

        ausentes = [
            campo for campo in obrigatorias
            if campo not in campos
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes na matriz: {ausentes}"
            )

        for linha in leitor:
            try:
                timestamp = str(
                    linha["timestamp"] or ""
                ).strip()

                if not timestamp:
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
                    "largura_regiao": numero(
                        linha["largura_regiao"],
                        "largura_regiao"
                    ),
                }

                if vela["atr"] <= MIN_ATR:
                    continue

                if vela["high"] < vela["low"]:
                    continue

                if vela["largura_regiao"] <= 0:
                    continue

                if vela["high"] < max(
                    vela["open"], vela["close"]
                ):
                    continue

                if vela["low"] > min(
                    vela["open"], vela["close"]
                ):
                    continue

                # Uma ocorrência por timestamp.
                dados_por_tempo[timestamp] = vela

            except (ValueError, TypeError, KeyError):
                continue

    dados = sorted(
        dados_por_tempo.values(),
        key=lambda item: item["eixo_x"]
    )

    if len(dados) < 2:
        raise RuntimeError(
            "Histórico insuficiente para detectar bordas."
        )

    # O eixo X precisa ser cronológico e único.
    for anterior, atual in zip(dados, dados[1:]):
        if atual["eixo_x"] <= anterior["eixo_x"]:
            raise ValueError(
                "Eixo X inválido: posições repetidas "
                "ou fora de ordem."
            )

    return dados


def identificar_bordas(velas):
    eventos = []

    for indice in range(1, len(velas)):
        anterior = velas[indice - 1]
        atual = velas[indice]

        largura = anterior["largura_regiao"]

        if largura <= 0:
            largura = LARGURA_REGIAO_PADRAO

        # As bordas vêm da região da vela anterior.
        # Não são calculadas por máximas móveis.
        regiao_anterior = anterior["eixo_y"]

        limite_inferior = regiao_anterior * largura
        limite_superior = limite_inferior + largura

        abertura = atual["open"]
        maxima = atual["high"]
        minima = atual["low"]
        fechamento = atual["close"]
        atr = atual["atr"]

        confirmacao = atr * CONFIRMACAO_ATR

        # Verifica aproximação ou passagem pelas bordas.
        toque_superior = (
            maxima >= limite_superior - TOLERANCIA_USD
        )

        toque_inferior = (
            minima <= limite_inferior + TOLERANCIA_USD
        )

        rompimento_alta = (
            fechamento > limite_superior + confirmacao
        )

        rompimento_baixa = (
            fechamento < limite_inferior - confirmacao
        )

        rejeicao_superior = (
            toque_superior
            and fechamento < limite_superior - confirmacao
            and fechamento < abertura
        )

        rejeicao_inferior = (
            toque_inferior
            and fechamento > limite_inferior + confirmacao
            and fechamento > abertura
        )

        eventos_vela = []

        if rompimento_alta:
            eventos_vela.append((
                "ROMPIMENTO_ALTA",
                limite_superior
            ))

        elif rejeicao_superior:
            eventos_vela.append((
                "REJEICAO_SUPERIOR",
                limite_superior
            ))

        elif toque_superior:
            eventos_vela.append((
                "TOQUE_SUPERIOR",
                limite_superior
            ))

        if rompimento_baixa:
            eventos_vela.append((
                "ROMPIMENTO_BAIXA",
                limite_inferior
            ))

        elif rejeicao_inferior:
            eventos_vela.append((
                "REJEICAO_INFERIOR",
                limite_inferior
            ))

        elif toque_inferior:
            eventos_vela.append((
                "TOQUE_INFERIOR",
                limite_inferior
            ))

        for tipo, nivel in eventos_vela:
            distancia = abs(fechamento - nivel)

            eventos.append({
                "timestamp": atual["timestamp"],
                "indice_vela": atual["eixo_x"],
                "regiao_anterior": regiao_anterior,
                "regiao_atual": atual["eixo_y"],
                "evento": tipo,
                "nivel_borda": round(nivel, 5),
                "preco_fechamento": round(
                    fechamento, 5
                ),
                "atr": round(atr, 5),
                "distancia_atr": round(
                    distancia / atr, 4
                ),
                "largura_regiao": round(
                    largura, 5
                ),
            })

    return eventos


def salvar_eventos(eventos):
    DATA.mkdir(parents=True, exist_ok=True)

    campos = [
        "timestamp",
        "indice_vela",
        "regiao_anterior",
        "regiao_atual",
        "evento",
        "nivel_borda",
        "preco_fechamento",
        "atr",
        "distancia_atr",
        "largura_regiao",
    ]

    with SAIDA.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(eventos)


def gerar_resumo(eventos):
    contagem = Counter(
        evento["evento"]
        for evento in eventos
    )

    print("\n========== DETECTOR DE BORDAS M5 ==========")

    for tipo in [
        "TOQUE_SUPERIOR",
        "TOQUE_INFERIOR",
        "REJEICAO_SUPERIOR",
        "REJEICAO_INFERIOR",
        "ROMPIMENTO_ALTA",
        "ROMPIMENTO_BAIXA",
    ]:
        print(f"{tipo}: {contagem[tipo]}")

    print("-------------------------------------------")
    print("Total de eventos:", len(eventos))
    print("Arquivo de entrega:", SAIDA)
    print("Largura fixa padrão: US$ 1,50")
    print("ATR altera largura das regiões: NÃO")
    print("Gravação direta em banco: NÃO")
    print("Destino da persistência: Coletor e Organizador")
    print("===========================================\n")


def processar():
    print("======================================")
    print("DETECTOR DE BORDAS - TABULEIRO M5")
    print("======================================")

    velas = carregar_dados()

    print("Velas carregadas:", len(velas))

    eventos = identificar_bordas(velas)

    salvar_eventos(eventos)
    gerar_resumo(eventos)

    print("PROCESSAMENTO CONCLUÍDO")


if __name__ == "__main__":
    processar()
