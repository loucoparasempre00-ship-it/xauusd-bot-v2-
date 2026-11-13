
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
COLETOR E VALIDADOR DE VELAS XAUUSD M5

Responsabilidades:
1. Coletar velas XAU/USD no intervalo M5.
2. Validar timestamps e valores OHLC.
3. Eliminar registros inválidos e duplicados.
4. Salvar diretamente no banco oficial graficos.db.gz.
5. Atualizar velas existentes sem duplicar registros.

Não utiliza CSV como armazenamento definitivo.
Não cria outro banco de dados.
"""

import os
import sys
import json
import gzip
import sqlite3
import tempfile
import urllib.parse
import urllib.request

from pathlib import Path
from datetime import datetime, timezone


# ==========================================================
# CONFIGURAÇÕES
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

BANCO_GZ = DATA_DIR / "graficos.db.gz"

API_URL = "https://api.twelvedata.com/time_series"

SIMBOLO = "XAU/USD"
INTERVALO = "5min"
QUANTIDADE = 5000

TIMEOUT = 60


# ==========================================================
# COLETA
# ==========================================================

def coletar_velas():

    chave = os.getenv("TWELVE_DATA_API_KEY")

    if not chave:
        raise RuntimeError(
            "Secret TWELVE_DATA_API_KEY não encontrado."
        )

    parametros = {
        "symbol": SIMBOLO,
        "interval": INTERVALO,
        "outputsize": QUANTIDADE,
        "timezone": "UTC",
        "apikey": chave
    }

    url = API_URL + "?" + urllib.parse.urlencode(parametros)

    requisicao = urllib.request.Request(
        url,
        headers={"User-Agent": "XAUUSD-Coletor/1.0"}
    )

    with urllib.request.urlopen(
        requisicao,
        timeout=TIMEOUT
    ) as resposta:

        dados = json.loads(
            resposta.read().decode("utf-8")
        )

    if dados.get("status") == "error":
        raise RuntimeError(
            dados.get("message", "Erro na API Twelve Data.")
        )

    valores = dados.get("values")

    if not isinstance(valores, list) or not valores:
        raise RuntimeError(
            "A API não retornou velas."
        )

    return valores


# ==========================================================
# VALIDAÇÃO
# ==========================================================

def validar_vela(registro):

    try:
        horario = datetime.strptime(
            registro["datetime"],
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        abertura = float(registro["open"])
        maxima = float(registro["high"])
        minima = float(registro["low"])
        fechamento = float(registro["close"])

    except (KeyError, TypeError, ValueError):
        return None

    valores = [
        abertura,
        maxima,
        minima,
        fechamento
    ]

    # Impede valores inválidos.
    if not all(
        map(lambda x: x == x and abs(x) != float("inf"), valores)
    ):
        return None

    # Preços precisam ser positivos.
    if any(valor <= 0 for valor in valores):
        return None

    # Máxima e mínima precisam respeitar o OHLC.
    if maxima < max(abertura, fechamento, minima):
        return None

    if minima > min(abertura, fechamento, maxima):
        return None

    # Não aceitar velas futuras.
    agora = datetime.now(timezone.utc)

    if horario > agora:
        return None

    # Timestamp alinhado ao intervalo M5.
    if horario.minute % 5 != 0 or horario.second != 0:
        return None

    return (
        horario.strftime("%Y-%m-%d %H:%M:%S"),
        abertura,
        maxima,
        minima,
        fechamento
    )


def validar_lote(registros):

    validas = {}

    invalidas = 0

    for registro in registros:

        vela = validar_vela(registro)

        if vela is None:
            invalidas += 1
            continue

        # A chave impede duplicações dentro da própria coleta.
        validas[vela[0]] = vela

    resultado = sorted(
        validas.values(),
        key=lambda item: item[0]
    )

    return resultado, invalidas


# ==========================================================
# BANCO OFICIAL
# ==========================================================

def abrir_banco_temporario():

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temporario = tempfile.TemporaryDirectory()

    caminho_db = Path(temporario.name) / "graficos.db"

    if BANCO_GZ.exists():

        with gzip.open(BANCO_GZ, "rb") as origem:
            conteudo = origem.read()

        with open(caminho_db, "wb") as destino:
            destino.write(conteudo)

    return temporario, caminho_db


def salvar_no_banco(velas):

    temporario, caminho_db = abrir_banco_temporario()

    try:

        conexao = sqlite3.connect(caminho_db)

        try:

            conexao.execute("""
                CREATE TABLE IF NOT EXISTS candles_m5 (

                    timestamp TEXT PRIMARY KEY,

                    abertura REAL NOT NULL,
                    maxima REAL NOT NULL,
                    minima REAL NOT NULL,
                    fechamento REAL NOT NULL,

                    fonte TEXT NOT NULL,
                    atualizado_em TEXT NOT NULL
                )
            """)

            conexao.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_candles_m5_timestamp
                ON candles_m5(timestamp)
            """)

            agora = datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")

            antes = conexao.total_changes

            conexao.executemany("""
                INSERT INTO candles_m5 (
                    timestamp,
                    abertura,
                    maxima,
                    minima,
                    fechamento,
                    fonte,
                    atualizado_em
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(timestamp)
                DO UPDATE SET

                    abertura = excluded.abertura,
                    maxima = excluded.maxima,
                    minima = excluded.minima,
                    fechamento = excluded.fechamento,
                    fonte = excluded.fonte,
                    atualizado_em = excluded.atualizado_em

            """, [
                (
                    vela[0],
                    vela[1],
                    vela[2],
                    vela[3],
                    vela[4],
                    "Twelve Data",
                    agora
                )
                for vela in velas
            ])

            alteracoes = conexao.total_changes - antes

            conexao.commit()

            total = conexao.execute("""
                SELECT COUNT(*)
                FROM candles_m5
            """).fetchone()[0]

        finally:
            conexao.close()

        # Recompacta o mesmo banco oficial.
        arquivo_gz_temp = BANCO_GZ.with_suffix(".db.gz.tmp")

        with open(caminho_db, "rb") as origem:

            with open(arquivo_gz_temp, "wb") as arquivo:

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

        # Substituição atômica do banco oficial.
        os.replace(
            arquivo_gz_temp,
            BANCO_GZ
        )

        return alteracoes, total

    finally:
        temporario.cleanup()


# ==========================================================
# EXECUÇÃO
# ==========================================================

def main():

    print("INICIANDO COLETOR E VALIDADOR XAUUSD M5")

    registros = coletar_velas()

    velas, invalidas = validar_lote(registros)

    if not velas:
        raise RuntimeError(
            "Nenhuma vela válida encontrada. Banco não alterado."
        )

    alteracoes, total = salvar_no_banco(velas)

    print("------------------------------------")
    print(f"Velas recebidas: {len(registros)}")
    print(f"Velas válidas: {len(velas)}")
    print(f"Velas inválidas: {invalidas}")
    print(f"Registros inseridos ou atualizados: {alteracoes}")
    print(f"Total de velas no banco: {total}")
    print(f"Banco atualizado: {BANCO_GZ}")
    print("------------------------------------")

    print("COLETA E PERSISTÊNCIA CONCLUÍDAS")


if __name__ == "__main__":

    try:
        main()

    except Exception as erro:

        print(f"ERRO: {erro}", file=sys.stderr)

        sys.exit(1)
