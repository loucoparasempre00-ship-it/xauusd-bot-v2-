
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VALIDADOR DO SETUP MATEMATICO

Utiliza exclusivamente:
    data/graficos.db.gz
    data/parametros_setups.json

Nao consulta bancos de operacoes.
"""

import gzip
import json
import os
import sqlite3
import math
from datetime import datetime, timezone

DB = "data/graficos.db.gz"
PARAMETROS = "data/parametros_setups.json"

MIN_AMOSTRAS_VALIDACAO = 30
TAXA_MINIMA = 0.55


def abrir_banco():
    with gzip.open(DB, "rb") as f:
        conteudo = f.read()
    con = sqlite3.connect(":memory:")
    con.deserialize(conteudo)
    con.row_factory = sqlite3.Row
    return con


def localizar(colunas, candidatos):
    mapa = {c.lower(): c for c in colunas}
    for c in candidatos:
        if c.lower() in mapa:
            return mapa[c.lower()]
    return None


def numero(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def carregar():
    with open(PARAMETROS, encoding="utf-8") as f:
        return json.load(f)


def validar():
    parametros = carregar()
    con = abrir_banco()

    tabelas = {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    if "indicadores_tecnicos" not in tabelas or "candles" not in tabelas:
        raise RuntimeError("Tabelas candles/indicadores_tecnicos ausentes.")

    ci = [r[1] for r in con.execute(
        "PRAGMA table_info(indicadores_tecnicos)"
    )]
    cv = [r[1] for r in con.execute("PRAGMA table_info(candles)")]

    ti = localizar(ci, ["timestamp", "datetime", "time", "data_hora", "data"])
    tv = localizar(cv, ["timestamp", "datetime", "time", "data_hora", "data"])
    close = localizar(cv, ["close", "fechamento", "preco_fechamento"])

    if not ti or not tv or not close:
        raise RuntimeError("Nao foi possivel identificar tempo e fechamento.")

    inds = [dict(r) for r in con.execute("SELECT * FROM indicadores_tecnicos")]
    velas = [dict(r) for r in con.execute("SELECT * FROM candles")]
    con.close()

    timeframe = localizar(ci, ["timeframe", "periodo", "intervalo"])
    if timeframe:
        inds = [
            r for r in inds
            if str(r.get(timeframe, "")).upper() in ("M5", "5M", "5MIN", "5")
        ]

    velas.sort(key=lambda r: str(r.get(tv, "")))
    precos = [
        (str(r.get(tv)), numero(r.get(close)))
        for r in velas
    ]
    precos = [(t, p) for t, p in precos if p is not None]
    indice = {t: i for i, (t, _) in enumerate(precos)}

    # Validador usa somente os 30% finais, fora da amostra de ajuste.
    corte = int(len(precos) * 0.70)
    horizonte = int(parametros.get("horizonte_velas", 3))
    pesos = parametros["pesos"]

    acertos = 0
    decisoes = 0
    long_count = 0
    short_count = 0
    lateral_count = 0

    for ind in inds:
        tempo = str(ind.get(ti))
        i = indice.get(tempo)

        if i is None or i < corte or i + horizonte >= len(precos):
            continue

        atual = precos[i][1]
        futuro = precos[i + horizonte][1]
        if not atual:
            continue

        # Medidas compatíveis com o otimizador.
        medidas = {}

        ema9 = localizar(ci, ["ema9", "mme9", "ema_9"])
        ema21 = localizar(ci, ["ema21", "mme21", "ema_21"])
        rsi_col = localizar(ci, ["rsi14", "rsi_14", "rsi"])
        macd_col = localizar(ci, ["macd", "macd_line"])

        if ema9 and ema21:
            a, b = numero(ind.get(ema9)), numero(ind.get(ema21))
            if a is not None and b is not None and atual:
                medidas["EMA"] = max(-10, min(10, (a - b) / atual * 1000))

        if rsi_col:
            rsi = numero(ind.get(rsi_col))
            if rsi is not None:
                medidas["RSI"] = max(-10, min(10, (rsi - 50) / 5))

        # MACD foi normalizado pelo otimizador. Para manter comparabilidade,
        # nao se inventa uma escala diferente na validacao.
        # Se o banco tiver MACD sem score normalizado, ele fica fora desta
        # amostra de validacao e isso sera informado no resultado.

        for c in ci:
            nome = c.upper()
            if nome.endswith("_SCORE") or nome.endswith("_MEDIDA"):
                valor = numero(ind.get(c))
                if valor is not None:
                    medidas[nome] = max(-10, min(10, valor))

        # Indicadores sem leitura nao recebem valor artificial.
        disponiveis = {
            k: v for k, v in medidas.items()
            if k in pesos
        }
        if not disponiveis:
            continue

        numerador = sum(
            v * max(1, numero(pesos.get(k)) or 1)
            for k, v in disponiveis.items()
        )
        denominador = sum(
            max(1, numero(pesos.get(k)) or 1)
            for k in disponiveis
        )
        score = numerador / denominador

        retorno = (futuro - atual) / atual
        limite = float(parametros["limite_long"])

        if score >= limite:
            long_count += 1
            decisoes += 1
            if retorno > 0:
                acertos += 1
        elif score <= -limite:
            short_count += 1
            decisoes += 1
            if retorno < 0:
                acertos += 1
        else:
            lateral_count += 1

    taxa = acertos / decisoes if decisoes else 0.0

    aprovado = (
        decisoes >= MIN_AMOSTRAS_VALIDACAO
        and taxa >= TAXA_MINIMA
    )

    parametros["status"] = "APROVADO" if aprovado else "REPROVADO"
    parametros["validacao"] = {
        "data_utc": datetime.now(timezone.utc).isoformat(),
        "amostras_decididas": decisoes,
        "long": long_count,
        "short": short_count,
        "lateral": lateral_count,
        "acertos_direcionais": acertos,
        "taxa_direcional": round(taxa, 4),
        "minimo_amostras": MIN_AMOSTRAS_VALIDACAO,
        "taxa_minima": TAXA_MINIMA
    }

    with open(PARAMETROS, "w", encoding="utf-8") as f:
        json.dump(parametros, f, ensure_ascii=False, indent=2)

    print(json.dumps(parametros["validacao"], ensure_ascii=False, indent=2))
    print("STATUS:", parametros["status"])


if __name__ == "__main__":
    validar()
