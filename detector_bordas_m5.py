
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DETECTOR DE BORDAS M5 - NOVA VERSAO

Fonte:
    data/graficos.db.gz
    Tabela: candles

Saidas:
    data/bordas_m5.csv
    data/eventos_bordas_m5.csv

Objetivo:
    Encontrar niveis de preco que funcionam como bordas,
    medindo toques, rejeicoes e rompimentos.

REGRAS:
    X = tempo cronologico.
    Y = preco.
    Largura fixa das regioes = US$ 1,50.
    ATR confirma movimentos, mas nao altera regioes.
    Nao executa ordens.
    Nao cria bancos de dados.
    Nao modifica o banco oficial.
"""

import csv
import gzip
import math
import os
import sqlite3
import tempfile

from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

BANCO = DATA / "graficos.db.gz"
SAIDA_BORDAS = DATA / "bordas_m5.csv"
SAIDA_EVENTOS = DATA / "eventos_bordas_m5.csv"

LARGURA_REGIAO = 1.50
TOLERANCIA_TOQUE = 0.10
CONFIRMACAO_ATR = 0.15
PERIODO_ATR = 14

MIN_TESTES_BORDA = 2
MIN_ATR = 0.00001


def encontrar_coluna(colunas, alternativas):
    mapa = {
        str(coluna).lower().strip(): coluna
        for coluna in colunas
    }

    for alternativa in alternativas:
        encontrada = mapa.get(alternativa.lower())

        if encontrada:
            return encontrada

    return None


def carregar_velas():
    if not BANCO.is_file():
        raise FileNotFoundError(
            f"Banco nao encontrado: {BANCO}"
        )

    temporario = tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False
    )

    caminho_temp = temporario.name
    temporario.close()

    try:
        with gzip.open(BANCO, "rb") as origem:
            with open(caminho_temp, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

        conexao = sqlite3.connect(caminho_temp)
        conexao.row_factory = sqlite3.Row

        tabelas = conexao.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

        nomes_tabelas = [
            linha["name"] for linha in tabelas
        ]

        if "candles" not in nomes_tabelas:
            raise RuntimeError(
                "Tabela candles nao encontrada no banco."
            )

        colunas = [
            linha["name"]
            for linha in conexao.execute(
                "PRAGMA table_info(candles)"
            ).fetchall()
        ]

        campos = {
            "timeframe": encontrar_coluna(
                colunas, ["timeframe", "periodo"]
            ),
            "timestamp": encontrar_coluna(
                colunas, ["timestamp", "datetime", "data_hora"]
            ),
            "open": encontrar_coluna(
                colunas, ["open", "abertura"]
            ),
            "high": encontrar_coluna(
                colunas, ["high", "maxima", "máxima"]
            ),
            "low": encontrar_coluna(
                colunas, ["low", "minima", "mínima"]
            ),
            "close": encontrar_coluna(
                colunas, ["close", "fechamento"]
            ),
        }

        ausentes = [
            nome for nome, coluna in campos.items()
            if coluna is None
        ]

        if ausentes:
            raise RuntimeError(
                "Colunas ausentes na tabela candles: "
                + ", ".join(ausentes)
            )

        consulta = f"""
            SELECT
                "{campos['timeframe']}" AS timeframe,
                "{campos['timestamp']}" AS timestamp,
                "{campos['open']}" AS open,
                "{campos['high']}" AS high,
                "{campos['low']}" AS low,
                "{campos['close']}" AS close
            FROM candles
            ORDER BY "{campos['timestamp']}" ASC
        """

        registros = conexao.execute(consulta).fetchall()
        conexao.close()

        velas = []

        for linha in registros:
            periodo = str(linha["timeframe"]).lower().strip()

            if periodo not in ("5min", "5m", "m5"):
                continue

            try:
                vela = {
                    "timestamp": str(linha["timestamp"]),
                    "open": float(linha["open"]),
                    "high": float(linha["high"]),
                    "low": float(linha["low"]),
                    "close": float(linha["close"]),
                }

                valores = [
                    vela["open"],
                    vela["high"],
                    vela["low"],
                    vela["close"],
                ]

                if not all(math.isfinite(v) for v in valores):
                    continue

                if min(valores) <= 0:
                    continue

                if vela["high"] < vela["low"]:
                    continue

                if vela["high"] < max(
                    vela["open"], vela["close"]
                ):
                    continue

                if vela["low"] > min(
                    vela["open"], vela["close"]
                ):
                    continue

                velas.append(vela)

            except (ValueError, TypeError):
                continue

        # Uma vela por timestamp.
        unicas = {}

        for vela in velas:
            unicas[vela["timestamp"]] = vela

        velas = sorted(
            unicas.values(),
            key=lambda item: item["timestamp"]
        )

        if len(velas) < PERIODO_ATR + 2:
            raise RuntimeError(
                "Historico M5 insuficiente."
            )

        return velas

    finally:
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)


def calcular_atr(velas):
    resultados = []
    ranges = []

    for indice, vela in enumerate(velas):
        if indice == 0:
            true_range = vela["high"] - vela["low"]
        else:
            anterior = velas[indice - 1]

            true_range = max(
                vela["high"] - vela["low"],
                abs(vela["high"] - anterior["close"]),
                abs(vela["low"] - anterior["close"]),
            )

        ranges.append(true_range)

        inicio = max(
            0,
            indice - PERIODO_ATR + 1
        )

        janela = ranges[inicio:indice + 1]

        atr = sum(janela) / len(janela)

        resultados.append(max(atr, MIN_ATR))

    return resultados


def nivel_inferior(preco):
    return math.floor(
        preco / LARGURA_REGIAO
    ) * LARGURA_REGIAO


def identificar_eventos(velas, atrs):
    eventos = []
    estatisticas = defaultdict(
        lambda: {
            "testes": 0,
            "toques": 0,
            "rejeicoes": 0,
            "rompimentos": 0,
            "rejeicoes_alta": 0,
            "rejeicoes_baixa": 0,
            "rompimentos_alta": 0,
            "rompimentos_baixa": 0,
        }
    )

    for indice in range(1, len(velas)):
        anterior = velas[indice - 1]
        atual = velas[indice]

        atr = atrs[indice]
        confirmacao = atr * CONFIRMACAO_ATR

        # Busca somente os niveis que a vela pode ter testado.
        primeiro = nivel_inferior(
            atual["low"] - TOLERANCIA_TOQUE
        )

        ultimo = nivel_inferior(
            atual["high"] + TOLERANCIA_TOQUE
        )

        quantidade = int(
            round(
                (ultimo - primeiro) / LARGURA_REGIAO
            )
        )

        for posicao in range(quantidade + 1):
            nivel = round(
                primeiro + posicao * LARGURA_REGIAO,
                5
            )

            # A vela precisa alcancar a regiao da borda.
            tocou = (
                atual["low"] <= nivel + TOLERANCIA_TOQUE
                and atual["high"] >= nivel - TOLERANCIA_TOQUE
            )

            if not tocou:
                continue

            fechamento_anterior = anterior["close"]
            fechamento = atual["close"]

            tipo = None

            # Preco vinha de baixo e encontrou a borda.
            if fechamento_anterior < nivel:
                if fechamento > nivel + confirmacao:
                    tipo = "ROMPIMENTO_ALTA"

                elif fechamento < nivel - confirmacao:
                    tipo = "REJEICAO_SUPERIOR"

                else:
                    tipo = "TOQUE_SUPERIOR"

            # Preco vinha de cima e encontrou a borda.
            elif fechamento_anterior > nivel:
                if fechamento < nivel - confirmacao:
                    tipo = "ROMPIMENTO_BAIXA"

                elif fechamento > nivel + confirmacao:
                    tipo = "REJEICAO_INFERIOR"

                else:
                    tipo = "TOQUE_INFERIOR"

            else:
                continue

            dados = estatisticas[nivel]

            dados["testes"] += 1

            if tipo.startswith("TOQUE"):
                dados["toques"] += 1

            elif tipo.startswith("REJEICAO"):
                dados["rejeicoes"] += 1

                if tipo == "REJEICAO_SUPERIOR":
                    dados["rejeicoes_baixa"] += 1
                else:
                    dados["rejeicoes_alta"] += 1

            elif tipo.startswith("ROMPIMENTO"):
                dados["rompimentos"] += 1

                if tipo == "ROMPIMENTO_ALTA":
                    dados["rompimentos_alta"] += 1
                else:
                    dados["rompimentos_baixa"] += 1

            eventos.append({
                "timestamp": atual["timestamp"],
                "indice_vela": indice,
                "nivel_borda": round(nivel, 5),
                "evento": tipo,
                "preco_fechamento": round(
                    fechamento, 5
                ),
                "atr": round(atr, 5),
                "distancia_atr": round(
                    abs(fechamento - nivel) / atr, 4
                ),
                "largura_regiao": LARGURA_REGIAO,
            })

    return eventos, estatisticas


def calcular_forca(dados):
    testes = dados["testes"]

    if testes == 0:
        return 0.0

    # Rejeicoes fortalecem a evidencia da borda.
    # Rompimentos reduzem a evidencia de resistencia.
    pontuacao = (
        dados["rejeicoes"] * 2.0
        + dados["toques"] * 0.5
        - dados["rompimentos"] * 2.0
    )

    forca = (
        pontuacao / (testes * 2.0)
    ) * 100

    return round(
        max(0.0, min(100.0, forca)),
        2
    )


def classificar_borda(dados):
    if dados["testes"] < MIN_TESTES_BORDA:
        return "EVIDENCIA_INSUFICIENTE"

    if dados["rompimentos"] > dados["rejeicoes"]:
        return "BORDA_FRAGIL"

    if dados["rejeicoes"] >= 3:
        return "BORDA_FORTE"

    if dados["rejeicoes"] >= 1:
        return "BORDA_EM_OBSERVACAO"

    return "BORDA_NAO_CONFIRMADA"


def salvar_eventos(eventos):
    campos = [
        "timestamp",
        "indice_vela",
        "nivel_borda",
        "evento",
        "preco_fechamento",
        "atr",
        "distancia_atr",
        "largura_regiao",
    ]

    with SAIDA_EVENTOS.open(
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


def salvar_bordas(estatisticas):
    campos = [
        "nivel_borda",
        "limite_inferior",
        "limite_superior",
        "testes",
        "toques",
        "rejeicoes",
        "rompimentos",
        "rejeicoes_alta",
        "rejeicoes_baixa",
        "rompimentos_alta",
        "rompimentos_baixa",
        "forca",
        "classificacao",
    ]

    with SAIDA_BORDAS.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()

        for nivel in sorted(estatisticas):
            dados = estatisticas[nivel]

            escritor.writerow({
                "nivel_borda": round(nivel, 5),
                "limite_inferior": round(
                    nivel - LARGURA_REGIAO, 5
                ),
                "limite_superior": round(
                    nivel + LARGURA_REGIAO, 5
                ),
                "testes": dados["testes"],
                "toques": dados["toques"],
                "rejeicoes": dados["rejeicoes"],
                "rompimentos": dados["rompimentos"],
                "rejeicoes_alta": dados["rejeicoes_alta"],
                "rejeicoes_baixa": dados["rejeicoes_baixa"],
                "rompimentos_alta": dados["rompimentos_alta"],
                "rompimentos_baixa": dados["rompimentos_baixa"],
                "forca": calcular_forca(dados),
                "classificacao": classificar_borda(dados),
            })


def gerar_resumo(velas, eventos, estatisticas):
    total_toques = sum(
        dados["toques"]
        for dados in estatisticas.values()
    )

    total_rejeicoes = sum(
        dados["rejeicoes"]
        for dados in estatisticas.values()
    )

    total_rompimentos = sum(
        dados["rompimentos"]
        for dados in estatisticas.values()
    )

    fortes = sum(
        1 for dados in estatisticas.values()
        if classificar_borda(dados) == "BORDA_FORTE"
    )

    print("\n========================================")
    print("DETECTOR DE BORDAS M5")
    print("========================================")
    print("Velas analisadas:", len(velas))
    print("Niveis de preco encontrados:", len(estatisticas))
    print("Eventos identificados:", len(eventos))
    print("----------------------------------------")
    print("Toques:", total_toques)
    print("Rejeicoes:", total_rejeicoes)
    print("Rompimentos:", total_rompimentos)
    print("Bordas fortes:", fortes)
    print("----------------------------------------")
    print("Largura fixa: US$ 1,50")
    print("ATR altera regioes: NAO")
    print("Executa ordens: NAO")
    print("Modifica banco oficial: NAO")
    print("----------------------------------------")
    print("Arquivo de bordas:", SAIDA_BORDAS)
    print("Arquivo de eventos:", SAIDA_EVENTOS)
    print("========================================\n")


def processar():
    DATA.mkdir(parents=True, exist_ok=True)

    print("Carregando velas M5 do banco oficial...")

    velas = carregar_velas()

    print("Calculando ATR...")

    atrs = calcular_atr(velas)

    print("Identificando bordas e eventos...")

    eventos, estatisticas = identificar_eventos(
        velas, atrs
    )

    salvar_eventos(eventos)
    salvar_bordas(estatisticas)

    gerar_resumo(
        velas,
        eventos,
        estatisticas
    )

    print("PROCESSAMENTO CONCLUIDO")


if __name__ == "__main__":
    processar()
