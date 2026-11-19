#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consolida regioes e transicoes M5 nas duas tabelas oficiais do XAUUSD."""

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BANCO = ROOT / "data" / "xauusd.db"
ESQUEMA = ROOT / "database" / "esquema_xauusd.sql"

TIMEFRAME = "M5"
LARGURA_REGIAO = 1.50
MIN_AMOSTRAS = 20
Z_WILSON = 1.96
ORIGEM_DETECTOR = "DETECTOR_BORDAS_M5"
VERSAO = "2.0"


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def abrir_banco():
    if not BANCO.is_file():
        raise FileNotFoundError(f"Banco oficial nao encontrado: {BANCO}")
    if not ESQUEMA.is_file():
        raise FileNotFoundError(f"Esquema oficial nao encontrado: {ESQUEMA}")
    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 30000")
    conexao.executescript(ESQUEMA.read_text(encoding="utf-8"))
    return conexao


def validar_estrutura(conexao):
    tabelas = {
        row["name"] for row in conexao.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    esperadas = {
        "m5", "m15", "h1", "indicadores", "tabuleiro",
        "sinais_validados", "sinais_negados", "ranking",
        "estatisticas_regioes", "estatisticas_transicoes",
    }
    if tabelas != esperadas:
        raise RuntimeError(
            "Banco fora do esquema oficial de 10 tabelas. Encontradas: "
            + ", ".join(sorted(tabelas))
        )


def indice_regiao(preco):
    return math.floor(float(preco) / LARGURA_REGIAO)


def limites_regiao(indice):
    inferior = indice * LARGURA_REGIAO
    return round(inferior, 5), round(inferior + LARGURA_REGIAO, 5)


def calcular_wilson(total, sucessos):
    if total <= 0:
        return 0.0
    p = sucessos / total
    z2 = Z_WILSON ** 2
    centro = p + z2 / (2 * total)
    margem = Z_WILSON * math.sqrt(
        (p * (1 - p) / total) + z2 / (4 * total ** 2)
    )
    return round(max(0.0, (centro - margem) / (1 + z2 / total)), 6)


def carregar_velas(conexao):
    corte = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    linhas = conexao.execute(
        """
        SELECT data_hora, abertura, maxima, minima, fechamento
        FROM m5
        WHERE data_hora >= ?
        ORDER BY data_hora ASC
        """,
        (corte,),
    ).fetchall()

    velas = []
    for row in linhas:
        try:
            horario = datetime.strptime(
                row["data_hora"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            valores = {
                "open": float(row["abertura"]),
                "high": float(row["maxima"]),
                "low": float(row["minima"]),
                "close": float(row["fechamento"]),
            }
        except (TypeError, ValueError) as erro:
            raise RuntimeError(
                f"Vela M5 invalida em {row['data_hora']}: {erro}"
            ) from erro

        if horario + timedelta(minutes=5) > datetime.now(timezone.utc):
            continue
        if not all(math.isfinite(v) and v > 0 for v in valores.values()):
            raise RuntimeError(f"OHLC M5 invalido em {row['data_hora']}.")
        if (
            valores["high"] < max(valores["open"], valores["close"], valores["low"])
            or valores["low"] > min(valores["open"], valores["close"], valores["high"])
        ):
            raise RuntimeError(f"Relacao OHLC invalida em {row['data_hora']}.")
        velas.append({"timestamp": row["data_hora"], **valores})

    if len(velas) < 2:
        raise RuntimeError("Historico M5 fechado insuficiente para estatisticas.")
    return velas


def carregar_eventos_bordas(conexao):
    corte = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    linhas = conexao.execute(
        """
        SELECT data_hora, sequencia_json
        FROM tabuleiro
        WHERE timeframe = 'M5'
          AND tipo_registro = 'ESTADO'
          AND data_hora >= ?
          AND sequencia_json IS NOT NULL
        ORDER BY data_hora ASC, id ASC
        """,
        (corte,),
    ).fetchall()

    eventos = []
    descartados = 0
    for row in linhas:
        try:
            payload = json.loads(row["sequencia_json"])
            if payload.get("origem") != ORIGEM_DETECTOR:
                continue
            if payload.get("tipo") != "EVENTO_BORDA":
                continue
            tipo = str(payload.get("evento") or "").strip().upper()
            timestamp = str(payload.get("timestamp") or row["data_hora"]).strip()
            nivel = float(payload["nivel_borda"])
            if (
                not timestamp or not math.isfinite(nivel) or nivel <= 0
                or tipo not in {
                    "TOQUE_SUPERIOR", "TOQUE_INFERIOR",
                    "REJEICAO_SUPERIOR", "REJEICAO_INFERIOR",
                    "ROMPIMENTO_ALTA", "ROMPIMENTO_BAIXA",
                }
            ):
                raise ValueError("Evento de borda invalido")
            eventos.append({
                "timestamp": timestamp,
                "nivel_borda": nivel,
                "evento": tipo,
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            descartados += 1
    return eventos, descartados


def criar_estatisticas_regioes(velas, eventos):
    regioes = defaultdict(lambda: {
        "visitas": 0, "toques": 0, "rejeicoes": 0, "rompimentos": 0,
        "rejeicoes_superiores": 0, "rejeicoes_inferiores": 0,
        "rompimentos_alta": 0, "rompimentos_baixa": 0,
        "toques_superiores": 0, "toques_inferiores": 0,
        "total_eventos": 0,
    })

    for vela in velas:
        regioes[indice_regiao(vela["close"])]["visitas"] += 1

    for evento in eventos:
        dados = regioes[indice_regiao(evento["nivel_borda"])]
        dados["total_eventos"] += 1
        tipo = evento["evento"]
        if tipo == "TOQUE_SUPERIOR":
            dados["toques"] += 1
            dados["toques_superiores"] += 1
        elif tipo == "TOQUE_INFERIOR":
            dados["toques"] += 1
            dados["toques_inferiores"] += 1
        elif tipo == "REJEICAO_SUPERIOR":
            dados["rejeicoes"] += 1
            dados["rejeicoes_superiores"] += 1
        elif tipo == "REJEICAO_INFERIOR":
            dados["rejeicoes"] += 1
            dados["rejeicoes_inferiores"] += 1
        elif tipo == "ROMPIMENTO_ALTA":
            dados["rompimentos"] += 1
            dados["rompimentos_alta"] += 1
        elif tipo == "ROMPIMENTO_BAIXA":
            dados["rompimentos"] += 1
            dados["rompimentos_baixa"] += 1

    resultados = []
    for indice, dados in sorted(regioes.items()):
        inferior, superior = limites_regiao(indice)
        amostras = dados["toques"] + dados["rejeicoes"] + dados["rompimentos"]
        prob_toque = dados["toques"] / amostras if amostras else 0.0
        prob_rejeicao = dados["rejeicoes"] / amostras if amostras else 0.0
        prob_rompimento = dados["rompimentos"] / amostras if amostras else 0.0
        if amostras < MIN_AMOSTRAS:
            classificacao = "AMOSTRA_INSUFICIENTE"
        elif dados["rompimentos"] > dados["rejeicoes"]:
            classificacao = "BORDA_FRAGIL"
        elif prob_rejeicao >= 0.60:
            classificacao = "BORDA_FORTE"
        elif dados["rejeicoes"] > 0:
            classificacao = "BORDA_EM_OBSERVACAO"
        else:
            classificacao = "BORDA_NAO_CONFIRMADA"

        resultados.append({
            "regiao": indice,
            "limite_inferior": inferior,
            "limite_superior": superior,
            **dados,
            "prob_toque": round(prob_toque, 6),
            "prob_rejeicao": round(prob_rejeicao, 6),
            "prob_rompimento": round(prob_rompimento, 6),
            "wilson_rejeicao_inferior": calcular_wilson(
                amostras, dados["rejeicoes"]
            ),
            "amostras": amostras,
            "amostra_suficiente": int(amostras >= MIN_AMOSTRAS),
            "classificacao": classificacao,
        })
    return resultados


def calcular_transicoes(velas):
    sequencia = []
    for vela in velas:
        indice = indice_regiao(vela["close"])
        if not sequencia or sequencia[-1] != indice:
            sequencia.append(indice)

    contagem = Counter()
    saidas = Counter()
    visitas = Counter(sequencia)
    for origem, destino in zip(sequencia[:-1], sequencia[1:]):
        contagem[(origem, destino)] += 1
        saidas[origem] += 1

    resultados = []
    for (origem, destino), quantidade in sorted(contagem.items()):
        total = saidas[origem]
        resultados.append({
            "origem": origem,
            "destino": destino,
            "transicoes": quantidade,
            "saidas_origem": total,
            "probabilidade": round(quantidade / total if total else 0.0, 6),
            "visitas_origem": visitas[origem],
            "amostra_suficiente": int(total >= MIN_AMOSTRAS),
        })
    return resultados


def gravar_resultados(conexao, regioes, transicoes, referencia):
    atualizado = agora()
    conexao.execute("BEGIN IMMEDIATE")
    try:
        conexao.execute(
            "DELETE FROM estatisticas_regioes WHERE timeframe = ?",
            (TIMEFRAME,),
        )
        conexao.execute(
            "DELETE FROM estatisticas_transicoes WHERE timeframe = ?",
            (TIMEFRAME,),
        )

        for item in regioes:
            conexao.execute(
                """
                INSERT INTO estatisticas_regioes (
                    timeframe, regiao, limite_inferior, limite_superior,
                    visitas, toques, rejeicoes, rompimentos, prob_toque,
                    prob_rejeicao, prob_rompimento, wilson_rejeicao_inferior,
                    amostras, amostra_suficiente, rejeicoes_superiores,
                    rejeicoes_inferiores, rompimentos_alta, rompimentos_baixa,
                    toques_superiores, toques_inferiores, classificacao,
                    total_eventos, data_hora_referencia, atualizado_em
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    TIMEFRAME, item["regiao"], item["limite_inferior"],
                    item["limite_superior"], item["visitas"], item["toques"],
                    item["rejeicoes"], item["rompimentos"], item["prob_toque"],
                    item["prob_rejeicao"], item["prob_rompimento"],
                    item["wilson_rejeicao_inferior"], item["amostras"],
                    item["amostra_suficiente"], item["rejeicoes_superiores"],
                    item["rejeicoes_inferiores"], item["rompimentos_alta"],
                    item["rompimentos_baixa"], item["toques_superiores"],
                    item["toques_inferiores"], item["classificacao"],
                    item["total_eventos"], referencia, atualizado,
                ),
            )

        for item in transicoes:
            conexao.execute(
                """
                INSERT INTO estatisticas_transicoes (
                    timeframe, origem, destino, transicoes, saidas_origem,
                    probabilidade, visitas_origem, amostra_suficiente,
                    data_hora_referencia, atualizado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TIMEFRAME, item["origem"], item["destino"],
                    item["transicoes"], item["saidas_origem"],
                    item["probabilidade"], item["visitas_origem"],
                    item["amostra_suficiente"], referencia, atualizado,
                ),
            )
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise


def processar():
    print("PROCESSADOR ESTATISTICO M5 — BANCO UNICO")
    conexao = abrir_banco()
    try:
        validar_estrutura(conexao)
        velas = carregar_velas(conexao)
        eventos, descartados = carregar_eventos_bordas(conexao)
        regioes = criar_estatisticas_regioes(velas, eventos)
        transicoes = calcular_transicoes(velas)
        referencia = velas[-1]["timestamp"]
        gravar_resultados(conexao, regioes, transicoes, referencia)

        regioes_validas = sum(x["amostra_suficiente"] for x in regioes)
        transicoes_validas = sum(x["amostra_suficiente"] for x in transicoes)
        print("Velas M5 fechadas:", len(velas))
        print("Eventos de borda:", len(eventos))
        print("Eventos descartados:", descartados)
        print("Regioes consolidadas:", len(regioes))
        print("Transicoes consolidadas:", len(transicoes))
        print("Regioes com amostra suficiente:", regioes_validas)
        print("Transicoes com amostra suficiente:", transicoes_validas)
        print("Tabelas atualizadas: estatisticas_regioes e estatisticas_transicoes")
        print("Banco oficial:", BANCO)
        print("PROCESSAMENTO CONCLUIDO")
    finally:
        conexao.close()


if __name__ == "__main__":
    try:
        processar()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
