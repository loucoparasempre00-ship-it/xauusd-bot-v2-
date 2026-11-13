
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Coleta e valida candles XAU/USD M5, M15 e H1.
Persistência definitiva: data/graficos.db.gz
"""

import os
import sys
import gzip
import math
import json
import sqlite3
import tempfile
import urllib.parse
import urllib.request

from pathlib import Path
from datetime import datetime, timedelta, timezone


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BANCO_GZ = DATA_DIR / "graficos.db.gz"

API_URL = "https://api.twelvedata.com/time_series"
SIMBOLO = "XAU/USD"
TIMEFRAMES = {
    "M5": ("5min", 5),
    "M15": ("15min", 15),
    "H1": ("1h", 60),
}
QUANTIDADE = 5000
TIMEOUT = 60


def abrir_banco_temporario():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporario = tempfile.TemporaryDirectory()
    caminho_db = Path(temporario.name) / "graficos.db"

    if BANCO_GZ.exists():
        with gzip.open(BANCO_GZ, "rb") as origem:
            with open(caminho_db, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)
                    if not bloco:
                        break
                    destino.write(bloco)

    return temporario, caminho_db


def coletar_timeframe(intervalo):
    chave = os.getenv("TWELVE_DATA_API_KEY")
    if not chave:
        raise RuntimeError("Secret TWELVE_DATA_API_KEY não encontrado.")

    parametros = {
        "symbol": SIMBOLO,
        "interval": intervalo,
        "outputsize": QUANTIDADE,
        "timezone": "UTC",
        "apikey": chave,
    }

    url = API_URL + "?" + urllib.parse.urlencode(parametros)
    requisicao = urllib.request.Request(
        url,
        headers={"User-Agent": "XAUUSD-Coletor/2.0"},
    )

    with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))

    if dados.get("status") == "error" or dados.get("code"):
        raise RuntimeError(
            f"Twelve Data ({intervalo}): "
            f"{dados.get('message', dados)}"
        )

    valores = dados.get("values")
    if not isinstance(valores, list) or not valores:
        raise RuntimeError(f"Nenhuma vela retornada para {intervalo}.")

    return valores


def validar_vela(registro, minutos, agora):
    try:
        horario = datetime.strptime(
            registro["datetime"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        abertura = float(registro["open"])
        maxima = float(registro["high"])
        minima = float(registro["low"])
        fechamento = float(registro["close"])

        volume_texto = registro.get("volume")
        volume = (
            float(volume_texto)
            if volume_texto not in (None, "")
            else None
        )
    except (KeyError, TypeError, ValueError):
        return None

    precos = (abertura, maxima, minima, fechamento)
    if not all(math.isfinite(valor) and valor > 0 for valor in precos):
        return None

    if maxima < max(abertura, fechamento, minima):
        return None
    if minima > min(abertura, fechamento, maxima):
        return None

    if horario > agora:
        return None

    if horario.minute % minutos != 0 or horario.second != 0:
        return None

    # Só aceita candles cujo intervalo já terminou.
    if horario + timedelta(minutes=minutos) > agora:
        return None

    if volume is not None and (
        not math.isfinite(volume) or volume < 0
    ):
        volume = None

    return (
        horario.strftime("%Y-%m-%d %H:%M:%S"),
        abertura,
        maxima,
        minima,
        fechamento,
        volume,
    )


def validar_lote(registros, minutos):
    agora = datetime.now(timezone.utc)
    validas = {}
    invalidas = 0

    for registro in registros:
        vela = validar_vela(registro, minutos, agora)
        if vela is None:
            invalidas += 1
            continue
        validas[vela[0]] = vela

    return sorted(validas.values(), key=lambda item: item[0]), invalidas


def criar_estrutura_banco(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            abertura REAL NOT NULL,
            maxima REAL NOT NULL,
            minima REAL NOT NULL,
            fechamento REAL NOT NULL,
            volume REAL,
            fonte TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            PRIMARY KEY (timeframe, timestamp)
        )
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_candles_timestamp
        ON candles(timestamp)
    """)


def salvar_velas(conexao, timeframe, velas):
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conexao.executemany("""
        INSERT INTO candles (
            timeframe, timestamp, abertura, maxima, minima,
            fechamento, volume, fonte, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe, timestamp)
        DO UPDATE SET
            abertura = excluded.abertura,
            maxima = excluded.maxima,
            minima = excluded.minima,
            fechamento = excluded.fechamento,
            volume = excluded.volume,
            fonte = excluded.fonte,
            atualizado_em = excluded.atualizado_em
    """, [
        (
            timeframe,
            vela[0],
            vela[1],
            vela[2],
            vela[3],
            vela[4],
            vela[5],
            "Twelve Data",
            agora,
        )
        for vela in velas
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
    print("COLETOR XAUUSD — M5 / M15 / H1")

    temporario, caminho_db = abrir_banco_temporario()
    resumo = {}

    try:
        conexao = sqlite3.connect(caminho_db)
        try:
            criar_estrutura_banco(conexao)

            # Cada temporalidade é coletada e validada separadamente.
            for timeframe, (intervalo_api, minutos) in TIMEFRAMES.items():
                print(f"Coletando {timeframe}...")
                recebidas = coletar_timeframe(intervalo_api)
                validas, invalidas = validar_lote(recebidas, minutos)

                if not validas:
                    raise RuntimeError(
                        f"Nenhuma vela fechada e válida em {timeframe}. "
                        "Banco oficial não será atualizado."
                    )

                salvar_velas(conexao, timeframe, validas)

                resumo[timeframe] = {
                    "recebidas": len(recebidas),
                    "validas": len(validas),
                    "invalidas": invalidas,
                }

            conexao.commit()

            for timeframe in TIMEFRAMES:
                total = conexao.execute(
                    "SELECT COUNT(*) FROM candles WHERE timeframe = ?",
                    (timeframe,),
                ).fetchone()[0]
                resumo[timeframe]["total_no_banco"] = total

        except Exception:
            conexao.rollback()
            raise
        finally:
            conexao.close()

        compactar_banco(caminho_db)

    finally:
        temporario.cleanup()

    for timeframe, dados in resumo.items():
        print(
            f"{timeframe}: recebidas={dados['recebidas']} | "
            f"válidas={dados['validas']} | "
            f"inválidas={dados['invalidas']} | "
            f"total={dados['total_no_banco']}"
        )

    print(f"Banco atualizado: {BANCO_GZ}")


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
