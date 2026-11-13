
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OTIMIZADOR DE PARAMETROS DO SETUP MATEMATICO

Leitura: data/graficos.db.gz
Gravacao: data/sinais.db.gz

Nao consulta bancos de operacoes nem Gemini.
"""

import bisect
import gzip
import json
import math
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
GRAFICOS = BASE / "data" / "graficos.db.gz"
SINAIS = BASE / "data" / "sinais.db.gz"

HORIZONTE = 3
PERCENTUAL_TREINO = 0.70
PESOS_CANDIDATOS = (1, 2, 3, 4, 5)
LIMITES_CANDIDATOS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)

MIN_AMOSTRAS_TREINO = 100
MIN_SINAIS_TREINO = 20


def agora_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numero(valor):
    try:
        n = float(valor)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def tanh_score(valor, escala):
    if valor is None:
        return None
    return max(-10.0, min(10.0, math.tanh(valor / escala) * 10.0))


def abrir_sqlite_gz(caminho, exigir_existencia=True):
    if exigir_existencia and not caminho.is_file():
        raise FileNotFoundError(
            f"Banco oficial nao encontrado: {caminho}"
        )

    temporario = tempfile.TemporaryDirectory(prefix="xau_setup_")
    db_path = Path(temporario.name) / caminho.name.replace(".gz", "")

    if caminho.exists():
        with gzip.open(caminho, "rb") as origem:
            with open(db_path, "wb") as destino:
                shutil.copyfileobj(origem, destino)

    conexao = sqlite3.connect(db_path)
    conexao.row_factory = sqlite3.Row

    return temporario, db_path, conexao


def compactar_sqlite_gz(temporario, db_path, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario_gz = destino.with_name(destino.name + ".tmp")

    try:
        with open(db_path, "rb") as origem:
            with open(temporario_gz, "wb") as bruto:
                with gzip.GzipFile(
                    fileobj=bruto,
                    mode="wb",
                    mtime=0
                ) as saida:
                    shutil.copyfileobj(origem, saida)

        os.replace(temporario_gz, destino)

    finally:
        if temporario_gz.exists():
            temporario_gz.unlink()
        temporario.cleanup()


def fechar_leitura(temporario, conexao):
    try:
        conexao.close()
    finally:
        temporario.cleanup()


def criar_tabelas_setup(con):
    # Tabelas exclusivas do setup.
    # Nenhuma tabela existente sera apagada ou alterada.

    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_parametros (
            versao INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            pesos_json TEXT NOT NULL,
            limite_long REAL NOT NULL,
            limite_short REAL NOT NULL,
            horizonte_velas INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            treino_json TEXT NOT NULL,
            validacao_json TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_decisoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_utc TEXT NOT NULL,
            timestamp_candle TEXT NOT NULL,
            decisao TEXT NOT NULL,
            pontuacao REAL NOT NULL,
            limite REAL NOT NULL,
            parametro_versao INTEGER NOT NULL,
            indicadores_json TEXT NOT NULL
        )
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_setup_decisoes_data
        ON setup_decisoes(timestamp_candle)
    """)


def carregar_banco_graficos():
    temporario, db_path, con = abrir_sqlite_gz(GRAFICOS)

    try:
        tabelas = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        necessarias = {"candles", "indicadores_tecnicos"}

        if not necessarias.issubset(tabelas):
            raise RuntimeError(
                "Tabelas necessarias ausentes em graficos.db.gz. "
                f"Encontradas: {sorted(tabelas)}"
            )

        candles = [
            dict(r)
            for r in con.execute("""
                SELECT timeframe, timestamp, fechamento
                FROM candles
                WHERE timeframe = 'M5'
                ORDER BY timestamp
            """)
        ]

        indicadores = [
            dict(r)
            for r in con.execute("""
                SELECT
                    timeframe,
                    timestamp,
                    mme_9,
                    mme_21,
                    rsi_14,
                    macd_histograma
                FROM indicadores_tecnicos
                WHERE timeframe IN ('M5', 'M15', 'H1')
                ORDER BY timeframe, timestamp
            """)
        ]

    finally:
        fechar_leitura(temporario, con)

    if not candles or not indicadores:
        raise RuntimeError(
            "Candles M5 ou indicadores tecnicos nao encontrados."
        )

    return candles, indicadores


