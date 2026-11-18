#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Indicadores técnicos XAUUSD: MME 9/21, RSI 14 e MACD 12/26/9."""

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
TIMEFRAMES = {"M5": 5, "M15": 15, "H1": 60}
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
    with gzip.open(BANCO_GZ, "rb") as origem, open(caminho_db, "wb") as destino:
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
        horario = datetime.strptime(candle["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
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
        atual = valores[i] * multiplicador + anterior * (1 - multiplicador)
        resultado[i] = atual
        anterior = atual
    return resultado


def calcular_rsi(valores, periodo=14):
    resultado = [None] * len(valores)
    if len(valores) <= periodo:
        return resultado
    ganhos, perdas = [], []
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

    resultado[periodo] = obter_rsi(ganho_medio, perda_media)
    for i in range(periodo + 1, len(valores)):
        indice = i - 1
        ganho_medio = (ganho_medio * (periodo - 1) + ganhos[indice]) / periodo
        perda_media = (perda_media * (periodo - 1) + perdas[indice]) / periodo
        resultado[i] = obter_rsi(ganho_medio, perda_media)
    return resultado


def calcular_macd(valores):
    ema12 = calcular_ema(valores, MACD_RAPIDA)
    ema26 = calcular_ema(valores, MACD_LENTA)
    linha_macd = [None] * len(valores)
    for i in range(len(valores)):
        if ema12[i] is not None and ema26[i] is not None:
            linha_macd[i] = ema12[i] - ema26[i]
    indices_validos = [i for i, valor in enumerate(linha_macd) if valor is not None]
    valores_macd = [linha_macd[i] for i in indices_validos]
    sinal_calculado = calcular_ema(valores_macd, MACD_SINAL)
    linha_sinal = [None] * len(valores)
    for posicao, indice in enumerate(indices_validos):
        linha_sinal[indice] = sinal_calculado[posicao]
    histograma = [None] * len(valores)
    for i in range(len(valores)):
        if linha_macd[i] is not None and linha_sinal[i] is not None:
            histograma[i] = linha_macd[i] - linha_sinal[i]
    return linha_macd, linha_sinal, histograma


def criar_tabelas(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS indicadores_tecnicos (
            timeframe TEXT NOT NULL, timestamp TEXT NOT NULL,
            mme_9 REAL, mme_21 REAL, rsi_14 REAL,
            macd REAL, macd_sinal REAL, macd_histograma REAL,
            versao TEXT NOT NULL, atualizado_em TEXT NOT NULL,
            PRIMARY KEY (timeframe, timestamp)
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS estado_indicadores (
            timeframe TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            quantidade_candles INTEGER NOT NULL, fechamento_anterior REAL NOT NULL,
            mme_9 REAL NOT NULL, mme_21 REAL NOT NULL,
            ema_12 REAL NOT NULL, ema_26 REAL NOT NULL,
            ganho_medio REAL NOT NULL, perda_media REAL NOT NULL,
            macd_sinal REAL NOT NULL, versao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
    """)


def obter_estado(conexao, timeframe):
    return conexao.execute("SELECT * FROM estado_indicadores WHERE timeframe = ?", (timeframe,)).fetchone()


def salvar_estado(conexao, timeframe, timestamp, quantidade, fechamento,
                  mme9, mme21, ema12, ema26, ganho, perda, sinal, agora):
    conexao.execute("""
        INSERT INTO estado_indicadores (
            timeframe, timestamp, quantidade_candles, fechamento_anterior,
            mme_9, mme_21, ema_12, ema_26, ganho_medio, perda_media,
            macd_sinal, versao, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe) DO UPDATE SET
            timestamp=excluded.timestamp,
            quantidade_candles=excluded.quantidade_candles,
            fechamento_anterior=excluded.fechamento_anterior,
            mme_9=excluded.mme_9, mme_21=excluded.mme_21,
            ema_12=excluded.ema_12, ema_26=excluded.ema_26,
            ganho_medio=excluded.ganho_medio, perda_media=excluded.perda_media,
            macd_sinal=excluded.macd_sinal, versao=excluded.versao,
            atualizado_em=excluded.atualizado_em
    """, (timeframe, timestamp, quantidade, fechamento, mme9, mme21,
          ema12, ema26, ganho, perda, sinal, VERSAO, agora))


def obter_fechamentos_validos(conexao, timeframe, minutos):
    candles = conexao.execute("""
        SELECT timestamp, fechamento FROM candles
        WHERE timeframe = ? ORDER BY timestamp ASC
    """, (timeframe,)).fetchall()
    candles = filtrar_candles_fechados(candles, minutos)
    fechamentos = [float(c["fechamento"]) for c in candles]
    if not all(math.isfinite(v) and v > 0 for v in fechamentos):
        raise RuntimeError(f"Fechamentos inválidos em {timeframe}.")
    return candles, fechamentos


def calcular_estado_completo(fechamentos):
    mme9 = calcular_ema(fechamentos, PERIODO_MME_RAPIDA)
    mme21 = calcular_ema(fechamentos, PERIODO_MME_LENTA)
    rsi14 = calcular_rsi(fechamentos, PERIODO_RSI)
    macd, sinal, histograma = calcular_macd(fechamentos)
    if len(fechamentos) < MACD_LENTA + MACD_SINAL:
        raise RuntimeError("Histórico insuficiente para inicializar MACD.")
    ganhos, perdas = [], []
    for i in range(1, len(fechamentos)):
        variacao = fechamentos[i] - fechamentos[i - 1]
        ganhos.append(max(variacao, 0.0))
        perdas.append(max(-variacao, 0.0))
    ganho_medio = sum(ganhos[:PERIODO_RSI]) / PERIODO_RSI
    perda_media = sum(perdas[:PERIODO_RSI]) / PERIODO_RSI
    for indice in range(PERIODO_RSI, len(ganhos)):
        ganho_medio = (ganho_medio * (PERIODO_RSI - 1) + ganhos[indice]) / PERIODO_RSI
        perda_media = (perda_media * (PERIODO_RSI - 1) + perdas[indice]) / PERIODO_RSI
    return (
        mme9, mme21, rsi14, macd, sinal, histograma,
        mme9[-1], mme21[-1],
        calcular_ema(fechamentos, MACD_RAPIDA)[-1],
        calcular_ema(fechamentos, MACD_LENTA)[-1],
        ganho_medio, perda_media, sinal[-1]
    )


def gravar_registros(conexao, registros):
    if not registros:
        return
    conexao.executemany("""
        INSERT INTO indicadores_tecnicos (
            timeframe, timestamp, mme_9, mme_21, rsi_14,
            macd, macd_sinal, macd_histograma, versao, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe, timestamp) DO UPDATE SET
            mme_9=excluded.mme_9, mme_21=excluded.mme_21,
            rsi_14=excluded.rsi_14, macd=excluded.macd,
            macd_sinal=excluded.macd_sinal,
            macd_histograma=excluded.macd_histograma,
            versao=excluded.versao, atualizado_em=excluded.atualizado_em
    """, registros)


def reconstruir_timeframe(conexao, timeframe, candles, fechamentos, agora):
    (mme9, mme21, rsi14, macd, sinal, histograma, estado_mme9,
     estado_mme21, ema12, ema26, ganho, perda, estado_sinal) = calcular_estado_completo(fechamentos)
    registros = [
        (timeframe, c["timestamp"], mme9[i], mme21[i], rsi14[i],
         macd[i], sinal[i], histograma[i], VERSAO, agora)
        for i, c in enumerate(candles)
    ]
    gravar_registros(conexao, registros)
    salvar_estado(conexao, timeframe, candles[-1]["timestamp"], len(candles),
                  fechamentos[-1], estado_mme9, estado_mme21, ema12, ema26,
                  ganho, perda, estado_sinal, agora)
    return len(registros), len(registros), "reconstruido"


def processar_incremental(conexao, timeframe, candles_novas, estado, agora):
    anterior = float(estado["fechamento_anterior"])
    mme9, mme21 = float(estado["mme_9"]), float(estado["mme_21"])
    ema12, ema26 = float(estado["ema_12"]), float(estado["ema_26"])
    ganho, perda = float(estado["ganho_medio"]), float(estado["perda_media"])
    sinal = float(estado["macd_sinal"])
    quantidade = int(estado["quantidade_candles"])
    k9, k21 = 2.0 / 10, 2.0 / 22
    k12, k26, ks = 2.0 / 13, 2.0 / 27, 2.0 / 10
    registros = []
    for candle in candles_novas:
        fechamento = float(candle["fechamento"])
        variacao = fechamento - anterior
        mme9 = fechamento * k9 + mme9 * (1 - k9)
        mme21 = fechamento * k21 + mme21 * (1 - k21)
        ema12 = fechamento * k12 + ema12 * (1 - k12)
        ema26 = fechamento * k26 + ema26 * (1 - k26)
        ganho = (ganho * 13 + max(variacao, 0.0)) / 14
        perda = (perda * 13 + max(-variacao, 0.0)) / 14
        if perda == 0:
            rsi = 100.0 if ganho > 0 else 50.0
        else:
            rs = ganho / perda
            rsi = 100.0 - (100.0 / (1.0 + rs))
        linha_macd = ema12 - ema26
        sinal = linha_macd * ks + sinal * (1 - ks)
        histograma = linha_macd - sinal
        registros.append((timeframe, candle["timestamp"], mme9, mme21, rsi,
                          linha_macd, sinal, histograma, VERSAO, agora))
        anterior = fechamento
        quantidade += 1
    gravar_registros(conexao, registros)
    if candles_novas:
        salvar_estado(conexao, timeframe, candles_novas[-1]["timestamp"],
                      quantidade, anterior, mme9, mme21, ema12, ema26,
                      ganho, perda, sinal, agora)
    return len(registros), quantidade, "incremental"


def processar_timeframe(conexao, timeframe, minutos):
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    estado = obter_estado(conexao, timeframe)
    if estado is None or estado["versao"] != VERSAO:
        candles, fechamentos = obter_fechamentos_validos(conexao, timeframe, minutos)
        if len(candles) < MACD_LENTA + MACD_SINAL:
            raise RuntimeError(f"Histórico insuficiente para indicadores em {timeframe}.")
        return reconstruir_timeframe(conexao, timeframe, candles, fechamentos, agora)

    timestamp_estado = estado["timestamp"]
    contagem = conexao.execute("""
        SELECT COUNT(*) AS total, MAX(timestamp) AS ultimo FROM candles
        WHERE timeframe = ? AND timestamp <= ?
    """, (timeframe, timestamp_estado)).fetchone()
    total_indicadores = conexao.execute("""
        SELECT COUNT(*) AS total FROM indicadores_tecnicos
        WHERE timeframe = ? AND timestamp <= ?
    """, (timeframe, timestamp_estado)).fetchone()
    integridade = (
        int(estado["quantidade_candles"]) == int(contagem["total"])
        and contagem["ultimo"] == timestamp_estado
        and int(total_indicadores["total"]) == int(estado["quantidade_candles"])
    )
    if not integridade:
        candles, fechamentos = obter_fechamentos_validos(conexao, timeframe, minutos)
        if len(candles) < MACD_LENTA + MACD_SINAL:
            raise RuntimeError(f"Histórico insuficiente para indicadores em {timeframe}.")
        return reconstruir_timeframe(conexao, timeframe, candles, fechamentos, agora)

    novas = conexao.execute("""
        SELECT timestamp, fechamento FROM candles
        WHERE timeframe = ? AND timestamp > ? ORDER BY timestamp ASC
    """, (timeframe, timestamp_estado)).fetchall()
    novas = filtrar_candles_fechados(novas, minutos)
    for candle in novas:
        fechamento = float(candle["fechamento"])
        if not math.isfinite(fechamento) or fechamento <= 0:
            raise RuntimeError(f"Fechamento inválido em {timeframe}: {candle['timestamp']}.")
    return processar_incremental(conexao, timeframe, novas, estado, agora)


def compactar(caminho_db):
    temporario_gz = BANCO_GZ.with_name(BANCO_GZ.name + ".tmp")
    try:
        with open(caminho_db, "rb") as origem, open(temporario_gz, "wb") as arquivo:
            with gzip.GzipFile(fileobj=arquivo, mode="wb", mtime=0) as destino:
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
            SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'candles'
        """).fetchone()
        if not existe:
            raise RuntimeError("Tabela candles não encontrada.")
        criar_tabelas(conexao)
        resumo = {}
        try:
            for timeframe, minutos in TIMEFRAMES.items():
                resumo[timeframe] = processar_timeframe(conexao, timeframe, minutos)
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise
        finally:
            conexao.close()
        compactar(caminho_db)
        print("INDICADORES TÉCNICOS — PROCESSAMENTO INCREMENTAL")
        for timeframe, (novas, total, modo) in resumo.items():
            print(f"{timeframe}: novas={novas} | total={total} | modo={modo}")
        print(f"Banco: {BANCO_GZ}")
    finally:
        temporario.cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
