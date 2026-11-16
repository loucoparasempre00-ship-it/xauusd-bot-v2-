
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Coleta incremental e valida candles XAU/USD M5, M15 e H1.
Recupera velas fechadas que não chegaram a ser gravadas.
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
import urllib.error

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

QUANTIDADE_INICIAL = 5000
QUANTIDADE_MAXIMA = 5000
MARGEM_RECUPERACAO = 2
TIMEOUT = 60


def agora_utc():
    return datetime.now(timezone.utc)


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


def ultimo_timestamp(conexao, timeframe):
    resultado = conexao.execute(
        """
        SELECT MAX(timestamp)
        FROM candles
        WHERE timeframe = ?
        """,
        (timeframe,),
    ).fetchone()

    return resultado[0] if resultado else None


def calcular_quantidade(ultimo, minutos):
    if not ultimo:
        return QUANTIDADE_INICIAL

    horario = datetime.strptime(
        ultimo, "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)

    proxima_abertura = horario + timedelta(minutes=minutos)
    diferenca = agora_utc() - proxima_abertura

    velas_estimadas = max(
        0,
        math.ceil(diferenca.total_seconds() / (minutos * 60))
    )

    quantidade = velas_estimadas + MARGEM_RECUPERACAO

    return max(
        2,
        min(quantidade, QUANTIDADE_MAXIMA)
    )


def coletar_timeframe(intervalo, ultimo, minutos):
    chave = os.getenv("TWELVE_DATA_API_KEY")

    if not chave:
        raise RuntimeError(
            "Secret TWELVE_DATA_API_KEY não encontrado."
        )

    quantidade = calcular_quantidade(ultimo, minutos)

    parametros = {
        "symbol": SIMBOLO,
        "interval": intervalo,
        "outputsize": quantidade,
        "timezone": "UTC",
        "apikey": chave,
    }

    # Na coleta incremental, começa na vela seguinte
    # à última que já existe no banco.
    if ultimo:
        horario = datetime.strptime(
            ultimo, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        inicio = horario + timedelta(minutes=minutos)

        parametros["start_date"] = inicio.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    url = API_URL + "?" + urllib.parse.urlencode(parametros)

    requisicao = urllib.request.Request(
        url,
        headers={"User-Agent": "XAUUSD-Coletor/3.0"},
    )

    with urllib.request.urlopen(
        requisicao,
        timeout=TIMEOUT
    ) as resposta:
        dados = json.loads(
            resposta.read().decode("utf-8")
        )

    if dados.get("status") == "error" or dados.get("code"):
        raise RuntimeError(
            f"Twelve Data ({intervalo}): "
            f"{dados.get('message', dados)}"
        )

    valores = dados.get("values")

    if not isinstance(valores, list):
        raise RuntimeError(
            f"Resposta inválida da Twelve Data para {intervalo}."
        )

    return valores, quantidade


def validar_vela(registro, minutos, agora):
    try:
        horario = datetime.strptime(
            registro["datetime"],
            "%Y-%m-%d %H:%M:%S"
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

    precos = (
        abertura,
        maxima,
        minima,
        fechamento,
    )

    if not all(
        math.isfinite(valor) and valor > 0
        for valor in precos
    ):
        return None

    if maxima < max(abertura, fechamento, minima):
        return None

    if minima > min(abertura, fechamento, maxima):
        return None

    if horario > agora:
        return None

    if horario.minute % minutos != 0 or horario.second != 0:
        return None

    # Nunca grava uma vela cujo período ainda não terminou.
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


def validar_lote(registros, minutos, ultimo):
    agora = agora_utc()

    validas = {}
    invalidas = 0
    repetidas = 0

    for registro in registros:
        vela = validar_vela(registro, minutos, agora)

        if vela is None:
            invalidas += 1
            continue

        # Não regrava velas que já existem no histórico.
        if ultimo and vela[0] <= ultimo:
            repetidas += 1
            continue

        validas[vela[0]] = vela

    ordenadas = sorted(
        validas.values(),
        key=lambda item: item[0]
    )

    return ordenadas, invalidas, repetidas


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
    if not velas:
        return

    agora = agora_utc().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conexao.executemany("""
        INSERT OR IGNORE INTO candles (
            timeframe,
            timestamp,
            abertura,
            maxima,
            minima,
            fechamento,
            volume,
            fonte,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def contar_velas(conexao, timeframe):
    resultado = conexao.execute(
        """
        SELECT COUNT(*)
        FROM candles
        WHERE timeframe = ?
        """,
        (timeframe,),
    ).fetchone()

    return resultado[0] if resultado else 0


def compactar_banco(caminho_db):
    temporario_gz = BANCO_GZ.with_name(
        BANCO_GZ.name + ".tmp"
    )

    try:
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

        os.replace(temporario_gz, BANCO_GZ)

    finally:
        if temporario_gz.exists():
            temporario_gz.unlink()


def processar_timeframe(conexao, timeframe, intervalo, minutos):
    print(f"Coletando {timeframe}...")

    ultimo = ultimo_timestamp(conexao, timeframe)

    if ultimo:
        print(f"Última vela no banco: {ultimo}")
    else:
        print("Banco sem histórico para esta temporalidade.")

    try:
        recebidas, quantidade_solicitada = coletar_timeframe(
            intervalo,
            ultimo,
            minutos
        )

    except urllib.error.HTTPError as erro:
        if erro.code == 429:
            total = contar_velas(conexao, timeframe)

            if total > 0:
                print(
                    f"AVISO {timeframe}: limite 429 da API. "
                    "Histórico existente preservado; "
                    "recuperação ficará para a próxima execução."
                )

                return {
                    "recebidas": 0,
                    "novas": 0,
                    "invalidas": 0,
                    "repetidas": 0,
                    "total_no_banco": total,
                    "situacao": "API 429 - histórico preservado",
                }

        raise

    validas, invalidas, repetidas = validar_lote(
        recebidas,
        minutos,
        ultimo
    )

    salvar_velas(
        conexao,
        timeframe,
        validas
    )

    total = contar_velas(conexao, timeframe)

    if validas:
        print(
            f"{timeframe}: {len(validas)} novas velas "
            "fechadas recuperadas."
        )
    else:
        print(
            f"{timeframe}: nenhuma vela nova fechada."
        )

    return {
        "recebidas": len(recebidas),
        "solicitadas": quantidade_solicitada,
        "novas": len(validas),
        "invalidas": invalidas,
        "repetidas": repetidas,
        "total_no_banco": total,
        "situacao": "atualizado",
    }


def main():
    print("COLETOR XAUUSD — M5 / M15 / H1")
    print("Modo: incremental com recuperação de velas")

    temporario, caminho_db = abrir_banco_temporario()

    resumo = {}

    try:
        conexao = sqlite3.connect(caminho_db)

        try:
            criar_estrutura_banco(conexao)

            for timeframe, (intervalo, minutos) in TIMEFRAMES.items():
                resumo[timeframe] = processar_timeframe(
                    conexao,
                    timeframe,
                    intervalo,
                    minutos
                )

            # Não permite concluir com alguma temporalidade
            # sem histórico disponível.
            for timeframe in TIMEFRAMES:
                if contar_velas(conexao, timeframe) == 0:
                    raise RuntimeError(
                        f"{timeframe} está sem histórico. "
                        "Não é seguro atualizar o banco."
                    )

            conexao.commit()

        except Exception:
            conexao.rollback()
            raise

        finally:
            conexao.close()

        compactar_banco(caminho_db)

    finally:
        temporario.cleanup()

    print("\nRESUMO DA COLETA")

    for timeframe, dados in resumo.items():
        print(
            f"{timeframe}: "
            f"recebidas={dados['recebidas']} | "
            f"novas={dados['novas']} | "
            f"inválidas={dados['invalidas']} | "
            f"repetidas={dados['repetidas']} | "
            f"total={dados['total_no_banco']} | "
            f"situação={dados['situacao']}"
        )

    print(f"\nBanco atualizado: {BANCO_GZ}")


if __name__ == "__main__":
    try:
        main()

    except Exception as erro:
        print(
            f"ERRO: {erro}",
            file=sys.stderr
        )
        sys.exit(1)