def construir_indices_indicadores(indicadores):
    indices = {}
    duracoes = {
        "M5": 5,
        "M15": 15,
        "H1": 60
    }

    for tf in duracoes:
        linhas = [
            r for r in indicadores
            if r["timeframe"] == tf
        ]

        tempos = [
            datetime.strptime(
                r["timestamp"],
                "%Y-%m-%d %H:%M:%S"
            )
            for r in linhas
        ]

        indices[tf] = (
            tempos,
            linhas,
            duracoes[tf]
        )

    return indices


def obter_indicador_fechado(
    indices,
    timeframe,
    instante_fechamento
):
    tempos, linhas, minutos = indices[timeframe]

    limite = instante_fechamento - timedelta(
        minutes=minutos
    )

    pos = bisect.bisect_right(tempos, limite) - 1

    return linhas[pos] if pos >= 0 else None


def extrair_features(indicadores_por_tf, fechamento):
    features = {}

    for tf in ("M5", "M15", "H1"):
        linha = indicadores_por_tf.get(tf)

        if not linha:
            continue

        mme9 = numero(linha.get("mme_9"))
        mme21 = numero(linha.get("mme_21"))
        rsi = numero(linha.get("rsi_14"))
        hist = numero(linha.get("macd_histograma"))

        # MME 9 x MME 21
        if (
            mme9 is not None
            and mme21 is not None
            and fechamento > 0
        ):
            spread_bps = (
                (mme9 - mme21) / fechamento
            ) * 10000.0

            features[f"{tf}_EMA"] = tanh_score(
                spread_bps, 2.0
            )

        # RSI 14
        if rsi is not None:
            features[f"{tf}_RSI"] = max(
                -10.0,
                min(10.0, (rsi - 50.0) / 5.0)
            )

        # Histograma MACD
        if hist is not None and fechamento > 0:
            hist_bps = (
                hist / fechamento
            ) * 10000.0

            features[f"{tf}_MACD"] = tanh_score(
                hist_bps, 0.5
            )

    return {
        k: round(v, 8)
        for k, v in features.items()
        if v is not None
    }


def carregar_amostras():
    candles, indicadores = carregar_banco_graficos()
    indices = construir_indices_indicadores(indicadores)

    amostras = []

    for i, candle in enumerate(candles):
        futuro = i + HORIZONTE

        if futuro >= len(candles):
            break

        atual = numero(candle.get("fechamento"))
        depois = numero(
            candles[futuro].get("fechamento")
        )

        if atual is None or depois is None or atual <= 0:
            continue

        inicio = datetime.strptime(
            candle["timestamp"],
            "%Y-%m-%d %H:%M:%S"
        )

        fechamento_candle = inicio + timedelta(minutes=5)

        linhas = {
            tf: obter_indicador_fechado(
                indices,
                tf,
                fechamento_candle
            )
            for tf in ("M5", "M15", "H1")
        }

        features = extrair_features(linhas, atual)

        if not features:
            continue

        amostras.append({
            "timestamp": candle["timestamp"],
            "features": features,
            "retorno": (depois - atual) / atual
        })

    if len(amostras) < MIN_AMOSTRAS_TREINO:
        raise RuntimeError(
            f"Amostras historicas insuficientes: "
            f"{len(amostras)}; minimo: {MIN_AMOSTRAS_TREINO}."
        )

    return amostras


def percentil(valores, p):
    ordenados = sorted(valores)

    if not ordenados:
        return 0.0

    pos = (len(ordenados) - 1) * p
    baixo = int(pos)
    alto = min(baixo + 1, len(ordenados) - 1)

    return (
        ordenados[baixo]
        + (ordenados[alto] - ordenados[baixo])
        * (pos - baixo)
    )


def pontuar(features, pesos):
    numerador = 0.0
    denominador = 0.0
    usados = {}

    for nome, leitura in features.items():
        peso = numero(pesos.get(nome))

        # Peso ausente recebe 1.
        # A leitura do indicador continua participando.
        if peso is None or peso <= 0:
            peso = 1.0

        numerador += leitura * peso
        denominador += peso

        usados[nome] = {
            "leitura": leitura,
            "peso": peso
        }

    if denominador <= 0:
        return None, usados

    return numerador / denominador, usados


