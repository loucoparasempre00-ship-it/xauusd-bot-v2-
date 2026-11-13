#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
INDICADORES TÉCNICOS XAUUSD

Entrada:
    data/graficos.db.gz
    tabela candles

Saída:
    data/graficos.db.gz
    tabela indicadores_tecnicos

Indicadores:
    MME 9
    MME 21
    RSI 14
    MACD 12, 26, 9

Temporalidades:
    M5, M15 e H1

Somente candles fechados.
"""

import os
import sys
import gzip
import math
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timedelta, timezone


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BANCO_GZ = DATA_DIR / "graficos.db.gz"

TIMEFRAMES = {
    "M5": 5,
    "M15": 15,
    "H1": 60,
}

PERIODO_RSI = 14
PERIODO_MME_RAPIDA = 9
PERIODO_MME_LENTA = 21

MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SINAL = 9

VERSAO = "1.0"


def abrir_banco():
    if not BANCO_GZ.exists():
        raise FileNotFoundError(f"Banco não encontrado: {BANCO_GZ}")

    temporario = tempfile.TemporaryDirectory()
    caminho_db = Path(temporario.name) / "graficos.db"

    with gzip.open(BANCO_GZ, "rb") as origem:
        with open(caminho_db, "wb") as destino:
            while True:
                bloco = origem.read(1024 * 1024)
                if not bloco:
                    break
                destino.write(bloco)

    conexao = sqlite3.connect(caminho_db)
    conexao.row_factory = sqlite3.Row

    return temporario, caminho_db, conexao


def filtrar_candles_fechados(candles, minutos):
    agora = datetime.now(timezone.utc)
    resultado = []

    for candle in candles:
        horario = datetime.strptime(
            candle["timestamp"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        if horario + timedelta(minutes=minutos) <= agora:
            resultado.append(candle)

    return resultado


def calcular_ema(valores, periodo):
    resultado = [None] * len(valores)

    if len(valores) < periodo:
        return resultado

    multiplicador = 2.0 / (periodo + 1)

    inicial = sum(valores[:periodo]) / periodo
    resultado[periodo - 1] = inicial

    anterior = inicial

    for i in range(periodo, len(valores)):
        atual = (
            valores[i] * multiplicador
            + anterior * (1 - multiplicador)
        )

        resultado[i] = atual
        anterior = atual

    return resultado


def calcular_rsi(valores, periodo=14):
    resultado = [None] * len(valores)

    if len(valores) <= periodo:
        return resultado

    ganhos = []
    perdas = []

    for i in range(1, len(valores)):
        variacao = valores[i] - valores[i - 1]
        ganhos.append(max(variacao, 0.0))
        perdas.append(max(-variacao, 0.0))

    ganho_medio = sum(ganhos[:periodo]) / periodo
    perda_media = sum(perdas[:periodo]) / periodo

    def obter_rsi(ganho, perda):
        if perda == 0:
            return 100.0 if ganho > 0 else 50.0
        rs = ganho / perda
        return 100.0 - (100.0 / (1.0 + rs))

    resultado[periodo] = obter_rsi(
        ganho_medio, perda_media
    )

    for i in range(periodo + 1, len(valores)):
        indice = i - 1

        ganho_medio = (
            ganho_medio * (periodo - 1) + ganhos[indice]
        ) / periodo

        perda_media = (
            perda_media * (periodo - 1) + perdas[indice]
        ) / periodo

        resultado[i] = obter_rsi(
            ganho_medio, perda_media
        )

    return resultado


def calcular_macd(valores):
    ema12 = calcular_ema(valores, MACD_RAPIDA)
    ema26 = calcular_ema(valores, MACD_LENTA)

    linha_macd = [None] * len(valores)

    for i in range(len(valores)):
        if ema12[i] is not None and ema26[i] is not None:
            linha_macd[i] = ema12[i] - ema26[i]

    indices_validos = [
        i for i, valor in enumerate(linha_macd)
        if valor is not None
    ]

    valores_macd = [
        linha_macd[i] for i in indices_validos
    ]

    sinal_calculado = calcular_ema(
        valores_macd, MACD_SINAL
    )

    linha_sinal = [None] * len(valores)

    for posicao, indice in enumerate(indices_validos):
        linha_sinal[indice] = sinal_calculado[posicao]

    histograma = [None] * len(valores)

    for i in range(len(valores)):
        if linha_macd[i] is not None and linha_sinal[i] is not None:
            histograma[i] = linha_macd[i] - linha_sinal[i]

    return linha_macd, linha_sinal, histograma


def criar_tabela(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS indicadores_tecnicos (
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,

            mme_9 REAL,
            mme_21 REAL,
            rsi_14 REAL,

            macd REAL,
            macd_sinal REAL,
            macd_histograma REAL,

            versao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,

            PRIMARY KEY (timeframe, timestamp)
        )
    """)


