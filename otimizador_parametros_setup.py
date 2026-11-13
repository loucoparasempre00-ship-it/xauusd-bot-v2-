
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OTIMIZADOR DE PARAMETROS DO SETUP MATEMATICO

Fonte exclusiva:
    data/graficos.db.gz

Saida:
    data/parametros_setups.json

Nao consulta:
    operacoes.db.gz
    resultados_operacoes.db.gz
    Gemini
"""

import gzip
import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone

DB = "data/graficos.db.gz"
SAIDA = "data/parametros_setups.json"

HORIZONTE = 3
MIN_AMOSTRAS = 100
PESOS_TESTE = [1, 2, 3, 4, 5]
LIMITES_TESTE = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]


def abrir_banco(caminho):
    if not os.path.exists(caminho):
        raise FileNotFoundError(caminho)

    with gzip.open(caminho, "rb") as f:
        conteudo = f.read()

    con = sqlite3.connect(":memory:")
    con.deserialize(conteudo)
    con.row_factory = sqlite3.Row
    return con


def tabelas(con):
    return {
        r["name"]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def colunas(con, tabela):
    return [
        r["name"]
        for r in con.execute(f'PRAGMA table_info("{tabela}")')
    ]


def localizar(lista, candidatos):
    mapa = {x.lower(): x for x in lista}
    for nome in candidatos:
        if nome.lower() in mapa:
            return mapa[nome.lower()]
    return None


def numero(valor):
    try:
        n = float(valor)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def limitar(valor, minimo=-10, maximo=10):
    return max(minimo, min(maximo, valor))


def descobrir_dados(con):
    ts = tabelas(con)

    tabela_ind = next(
        (x for x in ("indicadores_tecnicos", "indicadores")
         if x in ts), None
    )
    tabela_velas = next(
        (x for x in ("candles", "velas", "candles_m5")
         if x in ts), None
    )

    if not tabela_ind or not tabela_velas:
        raise RuntimeError(
            "Nao encontrei as tabelas de indicadores e candles. "
            f"Tabelas encontradas: {sorted(ts)}"
        )

    ci = colunas(con, tabela_ind)
    cv = colunas(con, tabela_velas)

    ti = localizar(ci, [
        "timestamp", "datetime", "time", "data_hora", "data"
    ])
    tv = localizar(cv, [
        "timestamp", "datetime", "time", "data_hora", "data"
    ])
    fechamento = localizar(cv, ["close", "fechamento", "preco_fechamento"])
    timeframe = localizar(ci, ["timeframe", "periodo", "intervalo"])

    if not ti or not tv or not fechamento:
        raise RuntimeError(
            "Colunas essenciais nao identificadas. "
            f"Indicadores: {ci}; candles: {cv}"
        )

    indicadores = [
        dict(r)
        for r in con.execute(f'SELECT * FROM "{tabela_ind}"')
    ]
    velas = [
        dict(r)
        for r in con.execute(f'SELECT * FROM "{tabela_velas}"')
    ]

    if timeframe:
        indicadores = [
            r for r in indicadores
            if str(r.get(timeframe, "")).upper() in
            ("M5", "5MIN", "5M", "5")
        ]

    velas.sort(key=lambda r: str(r.get(tv, "")))
    return indicadores, velas, ti, tv, fechamento


def construir_medidas(indicador, colunas_ind):
    """
    Cada indicador tem uma leitura independente entre -10 e +10.
    Peso ausente sera 1, mas a leitura nunca sera apagada.
    """

    resultado = {}

    # EMA 9 e EMA 21: diferenca relativa ao preco.
    ema9 = localizar(colunas_ind, ["ema9", "mme9", "ema_9"])
    ema21 = localizar(colunas_ind, ["ema21", "mme21", "ema_21"])
    close = numero(indicador.get(
        localizar(colunas_ind, ["close", "fechamento", "preco"])
    )) if localizar(
        colunas_ind, ["close", "fechamento", "preco"]
    ) else None

    if ema9 and ema21:
        a = numero(indicador.get(ema9))
        b = numero(indicador.get(ema21))
        base = close or (abs(a) if a is not None else None)
        if a is not None and b is not None and base:
            resultado["EMA"] = limitar(((a - b) / base) * 1000)

    # RSI: 50 e neutro; valores maiores favorecem alta.
    rsi_col = localizar(colunas_ind, ["rsi14", "rsi_14", "rsi"])
    if rsi_col:
        rsi = numero(indicador.get(rsi_col))
        if rsi is not None:
            resultado["RSI"] = limitar((rsi - 50) / 5)

    # MACD: normalizacao estatistica feita depois, no historico.
    macd_col = localizar(colunas_ind, ["macd", "macd_line"])
    if macd_col:
        macd = numero(indicador.get(macd_col))
        if macd is not None:
            resultado["MACD_BRUTO"] = macd

    # Indicadores que ja venham expressos como score -10 a +10.
    for c in colunas_ind:
        nome = c.lower()
        if nome.endswith("_score") or nome.endswith("_medida"):
            valor = numero(indicador.get(c))
            if valor is not None:
                resultado[c.upper()] = limitar(valor)

    return resultado


def normalizar_macd(registros):
    valores = [
        r["medidas"]["MACD_BRUTO"]
        for r in registros
        if "MACD_BRUTO" in r["medidas"]
    ]
    if not valores:
        return

    mediana = statistics.median(valores)
    desvios = [abs(x - mediana) for x in valores]
    escala = statistics.median(desvios) or 1.0

    for r in registros:
        if "MACD_BRUTO" in r["medidas"]:
            bruto = r["medidas"].pop("MACD_BRUTO")
            r["medidas"]["MACD"] = limitar(
                (bruto - mediana) / (escala * 1.4826)
            )


def percentil(valores, p):
    v = sorted(valores)
    if not v:
        return 0
    pos = (len(v) - 1) * p
    baixo = int(pos)
    alto = min(baixo + 1, len(v) - 1)
    return v[baixo] + (v[alto] - v[baixo]) * (pos - baixo)


def criar_amostras(indicadores, velas, ti, tv, fechamento, ci):
    mapa_velas = {
        str(v.get(tv)): numero(v.get(fechamento))
        for v in velas
    }
    precos = [
        (str(v.get(tv)), numero(v.get(fechamento)))
        for v in velas
    ]
    precos = [(t, p) for t, p in precos if p is not None]

    indice = {t: i for i, (t, _) in enumerate(precos)}
    registros = []

    for ind in indicadores:
        tempo = str(ind.get(ti))
        if tempo not in indice:
            continue

        i = indice[tempo]
        futuro = i + HORIZONTE
        if futuro >= len(precos):
            continue

        atual = precos[i][1]
        depois = precos[futuro][1]
        if not atual:
            continue

        medidas = construir_medidas(ind, ci)
        if medidas:
            registros.append({
                "tempo": tempo,
                "medidas": medidas,
                "retorno": (depois - atual) / atual * 100
            })

    normalizar_macd(registros)
    return registros


def pontuar(medidas, pesos):
    numerador = 0.0
    denominador = 0.0

    for nome, leitura in medidas.items():
        peso = numero(pesos.get(nome, 1)) or 1
        if peso <= 0:
            peso = 1
        numerador += leitura * peso
        denominador += peso

    return numerador / denominador if denominador else 0.0


def avaliar(registros, pesos, limite):
    acertos = 0
    decididos = 0

    for r in registros:
        score = pontuar(r["medidas"], pesos)
        retorno = r["retorno"]

        if score >= limite:
            decisao = "LONG"
            resultado = retorno > 0
        elif score <= -limite:
            decisao = "SHORT"
            resultado = retorno < 0
        else:
            decisao = "LATERAL"
            continue

        decididos += 1
        if resultado:
            acertos += 1

    taxa = acertos / decididos if decididos else 0
    cobertura = decididos / len(registros) if registros else 0

    return {
        "taxa_direcao": taxa,
        "cobertura": cobertura,
        "decididos": decididos,
        "amostras": len(registros)
    }


def otimizar():
    con = abrir_banco(DB)
    indicadores, velas, ti, tv, fechamento = descobrir_dados(con)
    ci = colunas(con, "indicadores_tecnicos")

    registros = criar_amostras(
        indicadores, velas, ti, tv, fechamento, ci
    )
    con.close()

    if len(registros) < MIN_AMOSTRAS:
        raise RuntimeError(
            f"Amostras insuficientes: {len(registros)}. "
            f"Minimo exigido: {MIN_AMOSTRAS}."
        )

    nomes = sorted({
        nome
        for r in registros
        for nome in r["medidas"]
    })

    # Separacao cronologica: ajuste nos 70% iniciais.
    corte = int(len(registros) * 0.70)
    treino = registros[:corte]

    melhor = None

    # Busca individual dos pesos para evitar explosao combinatoria.
    pesos = {nome: 1 for nome in nomes}

    for nome in nomes:
        melhor_local = None
        for peso in PESOS_TESTE:
            teste = dict(pesos)
            teste[nome] = peso

            for limite in LIMITES_TESTE:
                resultado = avaliar(treino, teste, limite)
                if resultado["decididos"] < 20:
                    continue

                # Prioriza taxa direcional, mas penaliza baixa cobertura.
                objetivo = (
                    resultado["taxa_direcao"] *
                    math.sqrt(resultado["cobertura"])
                )

                if melhor_local is None or objetivo > melhor_local[0]:
                    melhor_local = (objetivo, peso, limite)

        if melhor_local:
            pesos[nome] = melhor_local[1]
            limite_escolhido = melhor_local[2]

    if not nomes:
        raise RuntimeError("Nenhum indicador valido foi encontrado.")

    if "limite_escolhido" not in locals():
        limite_escolhido = 2.0

    # Teste final de treino com os pesos encontrados.
    melhor_limite = limite_escolhido
    melhor_resultado = avaliar(treino, pesos, melhor_limite)

    parametros = {
        "versao": 1,
        "origem": "graficos.db.gz",
        "criado_utc": datetime.now(timezone.utc).isoformat(),
        "indicadores": nomes,
        "pesos": pesos,
        "limite_long": melhor_limite,
        "limite_short": -melhor_limite,
        "horizonte_velas": HORIZONTE,
        "amostras_treino": len(treino),
        "status": "PENDENTE_VALIDACAO",
        "resultado_treino": melhor_resultado
    }

    os.makedirs("data", exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(parametros, f, ensure_ascii=False, indent=2)

    print(json.dumps(parametros, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    otimizar()
