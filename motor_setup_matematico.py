
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MOTOR MATEMATICO DE SETUP

Entrada:
    data/parametros_setups.json
    data/graficos.db.gz

Saida:
    data/decisao_setup.json

Decisoes:
    LONG
    SHORT
    LATERAL

Nao utiliza Gemini nem bancos de operacoes.
"""

import gzip
import json
import os
import sqlite3
import math
from datetime import datetime, timezone

DB = "data/graficos.db.gz"
PARAMETROS = "data/parametros_setups.json"
SAIDA = "data/decisao_setup.json"


def numero(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def localizar(colunas, candidatos):
    mapa = {c.lower(): c for c in colunas}
    for c in candidatos:
        if c.lower() in mapa:
            return mapa[c.lower()]
    return None


def abrir_banco():
    with gzip.open(DB, "rb") as f:
        conteudo = f.read()
    con = sqlite3.connect(":memory:")
    con.deserialize(conteudo)
    con.row_factory = sqlite3.Row
    return con


def carregar_parametros():
    with open(PARAMETROS, encoding="utf-8") as f:
        p = json.load(f)

    if p.get("status") != "APROVADO":
        raise RuntimeError(
            "Parametros ainda nao foram aprovados pelo validador."
        )
    return p


def ler_ultimo_indicador():
    con = abrir_banco()

    tabelas = {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "indicadores_tecnicos" not in tabelas:
        raise RuntimeError("Tabela indicadores_tecnicos nao encontrada.")

    colunas = [
        r[1] for r in con.execute(
            "PRAGMA table_info(indicadores_tecnicos)"
        )
    ]

    tempo = localizar(
        colunas, ["timestamp", "datetime", "time", "data_hora", "data"]
    )
    timeframe = localizar(colunas, ["timeframe", "periodo", "intervalo"])

    if not tempo:
        raise RuntimeError("Coluna de tempo nao encontrada.")

    if timeframe:
        consulta = (
            f'SELECT * FROM indicadores_tecnicos '
            f'WHERE UPPER("{timeframe}") IN '
            "('M5','5M','5MIN','5') "
            f'ORDER BY "{tempo}" DESC LIMIT 1'
        )
    else:
        consulta = (
            f'SELECT * FROM indicadores_tecnicos '
            f'ORDER BY "{tempo}" DESC LIMIT 1'
        )

    linha = con.execute(consulta).fetchone()
    con.close()

    if linha is None:
        raise RuntimeError("Nao existem indicadores disponiveis.")

    return dict(linha), colunas


def construir_medidas(ind, colunas):
    medidas = {}
    ema9 = localizar(colunas, ["ema9", "mme9", "ema_9"])
    ema21 = localizar(colunas, ["ema21", "mme21", "ema_21"])
    rsi_col = localizar(colunas, ["rsi14", "rsi_14", "rsi"])
    macd_score = localizar(colunas, ["macd_score", "macd_medida"])

    # EMA: leitura direcional relativa.
    if ema9 and ema21:
        a, b = numero(ind.get(ema9)), numero(ind.get(ema21))
        if a is not None and b is not None:
            base = abs(a) or 1
            medidas["EMA"] = max(-10, min(10, (a - b) / base * 1000))

    # RSI: leitura de força.
    if rsi_col:
        rsi = numero(ind.get(rsi_col))
        if rsi is not None:
            medidas["RSI"] = max(-10, min(10, (rsi - 50) / 5))

    # MACD: utiliza somente medida normalizada que exista no banco.
    if macd_score:
        valor = numero(ind.get(macd_score))
        if valor is not None:
            medidas["MACD"] = max(-10, min(10, valor))

    # Scores adicionais gravados diretamente nos indicadores.
    for c in colunas:
        nome = c.upper()
        if nome.endswith("_SCORE") or nome.endswith("_MEDIDA"):
            valor = numero(ind.get(c))
            if valor is not None:
                medidas[nome] = max(-10, min(10, valor))

    return medidas


def decidir():
    p = carregar_parametros()
    ind, colunas = ler_ultimo_indicador()
    medidas = construir_medidas(ind, colunas)

    pesos = p["pesos"]
    numerador = 0.0
    denominador = 0.0
    utilizados = {}
    ausentes = []

    for nome in p["indicadores"]:
        if nome not in medidas:
            ausentes.append(nome)
            continue

        leitura = medidas[nome]

        # Peso ausente ou invalido recebe 1.
        peso = numero(pesos.get(nome))
        if peso is None or peso <= 0:
            peso = 1.0

        numerador += leitura * peso
        denominador += peso

        utilizados[nome] = {
            "leitura": round(leitura, 4),
            "peso": peso,
            "contribuicao": round(leitura * peso, 4)
        }

    if denominador == 0:
        raise RuntimeError("Nenhum indicador valido para calcular o setup.")

    score = numerador / denominador
    limite = float(p["limite_long"])

    if score >= limite:
        decisao = "LONG"
    elif score <= -limite:
        decisao = "SHORT"
    else:
        decisao = "LATERAL"

    resultado = {
        "data_utc": datetime.now(timezone.utc).isoformat(),
        "decisao": decisao,
        "pontuacao": round(score, 4),
        "limite_long": limite,
        "limite_short": -limite,
        "indicadores_utilizados": utilizados,
        "indicadores_sem_leitura": ausentes,
        "quantidade_indicadores_utilizados": len(utilizados),
        "status_parametros": p["status"]
    }

    os.makedirs("data", exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    decidir()