def processar_timeframe(conexao, timeframe, minutos):
    candles = conexao.execute("""
        SELECT timestamp, fechamento
        FROM candles
        WHERE timeframe = ?
        ORDER BY timestamp ASC
    """, (timeframe,)).fetchall()

    candles = filtrar_candles_fechados(candles, minutos)

    if len(candles) < MACD_LENTA + MACD_SINAL:
        raise RuntimeError(
            f"Histórico insuficiente para indicadores em {timeframe}."
        )

    fechamentos = [
        float(candle["fechamento"]) for candle in candles
    ]

    if not all(math.isfinite(v) and v > 0 for v in fechamentos):
        raise RuntimeError(f"Fechamentos inválidos em {timeframe}.")

    mme9 = calcular_ema(fechamentos, PERIODO_MME_RAPIDA)
    mme21 = calcular_ema(fechamentos, PERIODO_MME_LENTA)
    rsi14 = calcular_rsi(fechamentos, PERIODO_RSI)

    macd, sinal, histograma = calcular_macd(fechamentos)

    agora = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    registros = []

    for i, candle in enumerate(candles):
        registros.append((
            timeframe,
            candle["timestamp"],
            mme9[i],
            mme21[i],
            rsi14[i],
            macd[i],
            sinal[i],
            histograma[i],
            VERSAO,
            agora,
        ))

    conexao.executemany("""
        INSERT INTO indicadores_tecnicos (
            timeframe, timestamp,
            mme_9, mme_21, rsi_14,
            macd, macd_sinal, macd_histograma,
            versao, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe, timestamp)
        DO UPDATE SET
            mme_9 = excluded.mme_9,
            mme_21 = excluded.mme_21,
            rsi_14 = excluded.rsi_14,
            macd = excluded.macd,
            macd_sinal = excluded.macd_sinal,
            macd_histograma = excluded.macd_histograma,
            versao = excluded.versao,
            atualizado_em = excluded.atualizado_em
    """, registros)

    return len(registros)


def compactar(caminho_db):
    temporario_gz = BANCO_GZ.with_name(BANCO_GZ.name + ".tmp")

    try:
        with open(caminho_db, "rb") as origem:
            with open(temporario_gz, "wb") as arquivo:
                with gzip.GzipFile(
                    fileobj=arquivo, mode="wb", mtime=0
                ) as destino:
                    while True:
                        bloco = origem.read(1024 * 1024)
                        if not bloco:
                            break
                        destino.write(bloco)

        os.replace(temporario_gz, BANCO_GZ)
    finally:
        if temporario_gz.exists():
            temporario_gz.unlink()


def main():
    temporario, caminho_db, conexao = abrir_banco()

    try:
        existe = conexao.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'candles'
        """).fetchone()

        if not existe:
            raise RuntimeError("Tabela candles não encontrada.")

        criar_tabela(conexao)

        resumo = {}

        try:
            for timeframe, minutos in TIMEFRAMES.items():
                resumo[timeframe] = processar_timeframe(
                    conexao, timeframe, minutos
                )

            conexao.commit()

        except Exception:
            conexao.rollback()
            raise

        finally:
            conexao.close()

        compactar(caminho_db)

        print("INDICADORES CALCULADOS E SALVOS")
        for timeframe, total in resumo.items():
            print(f"{timeframe}: {total} registros")

        print(f"Banco: {BANCO_GZ}")

    finally:
        temporario.cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