def avaliar(amostras, pesos, limite, piso_movimento):
    sinais = 0
    acertos = 0
    long_count = 0
    short_count = 0
    laterais = 0

    for amostra in amostras:
        score, _ = pontuar(
            amostra["features"],
            pesos
        )

        if score is None:
            continue

        retorno = amostra["retorno"]

        if score >= limite:
            long_count += 1
            sinais += 1

            if retorno > piso_movimento:
                acertos += 1

        elif score <= -limite:
            short_count += 1
            sinais += 1

            if retorno < -piso_movimento:
                acertos += 1

        else:
            laterais += 1

    taxa = acertos / sinais if sinais else 0.0
    cobertura = sinais / len(amostras) if amostras else 0.0

    objetivo = taxa * math.sqrt(cobertura)

    return {
        "amostras": len(amostras),
        "sinais": sinais,
        "long": long_count,
        "short": short_count,
        "lateral": laterais,
        "acertos": acertos,
        "taxa_direcional": round(taxa, 6),
        "cobertura": round(cobertura, 6),
        "objetivo": round(objetivo, 6)
    }


def salvar_parametros(parametros):
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        criar_tabelas_setup(con)

        cur = con.execute("""
            INSERT INTO setup_parametros (
                criado_utc,
                status,
                pesos_json,
                limite_long,
                limite_short,
                horizonte_velas,
                features_json,
                treino_json,
                validacao_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            agora_utc(),
            "PENDENTE_VALIDACAO",
            json.dumps(
                parametros["pesos"],
                ensure_ascii=False,
                sort_keys=True
            ),
            parametros["limite_long"],
            parametros["limite_short"],
            HORIZONTE,
            json.dumps(
                parametros["features"],
                ensure_ascii=False
            ),
            json.dumps(
                parametros["treino"],
                ensure_ascii=False
            )
        ))

        versao = cur.lastrowid

        con.commit()
        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS
        )

        return versao

    except Exception:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass

        temporario.cleanup()
        raise


def otimizar():
    amostras = carregar_amostras()

    corte = int(
        len(amostras) * PERCENTUAL_TREINO
    )

    # Purga o horizonte para nao cruzar treino e validacao.
    treino = amostras[
        :max(0, corte - HORIZONTE)
    ]

    if len(treino) < MIN_AMOSTRAS_TREINO:
        raise RuntimeError(
            "A parte de treinamento nao possui "
            "amostras suficientes."
        )

    movimentos = [
        abs(a["retorno"])
        for a in treino
    ]

    piso = percentil(movimentos, 0.20)

    features = sorted({
        nome
        for amostra in treino
        for nome in amostra["features"]
    })

    # Todos os indicadores comecam com peso 1.
    pesos = {
        nome: 1
        for nome in features
    }

    # Busca coordenada de pesos.
    for _ in range(2):
        for nome in features:
            melhor = None

            for peso in PESOS_CANDIDATOS:
                tentativa = dict(pesos)
                tentativa[nome] = peso

                for limite in LIMITES_CANDIDATOS:
                    resultado = avaliar(
                        treino,
                        tentativa,
                        limite,
                        piso
                    )

                    if resultado["sinais"] < MIN_SINAIS_TREINO:
                        continue

                    chave = (
                        resultado["objetivo"],
                        resultado["taxa_direcional"],
                        resultado["cobertura"]
                    )

                    if melhor is None or chave > melhor[0]:
                        melhor = (
                            chave,
                            peso,
                            limite
                        )

            if melhor:
                pesos[nome] = melhor[1]

    # Escolha final do limite.
    melhor_final = None

    for limite in LIMITES_CANDIDATOS:
        resultado = avaliar(
            treino,
            pesos,
            limite,
            piso
        )

        if resultado["sinais"] < MIN_SINAIS_TREINO:
            continue

        chave = (
            resultado["objetivo"],
            resultado["taxa_direcional"],
            resultado["cobertura"]
        )

        if melhor_final is None or chave > melhor_final[0]:
            melhor_final = (
                chave,
                limite,
                resultado
            )

    if melhor_final is None:
        raise RuntimeError(
            "Nenhuma combinacao produziu sinais suficientes."
        )

    melhor_limite = melhor_final[1]
    resultado_treino = melhor_final[2]

    parametros = {
        "pesos": pesos,
        "limite_long": melhor_limite,
        "limite_short": -melhor_limite,
        "features": features,
        "treino": {
            **resultado_treino,
            "piso_movimento": piso,
            "amostras_treino": len(treino),
            "amostras_total": len(amostras),
            "percentual_treino": PERCENTUAL_TREINO
        }
    }

    versao = salvar_parametros(parametros)

    print(json.dumps({
        "versao": versao,
        "status": "PENDENTE_VALIDACAO",
        **parametros
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    otimizar()
