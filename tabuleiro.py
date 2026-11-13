
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TABULEIRO PROBABILÍSTICO XAUUSD

Lê candles fechados de M5, M15 e H1 em data/graficos.db.gz.
Representa cada candle como evento de preço:
  T+ / T- = toque na borda superior / inferior
  V+ / V- = violação da borda superior / inferior
  R+ / R- = rejeição da borda superior / inferior
  C+ / C- / N = continuação de alta / baixa / neutra

Compara a sequência atual de 12 eventos com sequências históricas
iguais ou semelhantes (até 2 eventos diferentes) e mede os eventos
que ocorreram nos 3 candles seguintes.

Não envia sinais, não executa ordens e não coleta dados externos.
Persistência: somente data/graficos.db.gz.
"""

import os
import sys
import gzip
import json
import math
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BANCO_GZ = DATA_DIR / "graficos.db.gz"

TIMEFRAMES = {
    "M5": 5,
    "M15": 15,
    "H1": 60,
}

TAMANHO_SEQUENCIA = 12
DISTANCIA_MAXIMA = 2
HORIZONTES = (1, 2, 3)
MINIMO_CANDLES = TAMANHO_SEQUENCIA * 2 + max(HORIZONTES)
VERSAO = "1.0"


def abrir_banco():
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


def verificar_estrutura(conexao):
    tabelas = {
        linha["name"]
        for linha in conexao.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
        """).fetchall()
    }

    if "candles" not in tabelas:
        raise RuntimeError(
            "Tabela candles não encontrada em graficos.db.gz."
        )

    colunas = {
        linha["name"]
        for linha in conexao.execute(
            "PRAGMA table_info(candles)"
        ).fetchall()
    }

    obrigatorias = {
        "timeframe", "timestamp", "abertura",
        "maxima", "minima", "fechamento"
    }

    if not obrigatorias.issubset(colunas):
        faltantes = sorted(obrigatorias - colunas)
        raise RuntimeError(
            "A tabela candles não possui as colunas necessárias: "
            + ", ".join(faltantes)
        )


