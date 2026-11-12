
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DECISOR FINAL DO NOVO GERADOR DE SINAL XAUUSD

Entradas:
- data/sinal_matematico.json
- data/avaliacao_gemini.json
- data/candles/xauusd_m5_fechados.csv

Saida:
- data/decisao_final.json

Nao envia Telegram e nao executa ordens.
"""

import csv
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ARQUIVO_SETUP = DATA / "sinal_matematico.json"
ARQUIVO_GEMINI = DATA / "avaliacao_gemini.json"
ARQUIVO_CANDLES = DATA / "candles" / "xauusd_m5_fechados.csv"
ARQUIVO_SAIDA = DATA / "decisao_final.json"

# PARAMETROS VALIDADOS
PESO_TECNICO = 75.0
PESO_GEMINI = 25.0
LIMIAR = 40.0
MULTIPLICADOR_ATR = 1.5
RR = 1.5


def ler_json(caminho):
    if not caminho.is_file():
        raise FileNotFoundError(
            f"Arquivo obrigatorio ausente: {caminho}"
        )

    try:
        dados = json.loads(
            caminho.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as erro:
        raise ValueError(
            f"Erro ao ler {caminho.name}: {erro}"
        ) from erro

    if not isinstance(dados, dict):
        raise ValueError(
            f"{caminho.name} deve conter um objeto JSON."
        )

    return dados


def normalizar_coluna(nome):
    return "".join(
        c.lower()
        for c in str(nome).strip()
        if c.isalnum()
    )


def localizar_coluna(colunas, *nomes):
    for nome in nomes:
        chave = normalizar_coluna(nome)

        if chave in colunas:
            return colunas[chave]

    raise ValueError(
        "Coluna ausente no CSV: " + ", ".join(nomes)
    )


def numero_candle(linha, campo, indice):
    try:
        valor = float(
            str(linha[campo]).strip().replace(",", ".")
        )
    except (KeyError, TypeError, ValueError) as erro:
        raise ValueError(
            f"Valor invalido em {campo}, candle {indice}."
        ) from erro

    if not math.isfinite(valor):
        raise ValueError(
            f"Valor nao finito em {campo}, candle {indice}."
        )

    return valor


def calcular_atr14():
    if not ARQUIVO_CANDLES.is_file():
        raise FileNotFoundError(
            f"Candles fechados ausentes: {ARQUIVO_CANDLES}"
        )

    with ARQUIVO_CANDLES.open(
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo)

        colunas = {
            normalizar_coluna(nome): nome
            for nome in (leitor.fieldnames or [])
        }

        campo_maxima = localizar_coluna(
            colunas,
            "high", "max", "maximum",
            "maxima", "máxima"
        )

        campo_minima = localizar_coluna(
            colunas,
            "low", "min", "minimum",
            "minima", "mínima"
        )

        campo_fechamento = localizar_coluna(
            colunas,
            "close", "fechamento",
            "last", "preco_fechamento"
        )

        linhas = list(leitor)

    if len(linhas) < 15:
        raise ValueError(
            "Historico insuficiente para calcular ATR14."
        )

    candles = []

    for indice, linha in enumerate(linhas):
        maxima = numero_candle(
            linha, campo_maxima, indice
        )
        minima = numero_candle(
            linha, campo_minima, indice
        )
        fechamento = numero_candle(
            linha, campo_fechamento, indice
        )

        if maxima < minima:
            raise ValueError(
                f"Maxima menor que minima no candle {indice}."
            )

        if maxima <= 0 or minima <= 0 or fechamento <= 0:
            raise ValueError(
                f"Preco nao positivo no candle {indice}."
            )

        candles.append(
            (maxima, minima, fechamento)
        )

    true_ranges = []

    # Mantem a mesma janela do Gerador atual:
    # os ultimos 14 True Ranges.
    for indice in range(len(candles) - 14, len(candles)):
        maxima, minima, _ = candles[indice]
        fechamento_anterior = candles[indice - 1][2]

        true_ranges.append(max(
            maxima - minima,
            abs(maxima - fechamento_anterior),
            abs(minima - fechamento_anterior),
        ))

    atr = sum(true_ranges) / 14
    entrada = candles[-1][2]

    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("ATR14 invalido.")

    return atr, entrada


def calcular_decisao(setup, ia, atr, entrada):
    try:
        compra = int(setup["pontuacao_compra"])
        venda = int(setup["pontuacao_venda"])
    except (KeyError, TypeError, ValueError) as erro:
        raise ValueError(
            "Pontuacoes matematicas ausentes ou invalidas."
        ) from erro

    if not (0 <= compra <= 6 and 0 <= venda <= 6):
        raise ValueError(
            "Pontuacoes matematicas fora do intervalo 0 a 6."
        )

    gemini_disponivel = (
        ia.get("status") == "disponivel"
    )

    if gemini_disponivel:
        decisao_ia = ia.get("decisao")

        if decisao_ia not in (
            "COMPRA", "VENDA", "SEM_SINAL"
        ):
            raise ValueError("Decisao Gemini invalida.")

        try:
            confianca = int(ia.get("confianca", 0))
        except (TypeError, ValueError) as erro:
            raise ValueError(
                "Confianca Gemini invalida."
            ) from erro

        if not 0 <= confianca <= 100:
            raise ValueError(
                "Confianca Gemini fora do intervalo 0 a 100."
            )

        peso_tecnico = PESO_TECNICO
        peso_gemini = PESO_GEMINI

    else:
        decisao_ia = ia.get("decisao", "SEM_SINAL")
        confianca = 0
        peso_tecnico = 100.0
        peso_gemini = 0.0

    contribuicao_compra = 0.0
    contribuicao_venda = 0.0

    if gemini_disponivel:
        if decisao_ia == "COMPRA":
            contribuicao_compra = (
                peso_gemini * confianca / 100
            )

        elif decisao_ia == "VENDA":
            contribuicao_venda = (
                peso_gemini * confianca / 100
            )

    score_compra = round(
        (compra / 6) * peso_tecnico
        + contribuicao_compra,
        2
    )

    score_venda = round(
        (venda / 6) * peso_tecnico
        + contribuicao_venda,
        2
    )

    if not gemini_disponivel:
        # Contingencia original: sem Gemini,
        # exige direcao valida do setup matematico.
        direcao_setup = setup.get("direcao")

        if direcao_setup == "COMPRA":
            lado = "COMPRA"
            score = score_compra

        elif direcao_setup == "VENDA":
            lado = "VENDA"
            score = score_venda

        else:
            lado = "SEM_SINAL"
            score = max(score_compra, score_venda)

        if score < LIMIAR:
            lado = "SEM_SINAL"

    elif decisao_ia == "SEM_SINAL":
        lado = "SEM_SINAL"
        score = max(score_compra, score_venda)

    elif (
        score_compra >= LIMIAR
        and score_compra > score_venda
    ):
        lado = "COMPRA"
        score = score_compra

    elif (
        score_venda >= LIMIAR
        and score_venda > score_compra
    ):
        lado = "VENDA"
        score = score_venda

    else:
        lado = "SEM_SINAL"
        score = max(score_compra, score_venda)

    distancia_stop = atr * MULTIPLICADOR_ATR
    distancia_alvo = distancia_stop * RR

    stop = None
    alvo = None

    if lado == "COMPRA":
        stop = entrada - distancia_stop
        alvo = entrada + distancia_alvo

    elif lado == "VENDA":
        stop = entrada + distancia_stop
        alvo = entrada - distancia_alvo

    return {
        "ativo": "XAUUSD",
        "timeframe": "M5",
        "candle_utc": setup.get("candle_utc"),
        "decisao_final": lado,
        "pontuacao_final": round(score, 2),
        "pontuacao_compra": score_compra,
        "pontuacao_venda": score_venda,
        "setup_matematico": setup,
        "avaliacao_gemini": ia,
        "modo_operacao": (
            "NORMAL"
            if gemini_disponivel
            else "CONTINGENCIA_SEM_GEMINI"
        ),
        "pesos": {
            "tecnico": peso_tecnico,
            "gemini": peso_gemini,
        },
        "limiar": LIMIAR,
        "atr14": round(atr, 5),
        "multiplicador_atr_stop": MULTIPLICADOR_ATR,
        "rr": RR,
        "entrada": (
            round(entrada, 3)
            if stop is not None else None
        ),
        "stop_loss": (
            round(stop, 3)
            if stop is not None else None
        ),
        "take_profit": (
            round(alvo, 3)
            if alvo is not None else None
        ),
        "distancia_stop": round(distancia_stop, 3),
        "distancia_alvo": round(distancia_alvo, 3),
        "envio_telegram": False,
        "execucao_ordens": False,
    }


def executar():
    setup = ler_json(ARQUIVO_SETUP)
    ia = ler_json(ARQUIVO_GEMINI)

    if not setup.get("candle_utc"):
        raise ValueError(
            "Candle UTC ausente no setup matematico."
        )

    atr, entrada = calcular_atr14()

    resultado = calcular_decisao(
        setup,
        ia,
        atr,
        entrada
    )

    DATA.mkdir(parents=True, exist_ok=True)

    ARQUIVO_SAIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("DECISOR FINAL DO GERADOR XAUUSD")
    print("Modo:", resultado["modo_operacao"])
    print("Pesos:", resultado["pesos"])
    print("Setup matematico:", setup.get("direcao"))
    print("Decisao Gemini:", ia.get("decisao", "SEM_SINAL"))
    print("Decisao final:", resultado["decisao_final"])
    print("Pontuacao:", resultado["pontuacao_final"])
    print("Entrada:", resultado["entrada"])
    print("Stop:", resultado["stop_loss"])
    print("Alvo:", resultado["take_profit"])
    print("Arquivo:", ARQUIVO_SAIDA)
    print("Telegram desativado neste modulo.")
    print("Execucao de ordens desativada.")

    return resultado


if __name__ == "__main__":
    executar()
