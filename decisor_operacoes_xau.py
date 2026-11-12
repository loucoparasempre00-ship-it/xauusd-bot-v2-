
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DECISOR DE OPERACOES XAUUSD

Recebe:
- Operacoes ativas da Recepcao.
- Probabilidades gerais do Analisador.
- Historico M5 do Tabuleiro.

Registra uma decisao para cada codigo oficial.

Preserva o motor matematico existente.
Nao executa ordens.
Nao grava nos bancos oficiais.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from diretor_operacional_xau import calcular_decisao
from analisador_setup_xau import (
    carregar_dados,
    preparar_velas,
    calcular_caracteristicas,
    calcular_atr
)


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_RECEPCAO = DATA / "entrada_recepcao_operacoes.json"
ARQUIVO_PROBABILIDADE = DATA / "probabilidade_direcional_xau.json"
ARQUIVO_SAIDA = DATA / "decisoes_operacoes_xau.json"


def carregar_json(caminho, padrao=None):
    if padrao is None:
        padrao = {}

    if not caminho.exists():
        return padrao

    with caminho.open("r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(caminho)


def numero(valor):
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return None


def primeiro_valor(operacao, nomes):
    for nome in nomes:
        valor = operacao.get(nome)

        if valor not in (None, ""):
            return valor

    return None


def normalizar_operacao(codigo, original):
    operacao = dict(original)

    operacao["codigo_operacao"] = codigo

    operacao["direcao"] = str(
        primeiro_valor(
            operacao,
            ["direcao", "tipo", "lado"]
        ) or ""
    ).upper()

    operacao["entrada_real"] = numero(
        primeiro_valor(
            operacao,
            ["entrada_real", "preco_entrada", "entrada"]
        )
    )

    operacao["stop"] = numero(
        primeiro_valor(
            operacao,
            ["stop", "stop_atual", "stop_loss"]
        )
    )

    operacao["stop_original"] = numero(
        primeiro_valor(
            operacao,
            ["stop_original", "stop_inicial", "stop_loss"]
        )
    )

    operacao["alvo"] = numero(
        primeiro_valor(
            operacao,
            ["alvo", "take_profit", "gain"]
        )
    )

    return operacao


def obter_operacoes():
    dados = carregar_json(ARQUIVO_RECEPCAO, {})

    operacoes = dados.get("operacoes_ativas", {})

    if isinstance(operacoes, list):
        resultado = {}

        for item in operacoes:
            if not isinstance(item, dict):
                continue

            codigo = (
                item.get("codigo_operacao")
                or item.get("codigo")
            )

            if codigo:
                resultado[str(codigo)] = item

        return resultado

    if isinstance(operacoes, dict):
        return operacoes

    return {}


def obter_indicadores():
    tabuleiro, _, _ = carregar_dados()

    velas = preparar_velas(tabuleiro)

    caracteristicas, atr = calcular_caracteristicas(
        velas
    )

    if not velas:
        raise RuntimeError("Sem velas M5.")

    fechamentos = [
        vela["close"] for vela in velas
    ]

    from analisador_setup_xau import calcular_ema, calcular_rsi

    ema9 = calcular_ema(fechamentos, 9)
    ema21 = calcular_ema(fechamentos, 21)
    rsi = calcular_rsi(fechamentos, 14)

    ultima = velas[-1]

    return {
        "vela": ultima,
        "ema9": ema9[-1],
        "ema21": ema21[-1],
        "rsi": rsi[-1],
        "atr": atr[-1],
        "candle_utc": ultima["timestamp"]
    }


def executar():
    print("=== DECISOR DE OPERACOES XAUUSD ===")

    operacoes = obter_operacoes()

    probabilidades = carregar_json(
        ARQUIVO_PROBABILIDADE,
        {}
    )

    indicadores = obter_indicadores()

    p_subir = numero(
        probabilidades.get("probabilidade_subir")
    )

    p_cair = numero(
        probabilidades.get("probabilidade_cair")
    )

    if p_subir is None or p_cair is None:
        raise RuntimeError(
            "Probabilidades do Analisador ausentes."
        )

    decisoes = []

    for codigo, original in operacoes.items():

        try:
            operacao = normalizar_operacao(
                codigo,
                original
            )

            decisao = calcular_decisao(
                operacao=operacao,
                vela=indicadores["vela"],
                ema9=indicadores["ema9"],
                ema21=indicadores["ema21"],
                valor_rsi=indicadores["rsi"],
                atr=indicadores["atr"]
            )

            registro = {
                "codigo_operacao": codigo,
                "direcao_operacao": operacao["direcao"],
                "decisao": decisao["acao"],
                "motivo": decisao["motivo"],
                "preco_referencia": decisao["preco_referencia"],
                "stop_recomendado": decisao["novo_stop"],
                "resultado_r": decisao.get("resultado_r"),
                "probabilidade_subir": p_subir,
                "probabilidade_cair": p_cair,
                "horizonte_probabilidade_minutos": (
                    probabilidades.get("horizonte_minutos")
                ),
                "candle_utc": indicadores["candle_utc"],
                "registrado_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "confirmacao_manual": (
                    decisao["acao"] in (
                        "MUDA_STOP",
                        "REDUZ_RISCO",
                        "FECHA"
                    )
                ),
                "execucao_automatica": False
            }

            decisoes.append(registro)

        except Exception as erro:
            decisoes.append({
                "codigo_operacao": codigo,
                "decisao": "ERRO_ANALISE",
                "motivo": str(erro),
                "probabilidade_subir": p_subir,
                "probabilidade_cair": p_cair,
                "registrado_utc": datetime.now(
                    timezone.utc
                ).isoformat()
            })

    resultado = {
        "modulo": "decisor_operacoes_xau",
        "executado_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "probabilidade_geral": {
            "subir": p_subir,
            "cair": p_cair
        },
        "quantidade_operacoes": len(decisoes),
        "decisoes": decisoes
    }

    salvar_json(ARQUIVO_SAIDA, resultado)

    print("Operacoes analisadas:", len(decisoes))
    print("Saida:", ARQUIVO_SAIDA)


if __name__ == "__main__":
    executar()