def criar_tabelas(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS tabuleiro_estado_atual (
            timeframe TEXT PRIMARY KEY,
            timestamp_referencia TEXT NOT NULL,
            sequencia_json TEXT NOT NULL,
            quantidade_candles INTEGER NOT NULL,
            ocorrencias_encontradas INTEGER NOT NULL,
            ocorrencias_exatas INTEGER NOT NULL,
            distancia_maxima INTEGER NOT NULL,
            versao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS tabuleiro_probabilidades (
            timeframe TEXT NOT NULL,
            timestamp_referencia TEXT NOT NULL,
            horizonte INTEGER NOT NULL,
            evento_futuro TEXT NOT NULL,
            ocorrencias INTEGER NOT NULL,
            total_amostras INTEGER NOT NULL,
            probabilidade REAL NOT NULL,
            distancia_media REAL NOT NULL,
            versao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            PRIMARY KEY (
                timeframe,
                timestamp_referencia,
                horizonte,
                evento_futuro
            )
        )
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_tabuleiro_probabilidade
        ON tabuleiro_probabilidades(
            timeframe, timestamp_referencia, horizonte
        )
    """)


def carregar_candles(conexao, timeframe, minutos):
    linhas = conexao.execute("""
        SELECT timestamp, abertura, maxima, minima, fechamento
        FROM candles
        WHERE timeframe = ?
        ORDER BY timestamp ASC
    """, (timeframe,)).fetchall()

    agora = datetime.now(timezone.utc)
    resultado = []

    for linha in linhas:
        try:
            horario = datetime.strptime(
                linha["timestamp"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)

            if horario + timedelta(minutes=minutos) > agora:
                continue

            abertura = float(linha["abertura"])
            maxima = float(linha["maxima"])
            minima = float(linha["minima"])
            fechamento = float(linha["fechamento"])

            valores = (abertura, maxima, minima, fechamento)
            if not all(math.isfinite(v) and v > 0 for v in valores):
                continue

            if maxima < max(abertura, fechamento, minima):
                continue
            if minima > min(abertura, fechamento, maxima):
                continue

            resultado.append({
                "timestamp": linha["timestamp"],
                "abertura": abertura,
                "maxima": maxima,
                "minima": minima,
                "fechamento": fechamento,
            })

        except (TypeError, ValueError, KeyError):
            continue

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
    """Classifica a vela usando apenas as 12 velas anteriores."""
    topo = max(c["maxima"] for c in anteriores)
    fundo = min(c["minima"] for c in anteriores)
    amplitudes = [
        max(0.0, c["maxima"] - c["minima"])
        for c in anteriores
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
        anteriores = candles[
            indice - TAMANHO_SEQUENCIA:indice
        ]
        eventos[indice] = classificar_evento(
            candles[indice], anteriores
        )

    return eventos


def distancia_sequencias(atual, historica):
    return sum(
        1 for a, b in zip(atual, historica)
        if a != b
    )


def encontrar_repeticoes(eventos):
    """
    Compara a sequência atual com trechos históricos.
    Os três eventos posteriores de cada ocorrência precisam estar
    inteiramente antes do início da sequência atual.
    """
    inicio_atual = len(eventos) - TAMANHO_SEQUENCIA
    atual = eventos[inicio_atual:]

    if len(atual) != TAMANHO_SEQUENCIA or any(
        evento is None for evento in atual
    ):
        raise RuntimeError(
            "Não foi possível formar a sequência atual de 12 eventos."
        )

    limite_inicio = (
        inicio_atual - TAMANHO_SEQUENCIA - max(HORIZONTES)
    )
    correspondencias = []

    for inicio in range(0, limite_inicio + 1):
        trecho = eventos[inicio:inicio + TAMANHO_SEQUENCIA]

        if len(trecho) != TAMANHO_SEQUENCIA:
            continue
        if any(evento is None for evento in trecho):
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


def salvar_resultado(
    conexao, timeframe, timestamp_referencia,
    sequencia, correspondencias
):
    agora = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    exatas = sum(
        1 for item in correspondencias
        if item["distancia"] == 0
    )

    conexao.execute("""
        INSERT INTO tabuleiro_estado_atual (
            timeframe, timestamp_referencia, sequencia_json,
            quantidade_candles, ocorrencias_encontradas,
            ocorrencias_exatas, distancia_maxima,
            versao, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(timeframe) DO UPDATE SET
            timestamp_referencia = excluded.timestamp_referencia,
            sequencia_json = excluded.sequencia_json,
            quantidade_candles = excluded.quantidade_candles,
            ocorrencias_encontradas = excluded.ocorrencias_encontradas,
            ocorrencias_exatas = excluded.ocorrencias_exatas,
            distancia_maxima = excluded.distancia_maxima,
            versao = excluded.versao,
            atualizado_em = excluded.atualizado_em
    """, (
        timeframe,
        timestamp_referencia,
        json.dumps(sequencia, ensure_ascii=False),
        TAMANHO_SEQUENCIA,
        len(correspondencias),
        exatas,
        DISTANCIA_MAXIMA,
        VERSAO,
        agora,
    ))

    conexao.execute("""
        DELETE FROM tabuleiro_probabilidades
        WHERE timeframe = ?
    """, (timeframe,))

    total_por_horizonte = {}

    for horizonte in HORIZONTES:
        contagem = Counter(
            item["futuros"][horizonte]
            for item in correspondencias
        )
        total = sum(contagem.values())
        total_por_horizonte[horizonte] = total

        if total == 0:
            continue

        distancia_media = sum(
            item["distancia"] for item in correspondencias
        ) / len(correspondencias)

        registros = []

        for evento, quantidade in sorted(contagem.items()):
            registros.append((
                timeframe,
                timestamp_referencia,
                horizonte,
                evento,
                quantidade,
                total,
                quantidade / total,
                distancia_media,
                VERSAO,
                agora,
            ))

        conexao.executemany("""
            INSERT INTO tabuleiro_probabilidades (
                timeframe, timestamp_referencia, horizonte,
                evento_futuro, ocorrencias, total_amostras,
                probabilidade, distancia_media, versao, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, registros)

    return exatas, total_por_horizonte


def processar_timeframe(conexao, timeframe, minutos):
    candles = carregar_candles(conexao, timeframe, minutos)

    if len(candles) < MINIMO_CANDLES:
        raise RuntimeError(
            f"{timeframe}: histórico insuficiente. "
            f"Encontradas {len(candles)} velas fechadas; "
            f"necessárias pelo menos {MINIMO_CANDLES}."
        )

    eventos = construir_eventos(candles)
    sequencia, correspondencias = encontrar_repeticoes(eventos)
    timestamp_referencia = candles[-1]["timestamp"]

    exatas, totais = salvar_resultado(
        conexao,
        timeframe,
        timestamp_referencia,
        sequencia,
        correspondencias,
    )

    return {
        "candles": len(candles),
        "timestamp": timestamp_referencia,
        "sequencia": sequencia,
        "correspondencias": len(correspondencias),
        "exatas": exatas,
        "totais": totais,
        "distribuicoes": {
            horizonte: Counter(
                item["futuros"][horizonte]
                for item in correspondencias
            )
            for horizonte in HORIZONTES
        },
    }


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
    print("TABULEIRO PROBABILÍSTICO XAUUSD — M5 / M15 / H1")

    temporario, caminho_db, conexao = abrir_banco()

    try:
        verificar_estrutura(conexao)
        criar_tabelas(conexao)

        resumo = {}

        try:
            for timeframe, minutos in TIMEFRAMES.items():
                print(f"Analisando {timeframe}...")
                resumo[timeframe] = processar_timeframe(
                    conexao, timeframe, minutos
                )

            conexao.commit()

        except Exception:
            conexao.rollback()
            raise

        finally:
            conexao.close()

        compactar_banco(caminho_db)

        for timeframe, dados in resumo.items():
            print("")
            print(f"{timeframe} — referência {dados['timestamp']}")
            print(
                f"Sequência de 12: {' '.join(dados['sequencia'])}"
            )
            print(
                f"Correspondências: {dados['correspondencias']} "
                f"| exatas: {dados['exatas']}"
            )

            if dados["correspondencias"] == 0:
                print(
                    "Nenhuma sequência histórica semelhante encontrada."
                )
                continue

            for horizonte in HORIZONTES:
                contagem = dados["distribuicoes"][horizonte]
                total = dados["totais"][horizonte]

                if total == 0:
                    print(f"Após {horizonte} vela(s): sem amostras")
                    continue

                partes = []
                for evento, quantidade in contagem.most_common():
                    percentual = quantidade / total * 100
                    partes.append(
                        f"{evento}={percentual:.1f}% ({quantidade})"
                    )

                print(
                    f"Após {horizonte} vela(s): "
                    + " | ".join(partes)
                )

        print("")
        print(f"Banco atualizado: {BANCO_GZ}")
        print(
            "As probabilidades descrevem frequências históricas; "
            "não garantem o próximo movimento."
        )

    finally:
        temporario.cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
