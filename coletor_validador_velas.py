#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Coleta incremental e valida candles XAU/USD M5, M15 e H1.
Persistencia direta no banco SQLite oficial: data/xauusd.db.
"""

import os
import sys
import math
import json
import sqlite3
import urllib.parse
import urllib.request
import urllib.error

from pathlib import Path
from datetime import datetime, timedelta, timezone


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BANCO = DATA_DIR / "xauusd.db"
ESQUEMA = BASE_DIR / "database" / "esquema_xauusd.sql"

API_URL = "https://api.twelvedata.com/time_series"
SIMBOLO = "XAU/USD"

TIMEFRAMES = {
    "M5": ("5min", 5, "m5"),
    "M15": ("15min", 15, "m15"),
    "H1": ("1h", 60, "h1"),
}

QUANTIDADE_INICIAL = 5000
QUANTIDADE_MAXIMA = 5000
MARGEM_RECUPERACAO = 2
RETENCAO_DIAS = 10
TIMEOUT = 60


def agora_utc():
    return datetime.now(timezone.utc)


def abrir_banco():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not ESQUEMA.is_file():
        raise RuntimeError(
            f"Esquema oficial nao encontrado: {ESQUEMA}"
        )

    conexao = sqlite3.connect(BANCO, timeout=30)
    conexao.execute("PRAGMA busy_timeout = 30000")
    conexao.execute("PRAGMA journal_mode = WAL")

    try:
        conexao.executescript(
            ESQUEMA.read_text(encoding="utf-8")
        )

        tabelas = {
            linha[0]
            for linha in conexao.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        esperadas = {"m5", "m15", "h1"}
        ausentes = esperadas - tabelas

        if ausentes:
            raise RuntimeError(
                "Tabelas oficiais ausentes: "
                + ", ".join(sorted(ausentes))
            )

        return conexao

    except Exception:
        conexao.close()
        raise


def ultimo_timestamp(conexao, tabela):
    resultado = conexao.execute(
        f'SELECT MAX(data_hora) FROM "{tabela}"'
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

    return max(2, min(quantidade, QUANTIDADE_MAXIMA))


def coletar_timeframe(intervalo, ultimo, minutos):
    chave = os.getenv("TWELVE_DATA_API_KEY")

    if not chave:
        raise RuntimeError(
            "Secret TWELVE_DATA_API_KEY nao encontrado."
        )

    quantidade = calcular_quantidade(ultimo, minutos)

    parametros = {
        "symbol": SIMBOLO,
        "interval": intervalo,
        "outputsize": quantidade,
        "timezone": "UTC",
        "apikey": chave,
    }

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
        headers={"User-Agent": "XAUUSD-Coletor/4.0"},
    )

    with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))

    if dados.get("status") == "error" or dados.get("code"):
        raise RuntimeError(
            f"Twelve Data ({intervalo}): "
            f"{dados.get('message', dados)}"
        )

    valores = dados.get("values")

    if not isinstance(valores, list):
        raise RuntimeError(
            f"Resposta invalida da Twelve Data para {intervalo}."
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

    precos = (abertura, maxima, minima, fechamento)

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

        if ultimo and vela[0] <= ultimo:
            repetidas += 1
            continue

        validas[vela[0]] = vela

    ordenadas = sorted(
        validas.values(),
        key=lambda item: item[0]
    )

    return ordenadas, invalidas, repetidas


def salvar_velas(conexao, tabela, velas):
    if not velas:
        return 0

    cursor = conexao.executemany(
        f"""
        INSERT OR IGNORE INTO "{tabela}" (
            data_hora,
            abertura,
            maxima,
            minima,
            fechamento,
            volume,
            fonte
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                vela[0],
                vela[1],
                vela[2],
                vela[3],
                vela[4],
                vela[5],
                "Twelve Data",
            )
            for vela in velas
        ]
    )

    return cursor.rowcount


def contar_velas(conexao, tabela):
    resultado = conexao.execute(
        f'SELECT COUNT(*) FROM "{tabela}"'
    ).fetchone()

    return resultado[0] if resultado else 0


def limpar_historico_antigo(conexao):
    for tabela in ("m5", "m15", "h1"):
        conexao.execute(
            f"""
            DELETE FROM "{tabela}"
            WHERE data_hora < datetime('now', ?)
            """,
            (f"-{RETENCAO_DIAS} days",)
        )


def processar_timeframe(conexao, timeframe, intervalo, minutos, tabela):
    print(f"Coletando {timeframe}...")
    ultimo = ultimo_timestamp(conexao, tabela)

    if ultimo:
        print(f"Ultima vela no banco: {ultimo}")
    else:
        print("Tabela sem historico; solicitando carga inicial.")

    try:
        recebidas, quantidade_solicitada = coletar_timeframe(
            intervalo, ultimo, minutos
        )

    except urllib.error.HTTPError as erro:
        if erro.code == 429:
            total = contar_velas(conexao, tabela)

            if total > 0:
                print(
                    f"AVISO {timeframe}: limite 429 da API. "
                    "Historico existente preservado."
                )

                return {
                    "recebidas": 0,
                    "solicitadas": 0,
                    "novas": 0,
                    "invalidas": 0,
                    "repetidas": 0,
                    "total_no_banco": total,
                    "situacao": "API 429 - historico preservado",
                }

        raise

    validas, invalidas, repetidas = validar_lote(
        recebidas, minutos, ultimo
    )

    novas = salvar_velas(conexao, tabela, validas)
    total = contar_velas(conexao, tabela)

    print(
        f"{timeframe}: {novas} novas velas gravadas; "
        f"{invalidas} invalidas; {repetidas} repetidas."
    )

    return {
        "recebidas": len(recebidas),
        "solicitadas": quantidade_solicitada,
        "novas": novas,
        "invalidas": invalidas,
        "repetidas": repetidas,
        "total_no_banco": total,
        "situacao": "atualizado",
    }


def main():
    print("COLETOR XAUUSD - M5 / M15 / H1")
    print("Persistencia: SQLite direto em data/xauusd.db")
    print(f"Retencao: {RETENCAO_DIAS} dias")

    conexao = abrir_banco()
    resumo = {}

    try:
        conexao.execute("BEGIN")

        for timeframe, (intervalo, minutos, tabela) in TIMEFRAMES.items():
            resumo[timeframe] = processar_timeframe(
                conexao,
                timeframe,
                intervalo,
                minutos,
                tabela
            )

        for timeframe, (_, _, tabela) in TIMEFRAMES.items():
            if contar_velas(conexao, tabela) == 0:
                raise RuntimeError(
                    f"{timeframe} esta sem historico. "
                    "A transacao sera cancelada."
                )

        limpar_historico_antigo(conexao)
        conexao.commit()

    except Exception:
        conexao.rollback()
        raise

    finally:
        conexao.close()

    print("\nRESUMO DA COLETA")

    for timeframe, dados in resumo.items():
        print(
            f"{timeframe}: recebidas={dados['recebidas']} | "
            f"novas={dados['novas']} | "
            f"invalidas={dados['invalidas']} | "
            f"repetidas={dados['repetidas']} | "
            f"total={dados['total_no_banco']} | "
            f"situacao={dados['situacao']}"
        )

    print(f"\nBanco atualizado: {BANCO}")


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
