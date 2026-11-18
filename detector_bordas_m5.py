#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detector de bordas M5 integrado ao banco SQLite oficial XAUUSD.

As bordas e os eventos são persistidos como registros ESTADO na tabela
oficial tabuleiro. Nenhum CSV, banco auxiliar ou tabela adicional é criado.
"""

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BANCO = ROOT / "data" / "xauusd.db"
ESQUEMA = ROOT / "database" / "esquema_xauusd.sql"

LARGURA_REGIAO = 1.50
TOLERANCIA_TOQUE = 0.10
CONFIRMACAO_ATR = 0.15
PERIODO_ATR = 14
MIN_TESTES_BORDA = 2
MIN_ATR = 0.00001
TIMEFRAME = "M5"
VERSAO = "2.0"
ORIGEM = "DETECTOR_BORDAS_M5"


def abrir_banco():
    if not BANCO.is_file():
        raise FileNotFoundError(f"Banco oficial não encontrado: {BANCO}")
    if not ESQUEMA.is_file():
        raise FileNotFoundError(f"Esquema oficial não encontrado: {ESQUEMA}")
    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 30000")
    return conexao


def verificar_estrutura(conexao):
    conexao.executescript(ESQUEMA.read_text(encoding="utf-8"))
    colunas = {
        linha["name"] for linha in conexao.execute(
            "PRAGMA table_info(tabuleiro)"
        ).fetchall()
    }
    obrigatorias = {
        "data_hora", "timeframe", "tipo_registro", "sequencia_json",
        "quantidade_candles", "ocorrencias_encontradas",
        "ocorrencias_exatas", "distancia_maxima", "versao",
    }
    faltantes = obrigatorias - colunas
    if faltantes:
        raise RuntimeError(
            "Tabela tabuleiro incompatível com o esquema oficial. "
            "Colunas ausentes: " + ", ".join(sorted(faltantes))
        )


def carregar_velas(conexao):
    corte = datetime.now(timezone.utc) - timedelta(days=10)
    linhas = conexao.execute(
        """
        SELECT data_hora, abertura, maxima, minima, fechamento
        FROM m5
        WHERE data_hora >= ?
        ORDER BY data_hora ASC
        """,
        (corte.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    agora = datetime.now(timezone.utc)
    velas_por_horario = {}
    for linha in linhas:
        timestamp = linha["data_hora"]
        try:
            horario = datetime.strptime(
                timestamp, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            valores = {
                "open": float(linha["abertura"]),
                "high": float(linha["maxima"]),
                "low": float(linha["minima"]),
                "close": float(linha["fechamento"]),
            }
        except (TypeError, ValueError) as erro:
            raise RuntimeError(
                f"Vela M5 inválida no intervalo {timestamp}: {erro}"
            ) from erro

        if horario + timedelta(minutes=5) > agora:
            continue

        if not all(math.isfinite(v) and v > 0 for v in valores.values()):
            raise RuntimeError(f"OHLC inválido no intervalo M5 {timestamp}.")
        if valores["high"] < max(
            valores["open"], valores["close"], valores["low"]
        ) or valores["low"] > min(
            valores["open"], valores["close"], valores["high"]
        ):
            raise RuntimeError(f"Relação OHLC inválida no intervalo M5 {timestamp}.")

        velas_por_horario[timestamp] = {
            "timestamp": timestamp,
            **valores,
        }

    velas = [velas_por_horario[k] for k in sorted(velas_por_horario)]
    if len(velas) < PERIODO_ATR + 2:
        raise RuntimeError(
            f"Histórico M5 insuficiente: {len(velas)} velas fechadas "
            f"nos últimos 10 dias; necessárias pelo menos {PERIODO_ATR + 2}."
        )
    return velas


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
        inicio = max(0, indice - PERIODO_ATR + 1)
        janela = ranges[inicio:indice + 1]
        atr = sum(janela) / len(janela)
        resultados.append(max(atr, MIN_ATR))
    return resultados


def nivel_inferior(preco):
    return math.floor(preco / LARGURA_REGIAO) * LARGURA_REGIAO


def identificar_eventos(velas, atrs):
    eventos = []
    estatisticas = defaultdict(lambda: {
        "testes": 0,
        "toques": 0,
        "rejeicoes": 0,
        "rompimentos": 0,
        "rejeicoes_alta": 0,
        "rejeicoes_baixa": 0,
        "rompimentos_alta": 0,
        "rompimentos_baixa": 0,
    })

    for indice in range(1, len(velas)):
        anterior = velas[indice - 1]
        atual = velas[indice]
        atr = atrs[indice]
        confirmacao = atr * CONFIRMACAO_ATR

        primeiro = nivel_inferior(atual["low"] - TOLERANCIA_TOQUE)
        ultimo = nivel_inferior(atual["high"] + TOLERANCIA_TOQUE)
        quantidade = int(round((ultimo - primeiro) / LARGURA_REGIAO))

        for posicao in range(quantidade + 1):
            nivel = round(primeiro + posicao * LARGURA_REGIAO, 5)
            tocou = (
                atual["low"] <= nivel + TOLERANCIA_TOQUE
                and atual["high"] >= nivel - TOLERANCIA_TOQUE
            )
            if not tocou:
                continue

            fechamento_anterior = anterior["close"]
            fechamento = atual["close"]
            tipo = None

            if fechamento_anterior < nivel:
                if fechamento > nivel + confirmacao:
                    tipo = "ROMPIMENTO_ALTA"
                elif fechamento < nivel - confirmacao:
                    tipo = "REJEICAO_SUPERIOR"
                else:
                    tipo = "TOQUE_SUPERIOR"
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
                "preco_fechamento": round(fechamento, 5),
                "atr": round(atr, 5),
                "distancia_atr": round(abs(fechamento - nivel) / atr, 4),
                "largura_regiao": LARGURA_REGIAO,
            })

    return eventos, estatisticas


def calcular_forca(dados):
    testes = dados["testes"]
    if testes == 0:
        return 0.0
    pontuacao = (
        dados["rejeicoes"] * 2.0
        + dados["toques"] * 0.5
        - dados["rompimentos"] * 2.0
    )
    return round(max(0.0, min(100.0, pontuacao / (testes * 2.0) * 100)), 2)


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


def salvar_resultados(conexao, velas, eventos, estatisticas):
    referencia = velas[-1]["timestamp"]

    # Remove somente os registros deste detector; os registros do Tabuleiro
    # probabilístico e de outros módulos permanecem intactos.
    conexao.execute(
        """
        DELETE FROM tabuleiro
        WHERE timeframe = ?
          AND tipo_registro = 'ESTADO'
          AND substr(sequencia_json, 1, ?) = ?
        """,
        (TIMEFRAME, len('{"origem":"' + ORIGEM + '"'), '{"origem":"' + ORIGEM + '"'),
    )

    registros = []
    for nivel in sorted(estatisticas):
        dados = estatisticas[nivel]
        payload = {
            "origem": ORIGEM,
            "tipo": "BORDA",
            "nivel_borda": round(nivel, 5),
            "limite_inferior": round(nivel - LARGURA_REGIAO, 5),
            "limite_superior": round(nivel + LARGURA_REGIAO, 5),
            **dados,
            "forca": calcular_forca(dados),
            "classificacao": classificar_borda(dados),
            "largura_regiao": LARGURA_REGIAO,
            "confirmacao_atr": CONFIRMACAO_ATR,
            "versao": VERSAO,
        }
        registros.append((
            referencia, TIMEFRAME, "ESTADO",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            len(velas), dados["testes"], dados["rejeicoes"],
            None, VERSAO,
        ))

    for evento in eventos:
        payload = {
            "origem": ORIGEM,
            "tipo": "EVENTO_BORDA",
            **evento,
            "versao": VERSAO,
        }
        registros.append((
            evento["timestamp"], TIMEFRAME, "ESTADO",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            len(velas), 1, 0, DISTANCIA_MAXIMA_BORDA, VERSAO,
        ))

    if registros:
        conexao.executemany(
            """
            INSERT INTO tabuleiro (
                data_hora, timeframe, tipo_registro, sequencia_json,
                quantidade_candles, ocorrencias_encontradas,
                ocorrencias_exatas, distancia_maxima, versao
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

    corte = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conexao.execute(
        "DELETE FROM tabuleiro WHERE data_hora < ?",
        (corte,),
    )



def gerar_resumo(velas, eventos, estatisticas):
    total_toques = sum(d["toques"] for d in estatisticas.values())
    total_rejeicoes = sum(d["rejeicoes"] for d in estatisticas.values())
    total_rompimentos = sum(d["rompimentos"] for d in estatisticas.values())
    fortes = sum(
        1 for d in estatisticas.values()
        if classificar_borda(d) == "BORDA_FORTE"
    )

    print("\n========================================")
    print("DETECTOR DE BORDAS M5")
    print("========================================")
    print("Velas analisadas:", len(velas))
    print("Níveis de preço encontrados:", len(estatisticas))
    print("Eventos identificados:", len(eventos))
    print("----------------------------------------")
    print("Toques:", total_toques)
    print("Rejeições:", total_rejeicoes)
    print("Rompimentos:", total_rompimentos)
    print("Bordas fortes:", fortes)
    print("----------------------------------------")
    print("Largura fixa: US$ 1,50")
    print("ATR altera regiões: NÃO")
    print("Executa ordens: NÃO")
    print("Persistência: tabela oficial tabuleiro")
    print("Retenção: 10 dias")
    print("========================================\n")


def processar():
    print("Carregando velas M5 do banco oficial...")
    conexao = abrir_banco()
    try:
        verificar_estrutura(conexao)
        velas = carregar_velas(conexao)
        print("Calculando ATR...")
        atrs = calcular_atr(velas)
        print("Identificando bordas e eventos...")
        eventos, estatisticas = identificar_eventos(velas, atrs)

        try:
            salvar_resultados(conexao, velas, eventos, estatisticas)
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise

        gerar_resumo(velas, eventos, estatisticas)
        print("PROCESSAMENTO CONCLUÍDO")
    finally:
        conexao.close()


if __name__ == "__main__":
    try:
        processar()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
