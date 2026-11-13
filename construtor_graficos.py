
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CONSTRUTOR DE GRÁFICOS XAUUSD M5

Entrada:
    data/graficos.db.gz
    tabela candles_m5

Saída:
    data/graficos.db.gz
    tabela estrutura_grafica_m5

Responsabilidade:
    Transformar candles validados em registros gráficos
    organizados por sequência temporal e preço.

Não coleta candles.
Não valida novamente a fonte.
Não cria CSV.
Não cria outro banco.
"""

import gzip
import json
import math
import os
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timezone


# ==========================================================
# CONFIGURAÇÕES
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

BANCO_GZ = DATA_DIR / "graficos.db.gz"

VERSAO_CONSTRUTOR = "1.0"


# ==========================================================
# ABERTURA DO BANCO OFICIAL
# ==========================================================

def abrir_banco():

    if not BANCO_GZ.exists():
        raise FileNotFoundError(
            f"Banco oficial não encontrado: {BANCO_GZ}"
        )

    temporario = tempfile.TemporaryDirectory()

    caminho_db = Path(temporario.name) / "graficos.db"

    with gzip.open(BANCO_GZ, "rb") as origem:
        with open(caminho_db, "wb") as destino:
            destino.write(origem.read())

    conexao = sqlite3.connect(caminho_db)

    conexao.row_factory = sqlite3.Row

    return temporario, caminho_db, conexao


# ==========================================================
# LEITURA DOS CANDLES
# ==========================================================

def carregar_candles(conexao):

    tabelas = {
        linha["name"]
        for linha in conexao.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
        """)
    }

    if "candles_m5" not in tabelas:
        raise RuntimeError(
            "Tabela candles_m5 não encontrada no banco."
        )

    candles = conexao.execute("""
        SELECT
            timestamp,
            abertura,
            maxima,
            minima,
            fechamento
        FROM candles_m5
        ORDER BY timestamp ASC
    """).fetchall()

    if len(candles) < 2:
        raise RuntimeError(
            "Quantidade insuficiente de candles para construir o gráfico."
        )

    return candles


# ==========================================================
# CONSTRUÇÃO DA ESTRUTURA GRÁFICA
# ==========================================================

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

        if preco_anterior is None:
            variacao = 0.0
        else:
            variacao = fechamento - preco_anterior

        if variacao > 0:
            movimento = "ASCENDENTE"
        elif variacao < 0:
            movimento = "DESCENDENTE"
        else:
            movimento = "LATERAL"

        proporcao_corpo = (
            corpo / amplitude
            if amplitude > 0
            else 0.0
        )

        registro = {
            "timestamp": candle["timestamp"],
            "eixo_x": indice,
            "eixo_y_abertura": abertura,
            "eixo_y_maxima": maxima,
            "eixo_y_minima": minima,
            "eixo_y_fechamento": fechamento,
            "amplitude": amplitude,
            "corpo": corpo,
            "proporcao_corpo": proporcao_corpo,
            "direcao": direcao,
            "movimento": movimento,
            "variacao": variacao
        }

        registros.append(registro)

        preco_anterior = fechamento

    return registros


# ==========================================================
# PERSISTÊNCIA NO BANCO OFICIAL
# ==========================================================

def salvar_estrutura(conexao, registros):

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS estrutura_grafica_m5 (

            timestamp TEXT PRIMARY KEY,

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

            versao_construtor TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
    """)

    agora = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    conexao.executemany("""
        INSERT INTO estrutura_grafica_m5 (
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
            versao_construtor,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(timestamp)
        DO UPDATE SET

            eixo_x = excluded.eixo_x,
            eixo_y_abertura = excluded.eixo_y_abertura,
            eixo_y_maxima = excluded.eixo_y_maxima,
            eixo_y_minima = excluded.eixo_y_minima,
            eixo_y_fechamento = excluded.eixo_y_fechamento,
            amplitude = excluded.amplitude,
            corpo = excluded.corpo,
            proporcao_corpo = excluded.proporcao_corpo,
            direcao = excluded.direcao,
            movimento = excluded.movimento,
            variacao = excluded.variacao,
            versao_construtor = excluded.versao_construtor,
            atualizado_em = excluded.atualizado_em

    """, [
        (
            r["timestamp"],
            r["eixo_x"],
            r["eixo_y_abertura"],
            r["eixo_y_maxima"],
            r["eixo_y_minima"],
            r["eixo_y_fechamento"],
            r["amplitude"],
            r["corpo"],
            r["proporcao_corpo"],
            r["direcao"],
            r["movimento"],
            r["variacao"],
            VERSAO_CONSTRUTOR,
            agora
        )
        for r in registros
    ])


# ==========================================================
# COMPACTAÇÃO DO BANCO
# ==========================================================

def compactar_banco(caminho_db):

    temporario_gz = BANCO_GZ.with_name(
        BANCO_GZ.name + ".tmp"
    )

    with open(caminho_db, "rb") as origem:

        with open(temporario_gz, "wb") as arquivo:

            with gzip.GzipFile(
                fileobj=arquivo,
                mode="wb",
                mtime=0
            ) as destino:

                while True:

                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

    os.replace(
        temporario_gz,
        BANCO_GZ
    )


# ==========================================================
# EXECUÇÃO
# ==========================================================

def main():

    print("CONSTRUTOR DE GRÁFICOS XAUUSD M5")

    temporario, caminho_db, conexao = abrir_banco()

    try:

        candles = carregar_candles(conexao)

        registros = construir_estrutura(candles)

        conexao.execute("BEGIN")

        salvar_estrutura(conexao, registros)

        conexao.commit()

        total = conexao.execute("""
            SELECT COUNT(*)
            FROM estrutura_grafica_m5
        """).fetchone()[0]

        conexao.close()

        compactar_banco(caminho_db)

        print("--------------------------------")
        print(f"Candles lidos: {len(candles)}")
        print(f"Registros gráficos: {len(registros)}")
        print(f"Total no banco: {total}")
        print(f"Banco atualizado: {BANCO_GZ}")
        print("--------------------------------")

    except Exception:

        conexao.rollback()
        conexao.close()
        raise

    finally:
        temporario.cleanup()


if __name__ == "__main__":
    main()
