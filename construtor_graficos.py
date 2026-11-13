
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Constrói estruturas gráficas XAUUSD M5, M15 e H1.
Entrada e saída: data/graficos.db.gz
"""

import os
import sys
import gzip
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timezone


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BANCO_GZ = DATA_DIR / "graficos.db.gz"

TIMEFRAMES = ("M5", "M15", "H1")
VERSAO = "2.0"


def abrir_banco_temporario():
    if not BANCO_GZ.exists():
        raise FileNotFoundError(
            f"Banco oficial não encontrado: {BANCO_GZ}"
        )

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


def verificar_entrada(conexao):
    existe = conexao.execute("""
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'candles'
    """).fetchone()

    if not existe:
        raise RuntimeError(
            "Tabela candles não encontrada. "
            "Execute primeiro o coletor_validador_velas.py."
        )


def criar_tabela_grafica(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS estrutura_grafica (
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            eixo_x INTEGER NOT NULL,

            eixo_y_abertura REAL NOT NULL,
            eixo_y_maxima REAL NOT NULL,
            eixo_y_minima REAL NOT NULL,
            eixo_y_fechamento REAL NOT NULL,

            amplitude REAL NOT NULL,
            corpo REAL NOT NULL,
            proporcao_corpo REAL NOT NULL,

            direcao TEXT NOT NULL,
            movimento TEXT NOT NULL,
            variacao REAL NOT NULL,

            versao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,

            PRIMARY KEY (timeframe, timestamp)
        )
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_estrutura_grafica_x
        ON estrutura_grafica(timeframe, eixo_x)
    """)


def carregar_candles(conexao, timeframe):
    return conexao.execute("""
        SELECT
            timestamp,
            abertura,
            maxima,
            minima,
            fechamento
        FROM candles
        WHERE timeframe = ?
        ORDER BY timestamp ASC
    """, (timeframe,)).fetchall()


def construir_estrutura(candles):
    registros = []
    preco_anterior = None

    for indice, candle in enumerate(candles):
        abertura = float(candle["abertura"])
        maxima = float(candle["maxima"])
        minima = float(candle["minima"])
        fechamento = float(candle["fechamento"])

        amplitude = maxima - minima
        corpo = abs(fechamento - abertura)

        if fechamento > abertura:
            direcao = "ALTA"
        elif fechamento < abertura:
            direcao = "BAIXA"
        else:
            direcao = "NEUTRA"

        variacao = (
            0.0 if preco_anterior is None
            else fechamento - preco_anterior
        )

        if variacao > 0:
            movimento = "ASCENDENTE"
        elif variacao < 0:
            movimento = "DESCENDENTE"
        else:
            movimento = "LATERAL"

        proporcao_corpo = corpo / amplitude if amplitude else 0.0

        registros.append((
            candle["timestamp"],
            indice,
            abertura,
            maxima,
            minima,
            fechamento,
            amplitude,
            corpo,
            proporcao_corpo,
            direcao,
            movimento,
            variacao,
        ))

        preco_anterior = fechamento

    return registros


def salvar_estrutura(conexao, timeframe, registros):
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Recalcula a sequência inteira para manter o eixo X coerente.
    conexao.execute(
        "DELETE FROM estrutura_grafica WHERE timeframe = ?",
        (timeframe,),
    )

    conexao.executemany("""
        INSERT INTO estrutura_grafica (
            timeframe,
            timestamp,
            eixo_x,
            eixo_y_abertura,
            eixo_y_maxima,
            eixo_y_minima,
            eixo_y_fechamento,
            amplitude,
            corpo,
            proporcao_corpo,
            direcao,
            movimento,
            variacao,
            versao,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            timeframe,
            registro[0],
            registro[1],
            registro[2],
            registro[3],
            registro[4],
            registro[5],
            registro[6],
            registro[7],
            registro[8],
            registro[9],
            registro[10],
            registro[11],
            VERSAO,
            agora,
        )
        for registro in registros
    ])


def compactar_banco(caminho_db):
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
    print("CONSTRUTOR DE GRÁFICOS — M5 / M15 / H1")

    temporario, caminho_db, conexao = abrir_banco_temporario()

    try:
        verificar_entrada(conexao)
        criar_tabela_grafica(conexao)

        resumo = {}

        try:
            for timeframe in TIMEFRAMES:
                candles = carregar_candles(conexao, timeframe)

                if not candles:
                    raise RuntimeError(
                        f"Não existem candles {timeframe} no banco."
                    )

                registros = construir_estrutura(candles)
                salvar_estrutura(conexao, timeframe, registros)

                resumo[timeframe] = len(registros)

            conexao.commit()

        except Exception:
            conexao.rollback()
            raise

        finally:
            conexao.close()

        compactar_banco(caminho_db)

        for timeframe, total in resumo.items():
            print(f"{timeframe}: {total} estruturas gráficas")

        print(f"Banco atualizado: {BANCO_GZ}")

    finally:
        temporario.cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
