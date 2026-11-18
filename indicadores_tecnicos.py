#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calcula EMA9, EMA21, RSI14, MACD e ATR14 no banco oficial XAUUSD."""

import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BANCO = BASE_DIR / "data" / "xauusd.db"
ESQUEMA = BASE_DIR / "database" / "esquema_xauusd.sql"

TIMEFRAMES = {"M5": ("m5", 5), "M15": ("m15", 15), "H1": ("h1", 60)}
PERIODO_RSI = 14
PERIODO_ATR = 14
PERIODO_EMA9 = 9
PERIODO_EMA21 = 21
MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SINAL = 9
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


def garantir_esquema(conexao):
    conexao.executescript(ESQUEMA.read_text(encoding="utf-8"))
    colunas = {
        linha["name"]
        for linha in conexao.execute("PRAGMA table_info(indicadores)").fetchall()
    }
    obrigatorias = {"timeframe", "data", "hora", "preco", "ema9", "ema21", "rsi", "macd", "atr"}
    faltantes = obrigatorias - colunas
    if faltantes:
        raise RuntimeError(
            "A tabela indicadores do banco não corresponde ao esquema oficial atualizado. "
            f"Colunas ausentes: {', '.join(sorted(faltantes))}. "
            "Nenhum dado foi alterado; é necessária a migração controlada do banco."
        )


