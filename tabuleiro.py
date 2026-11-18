#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tabuleiro probabilístico XAUUSD, integrado ao banco SQLite oficial."""

import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BANCO = BASE_DIR / "data" / "xauusd.db"
ESQUEMA = BASE_DIR / "database" / "esquema_xauusd.sql"

TIMEFRAMES = {"M5": ("m5", 5), "M15": ("m15", 15), "H1": ("h1", 60)}
TAMANHO_SEQUENCIA = 12
DISTANCIA_MAXIMA = 2
HORIZONTES = (1, 2, 3)
MINIMO_CANDLES = TAMANHO_SEQUENCIA * 2 + max(HORIZONTES)
VERSAO = "2.0"


def abrir_banco():
    if not BANCO.exists():
        raise FileNotFoundError(f"Banco oficial não encontrado: {BANCO}")
    if not ESQUEMA.exists():
        raise FileNotFoundError(f"Esquema oficial não encontrado: {ESQUEMA}")
    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 30000")
    return conexao


def verificar_estrutura(conexao):
    conexao.executescript(ESQUEMA.read_text(encoding="utf-8"))
    tabelas = {
        linha["name"] for linha in conexao.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    oficiais = {
        "m5", "m15", "h1", "indicadores",
        "tabuleiro", "sinais_validados", "sinais_negados", "ranking",
    }
    ausentes = oficiais - tabelas
    if ausentes:
        raise RuntimeError(
            "Banco oficial incompleto. Tabelas ausentes: "
            + ", ".join(sorted(ausentes))
        )
    colunas = {
        linha["name"] for linha in conexao.execute(
            "PRAGMA table_info(tabuleiro)"
        ).fetchall()
    }
    obrigatorias = {
        "id", "data_hora", "timeframe", "tipo_registro",
        "sequencia_json", "quantidade_candles",
        "ocorrencias_encontradas", "ocorrencias_exatas",
        "distancia_maxima", "horizonte", "evento_futuro",
        "ocorrencias", "total_amostras", "probabilidade",
        "distancia_media", "versao",
    }
    faltantes = obrigatorias - colunas
    if faltantes:
        raise RuntimeError(
            "Tabela tabuleiro incompatível com o esquema oficial. "
            "Colunas ausentes: " + ", ".join(sorted(faltantes))
        )


def carregar_candles(conexao, timeframe, tabela, minutos, limite):
    linhas = conexao.execute(
        f"""
        SELECT data_hora, abertura, maxima, minima, fechamento
        FROM {tabela}
        WHERE data_hora >= ?
        ORDER BY data_hora ASC
        """,
        (limite.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    agora = datetime.now(timezone.utc)
    resultado = []
    anterior = None

    for linha in linhas:
        texto_horario = linha["data_hora"]
        try:
            horario = datetime.strptime(
                texto_horario, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            valores = (
                float(linha["abertura"]),
                float(linha["maxima"]),
                float(linha["minima"]),
                float(linha["fechamento"]),
            )
        except (TypeError, ValueError) as erro:
            raise RuntimeError(
                f"Vela inválida em {timeframe}, intervalo {texto_horario}: {erro}"
            ) from erro

        if not all(math.isfinite(v) and v > 0 for v in valores):
            raise RuntimeError(
                f"OHLC inválido em {timeframe}, intervalo {texto_horario}."
            )

        abertura, maxima, minima, fechamento = valores
        if maxima < max(abertura, fechamento, minima) or minima > min(
            abertura, fechamento, maxima
        ):
            raise RuntimeError(
                f"Relação OHLC inválida em {timeframe}, intervalo {texto_horario}."
            )

        if anterior is not None and horario <= anterior:
            raise RuntimeError(
                f"Ordem temporal inválida em {timeframe}, intervalo {texto_horario}."
            )
        anterior = horario

        if horario + timedelta(minutes=minutos) > agora:
            continue

        resultado.append({
            "timestamp": texto_horario,
            "abertura": abertura,
            "maxima": maxima,
            "minima": minima,
            "fechamento": fechamento,
        })

    if len(resultado) < MINIMO_CANDLES:
        raise RuntimeError(
            f"{timeframe}: histórico insuficiente. Encontradas "
            f"{len(resultado)} velas fechadas; necessárias pelo menos "
            f"{MINIMO_CANDLES}."
        )
    return resultado


def mediana(valores):
    ordenados = sorted(valores)
    tamanho = len(ordenados)
    if not tamanho:
        return 0.0
    meio = tamanho // 2
    if tamanho % 2:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2.0


def classificar_evento(candle, anteriores):
    """Classifica a vela usando somente as 12 velas anteriores."""
    topo = max(c["maxima"] for c in anteriores)
    fundo = min(c["minima"] for c in anteriores)
    amplitudes = [
        max(0.0, c["maxima"] - c["minima"]) for c in anteriores
    ]
    tolerancia = max(mediana(amplitudes) * 0.08, 0.00001)

    rompeu_topo = candle["maxima"] > topo + tolerancia
    rompeu_fundo = candle["minima"] < fundo - tolerancia
    fechou_acima = candle["fechamento"] > topo + tolerancia
    fechou_abaixo = candle["fechamento"] < fundo - tolerancia

    if rompeu_topo and rompeu_fundo:
        excursao_topo = candle["maxima"] - topo
        excursao_fundo = fundo - candle["minima"]
        if excursao_topo >= excursao_fundo:
            rompeu_fundo = False
            fechou_abaixo = False
        else:
            rompeu_topo = False
            fechou_acima = False

    if fechou_acima:
        return "V+"
    if fechou_abaixo:
        return "V-"
    if rompeu_topo:
        return "R+"
    if rompeu_fundo:
        return "R-"

    tocou_topo = candle["maxima"] >= topo - tolerancia
    tocou_fundo = candle["minima"] <= fundo + tolerancia
    if tocou_topo and tocou_fundo:
        distancia_topo = abs(topo - candle["fechamento"])
        distancia_fundo = abs(candle["fechamento"] - fundo)
        return "T+" if distancia_topo <= distancia_fundo else "T-"
    if tocou_topo:
        return "T+"
    if tocou_fundo:
        return "T-"
    if candle["fechamento"] > candle["abertura"]:
        return "C+"
    if candle["fechamento"] < candle["abertura"]:
        return "C-"
    return "N"


def construir_eventos(candles):
    eventos = [None] * len(candles)
    for indice in range(TAMANHO_SEQUENCIA, len(candles)):
        anteriores = candles[indice - TAMANHO_SEQUENCIA:indice]
        eventos[indice] = classificar_evento(candles[indice], anteriores)
    return eventos


def distancia_sequencias(atual, historica):
    return sum(1 for a, b in zip(atual, historica) if a != b)


def encontrar_repeticoes(eventos):
    inicio_atual = len(eventos) - TAMANHO_SEQUENCIA
    atual = eventos[inicio_atual:]
    if len(atual) != TAMANHO_SEQUENCIA or any(e is None for e in atual):
        raise RuntimeError(
            "Não foi possível formar a sequência atual de 12 eventos."
        )

    limite_inicio = inicio_atual - TAMANHO_SEQUENCIA - max(HORIZONTES)
    correspondencias = []

    for inicio in range(0, limite_inicio + 1):
        trecho = eventos[inicio:inicio + TAMANHO_SEQUENCIA]
        if len(trecho) != TAMANHO_SEQUENCIA or any(e is None for e in trecho):
            continue

        distancia = distancia_sequencias(atual, trecho)
        if distancia > DISTANCIA_MAXIMA:
            continue

        futuros = {}
        valido = True
        for horizonte in HORIZONTES:
            indice_futuro = inicio + TAMANHO_SEQUENCIA + horizonte - 1
            if indice_futuro >= inicio_atual:
                valido = False
                break
            evento = eventos[indice_futuro]
            if evento is None:
                valido = False
                break
            futuros[horizonte] = evento

        if valido:
            correspondencias.append({
                "inicio": inicio,
                "distancia": distancia,
                "futuros": futuros,
            })

    return atual, correspondencias


def salvar_resultado(conexao, timeframe, timestamp, candles, sequencia, correspondencias):
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exatas = sum(1 for item in correspondencias if item["distancia"] == 0)

    # Substitui somente os registros anteriores deste timeframe.
    conexao.execute(
        "DELETE FROM tabuleiro WHERE timeframe = ?",
        (timeframe,),
    )

    conexao.execute(
        """
        INSERT INTO tabuleiro (
            data_hora, timeframe, tipo_registro, sequencia_json,
            quantidade_candles, ocorrencias_encontradas,
            ocorrencias_exatas, distancia_maxima, versao
        ) VALUES (?, ?, 'ESTADO', ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            timeframe,
            json.dumps(sequencia, ensure_ascii=False),
            len(candles),
            len(correspondencias),
            exatas,
            DISTANCIA_MAXIMA,
            VERSAO,
        ),
    )

    totais = {}
    distribuicoes = {}
    for horizonte in HORIZONTES:
        contagem = Counter(
            item["futuros"][horizonte] for item in correspondencias
        )
        total = sum(contagem.values())
        totais[horizonte] = total
        distribuicoes[horizonte] = contagem
        if total == 0:
            continue

        distancia_media = sum(
            item["distancia"] for item in correspondencias
        ) / len(correspondencias)

        registros = [
            (
                timestamp, timeframe, "PROBABILIDADE", None,
                None, None, None, DISTANCIA_MAXIMA,
                horizonte, evento, quantidade, total,
                quantidade / total, distancia_media, VERSAO,
            )
            for evento, quantidade in sorted(contagem.items())
        ]
        conexao.executemany(
            """
            INSERT INTO tabuleiro (
                data_hora, timeframe, tipo_registro, sequencia_json,
                quantidade_candles, ocorrencias_encontradas,
                ocorrencias_exatas, distancia_maxima, horizonte,
                evento_futuro, ocorrencias, total_amostras,
                probabilidade, distancia_media, versao
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

    return exatas, totais, distribuicoes


def processar_timeframe(conexao, timeframe, tabela, minutos, limite):
    candles = carregar_candles(
        conexao, timeframe, tabela, minutos, limite
    )
    eventos = construir_eventos(candles)
    sequencia, correspondencias = encontrar_repeticoes(eventos)
    timestamp = candles[-1]["timestamp"]

    exatas, totais, distribuicoes = salvar_resultado(
        conexao, timeframe, timestamp, candles,
        sequencia, correspondencias,
    )
    return {
        "candles": len(candles),
        "timestamp": timestamp,
        "sequencia": sequencia,
        "correspondencias": len(correspondencias),
        "exatas": exatas,
        "totais": totais,
        "distribuicoes": distribuicoes,
    }


def main():
    print("TABULEIRO PROBABILÍSTICO XAUUSD — M5 / M15 / H1")
    conexao = abrir_banco()
    try:
        verificar_estrutura(conexao)
        limite = datetime.now(timezone.utc) - timedelta(days=10)
        resumo = {}
        try:
            for timeframe, (tabela, minutos) in TIMEFRAMES.items():
                print(f"Analisando {timeframe}...")
                resumo[timeframe] = processar_timeframe(
                    conexao, timeframe, tabela, minutos, limite
                )

            corte = limite.strftime("%Y-%m-%d %H:%M:%S")
            conexao.execute(
                "DELETE FROM tabuleiro WHERE data_hora < ?",
                (corte,),
            )
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise

        for timeframe, dados in resumo.items():
            print(f"\n{timeframe} — referência {dados['timestamp']}")
            print("Sequência de 12: " + " ".join(dados["sequencia"]))
            print(
                f"Correspondências: {dados['correspondencias']} "
                f"| exatas: {dados['exatas']}"
            )
            if not dados["correspondencias"]:
                print("Nenhuma sequência histórica semelhante encontrada.")
                continue

            for horizonte in HORIZONTES:
                contagem = dados["distribuicoes"][horizonte]
                total = dados["totais"][horizonte]
                if not total:
                    print(f"Após {horizonte} vela(s): sem amostras")
                    continue
                partes = [
                    f"{evento}={quantidade / total * 100:.1f}% ({quantidade})"
                    for evento, quantidade in contagem.most_common()
                ]
                print(
                    f"Após {horizonte} vela(s): " + " | ".join(partes)
                )

        print(f"\nBanco oficial atualizado: {BANCO}")
        print(
            "As probabilidades representam frequências históricas; "
            "não garantem o próximo movimento."
        )
    finally:
        conexao.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