def parse_data_hora(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as erro:
        raise RuntimeError(f"Data/hora inválida no banco de velas: {valor!r}") from erro


def candles_fechados(conexao, tabela, minutos, limite):
    agora = datetime.now(timezone.utc)
    linhas = conexao.execute(
        f"SELECT data_hora, abertura, maxima, minima, fechamento "
        f"FROM {tabela} WHERE data_hora >= ? ORDER BY data_hora ASC",
        (limite.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    resultado = []
    anterior = None
    for linha in linhas:
        horario = parse_data_hora(linha["data_hora"])
        if horario + timedelta(minutes=minutos) > agora:
            continue

        valores = [
            float(linha["abertura"]), float(linha["maxima"]),
            float(linha["minima"]), float(linha["fechamento"]),
        ]
        if not all(math.isfinite(v) and v > 0 for v in valores):
            raise RuntimeError(f"OHLC inválido em {tabela}: {linha['data_hora']}")
        abertura, maxima, minima, fechamento = valores
        if maxima < max(abertura, fechamento, minima) or minima > min(abertura, fechamento, maxima):
            raise RuntimeError(f"Relação OHLC inválida em {tabela}: {linha['data_hora']}")

        if anterior is not None and horario <= anterior:
            raise RuntimeError(f"Sequência temporal inválida em {tabela}: {linha['data_hora']}")
        resultado.append({
            "data_hora": linha["data_hora"],
            "horario": horario,
            "abertura": abertura,
            "maxima": maxima,
            "minima": minima,
            "fechamento": fechamento,
        })
        anterior = horario

    if len(resultado) < MACD_LENTA + MACD_SINAL:
        raise RuntimeError(
            f"Histórico insuficiente em {tabela}: {len(resultado)} velas fechadas; "
            f"são necessárias pelo menos {MACD_LENTA + MACD_SINAL}."
        )
    return resultado


def calcular_ema(valores, periodo):
    saida = [None] * len(valores)
    if len(valores) < periodo:
        return saida
    fator = 2.0 / (periodo + 1)
    atual = sum(valores[:periodo]) / periodo
    saida[periodo - 1] = atual
    for i in range(periodo, len(valores)):
        atual = valores[i] * fator + atual * (1.0 - fator)
        saida[i] = atual
    return saida


def calcular_rsi(fechamentos, periodo=PERIODO_RSI):
    saida = [None] * len(fechamentos)
    if len(fechamentos) <= periodo:
        return saida
    variacoes = [fechamentos[i] - fechamentos[i - 1] for i in range(1, len(fechamentos))]
    ganho = sum(max(v, 0.0) for v in variacoes[:periodo]) / periodo
    perda = sum(max(-v, 0.0) for v in variacoes[:periodo]) / periodo

    def valor_rsi(g, p):
        if p == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / p)

    saida[periodo] = valor_rsi(ganho, perda)
    for i in range(periodo + 1, len(fechamentos)):
        variacao = variacoes[i - 1]
        ganho = (ganho * (periodo - 1) + max(variacao, 0.0)) / periodo
        perda = (perda * (periodo - 1) + max(-variacao, 0.0)) / periodo
        saida[i] = valor_rsi(ganho, perda)
    return saida


def calcular_atr(candles, periodo=PERIODO_ATR):
    saida = [None] * len(candles)
    if len(candles) <= periodo:
        return saida
    ranges = []
    for i, candle in enumerate(candles):
        if i == 0:
            tr = candle["maxima"] - candle["minima"]
        else:
            fechamento_anterior = candles[i - 1]["fechamento"]
            tr = max(
                candle["maxima"] - candle["minima"],
                abs(candle["maxima"] - fechamento_anterior),
                abs(candle["minima"] - fechamento_anterior),
            )
        ranges.append(tr)

    atual = sum(ranges[1:periodo + 1]) / periodo
    saida[periodo] = atual
    for i in range(periodo + 1, len(candles)):
        atual = (atual * (periodo - 1) + ranges[i]) / periodo
        saida[i] = atual
    return saida


def calcular_macd(fechamentos):
    ema_rapida = calcular_ema(fechamentos, MACD_RAPIDA)
    ema_lenta = calcular_ema(fechamentos, MACD_LENTA)
    linha = [None] * len(fechamentos)
    indices = []
    valores = []
    for i in range(len(fechamentos)):
        if ema_rapida[i] is not None and ema_lenta[i] is not None:
            linha[i] = ema_rapida[i] - ema_lenta[i]
            indices.append(i)
            valores.append(linha[i])
    sinal_compacto = calcular_ema(valores, MACD_SINAL)
    sinal = [None] * len(fechamentos)
    for posicao, indice in enumerate(indices):
        sinal[indice] = sinal_compacto[posicao]
    return linha


def processar_timeframe(conexao, timeframe, tabela, minutos, limite, agora):
    candles = candles_fechados(conexao, tabela, minutos, limite)
    fechamentos = [c["fechamento"] for c in candles]
    ema9 = calcular_ema(fechamentos, PERIODO_EMA9)
    ema21 = calcular_ema(fechamentos, PERIODO_EMA21)
    rsi = calcular_rsi(fechamentos)
    atr = calcular_atr(candles)

    ema12 = calcular_ema(fechamentos, MACD_RAPIDA)
    ema26 = calcular_ema(fechamentos, MACD_LENTA)
    macd = [
        (ema12[i] - ema26[i]) if ema12[i] is not None and ema26[i] is not None else None
        for i in range(len(candles))
    ]

    indices_macd = [i for i, valor in enumerate(macd) if valor is not None]
    valores_macd = [macd[i] for i in indices_macd]
    sinal_compacto = calcular_ema(valores_macd, MACD_SINAL)
    sinal = [None] * len(candles)
    for posicao, indice in enumerate(indices_macd):
        sinal[indice] = sinal_compacto[posicao]

    registros = []
    for i, candle in enumerate(candles):
        registros.append((
            timeframe,
            candle["horario"].strftime("%Y-%m-%d"),
            candle["horario"].strftime("%H:%M:%S"),
            candle["fechamento"],
            ema9[i],
            ema21[i],
            rsi[i],
            macd[i],
            atr[i],
        ))

    conexao.executemany(
        """
        INSERT INTO indicadores (
            timeframe, data, hora, preco, ema9, ema21, rsi, macd, atr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe, data, hora) DO UPDATE SET
            preco=excluded.preco,
            ema9=excluded.ema9,
            ema21=excluded.ema21,
            rsi=excluded.rsi,
            macd=excluded.macd,
            atr=excluded.atr
        """,
        registros,
    )
    return len(registros), candles[-1]["data_hora"]


def main():
    conexao = abrir_banco()
    try:
        garantir_esquema(conexao)
        limite = datetime.now(timezone.utc) - timedelta(days=10)
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        resumo = {}
        try:
            for timeframe, (tabela, minutos) in TIMEFRAMES.items():
                resumo[timeframe] = processar_timeframe(
                    conexao, timeframe, tabela, minutos, limite, agora
                )

            corte = limite.strftime("%Y-%m-%d %H:%M:%S")
            conexao.execute(
                "DELETE FROM indicadores WHERE (data || ' ' || hora) < ?",
                (corte,),
            )
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise

        print("INDICADORES TÉCNICOS — BANCO OFICIAL XAUUSD")
        for timeframe, (quantidade, ultima) in resumo.items():
            print(f"{timeframe}: registros={quantidade} | última vela fechada={ultima}")
        print("Indicadores: EMA9, EMA21, RSI14, MACD e ATR14.")
        print("Retenção aplicada: 10 dias.")
        print(f"Banco: {BANCO}")
    finally:
        conexao.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
