#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLASSIFICADOR E AUDITOR GEMINI XAUUSD

TRANSFERENCIA:
SINAIS_RECEBIDOS -> CLASSIFICACAO_GEMINI

BANCO OFICIAL:
data/sinais.db.gz

HISTORICO:
RANKING_MELHORES

- Consulta o historico real das operacoes.
- Nao utiliza JSON antigo de ranking.
- Nao confunde ranking de modelos com ranking de operacoes.
- Nao inventa entrada, stop ou alvo.
- Preserva todos os campos originais do sinal.
- Registra nota, justificativa, relatorio e CSV.
- Remove da origem somente apos confirmar a gravacao.
- Nao envia Telegram.
- Nao executa ordens.
"""

import os
import sys
import json
import csv
import io
import urllib.request
import urllib.error
import sqlite3

from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


ORIGEM = "SINAIS_RECEBIDOS"
DESTINO = "CLASSIFICACAO_GEMINI"
RANKING = "RANKING_MELHORES"

ARQUIVO_MODELO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "modelo_gemini.json",
)

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_BASE = (
    "https://googleapis.com"
)


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def ident(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def nomes_colunas(con, tabela):
    return {
        r["name"]
        for r in con.execute(
            f"PRAGMA table_info({ident(tabela)})"
        ).fetchall()
    }


def garantir_coluna(con, tabela, nome, tipo):
    if nome not in nomes_colunas(con, tabela):
        con.execute(
            f"ALTER TABLE {ident(tabela)} "
            f"ADD COLUMN {ident(nome)} {tipo}"
        )


def carregar_modelo():
    if not os.path.isfile(ARQUIVO_MODELO):
        raise RuntimeError(
            "Arquivo data/modelo_gemini.json nao encontrado. "
            "Disponibilize o artefato do Testador Gemini."
        )

    with open(
        ARQUIVO_MODELO,
        "r",
        encoding="utf-8",
    ) as arquivo:
        dados = json.load(arquivo)

    status = str(dados.get("status", "")).lower()

    if status not in (
        "pronto",
        "pronto_com_aviso_api",
    ):
        raise RuntimeError(
            "Testador Gemini nao esta pronto. Status: "
            + status
        )

    modelo = dados.get("modelo_escolhido")

    if not modelo:
        raise RuntimeError(
            "Modelo escolhido nao informado pelo Testador."
        )

    return modelo


def criar_tabela(con):
    tabelas = {
        r["name"]
        for r in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if ORIGEM not in tabelas:
        raise RuntimeError(
            "Tabela SINAIS_RECEBIDOS nao encontrada."
        )

    origem = con.execute(
        f"PRAGMA table_info({ident(ORIGEM)})"
    ).fetchall()

    nomes_origem = {r["name"] for r in origem}

    obrigatorias = {"codigo", "origem_id"}

    if not obrigatorias.issubset(nomes_origem):
        raise RuntimeError(
            "SINAIS_RECEBIDOS precisa conter codigo e origem_id."
        )

    definicoes = [
        '"codigo" TEXT PRIMARY KEY',
        '"origem_id" INTEGER NOT NULL UNIQUE',
        '"classificacao" TEXT NOT NULL',
        '"justificativa" TEXT NOT NULL',
        '"modelo" TEXT NOT NULL',
        '"classificado_utc" TEXT NOT NULL',
        '"resposta_json" TEXT NOT NULL',
        '"nota_auditoria" REAL',
        '"relatorio_gemini" TEXT',
        '"csv_linha" TEXT',
    ]

    for coluna in origem:
        nome = coluna["name"]

        if nome in (
            "codigo",
            "origem_id",
            "transferido_utc",
        ):
            continue

        tipo = coluna["type"] or "TEXT"

        definicoes.append(
            f"{ident(nome)} {tipo}"
        )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ident(DESTINO)}
        ({", ".join(definicoes)})
        """
    )

    garantir_coluna(
        con, DESTINO, "nota_auditoria", "REAL"
    )
    garantir_coluna(
        con, DESTINO, "relatorio_gemini", "TEXT"
    )
    garantir_coluna(
        con, DESTINO, "csv_linha", "TEXT"
    )

    destino = nomes_colunas(con, DESTINO)

    necessarias = {
        "codigo",
        "origem_id",
        "classificacao",
        "justificativa",
        "modelo",
        "classificado_utc",
        "resposta_json",
    }

    faltando = necessarias - destino

    if faltando:
        raise RuntimeError(
            "Estrutura incompativel em CLASSIFICACAO_GEMINI: "
            + ", ".join(sorted(faltando))
        )

    con.commit()

    return origem


def numero(valor):
    if valor is None:
        return None

    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def consultar_historico(con):
    """
    Consulta somente operacoes com resultado conhecido.
    Operacoes PENDENTES nao entram nas estatisticas.
    """

    tabelas = {
        r["name"]
        for r in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if RANKING not in tabelas:
        return {
            "disponibilidade": "TABELA_AINDA_NAO_EXISTE",
            "total_encerradas": 0,
            "total_pendentes": 0,
            "estatisticas": {},
            "melhores_resultados": [],
            "piores_resultados": [],
        }

    colunas = nomes_colunas(con, RANKING)

    obrigatorias = {
        "codigo",
        "classificacao_gemini",
        "resultado_usdt",
        "status_resultado",
    }

    if not obrigatorias.issubset(colunas):
        return {
            "disponibilidade": "ESTRUTURA_INCOMPLETA",
            "colunas_encontradas": sorted(colunas),
            "total_encerradas": 0,
            "total_pendentes": 0,
            "estatisticas": {},
            "melhores_resultados": [],
            "piores_resultados": [],
        }

    registros = con.execute(
        f"SELECT * FROM {ident(RANKING)}"
    ).fetchall()

    encerradas = []
    pendentes = 0

    for linha in registros:
        r = dict(linha)
        resultado = numero(r.get("resultado_usdt"))
        status = str(
            r.get("status_resultado") or ""
        ).upper()

        if resultado is None or status == "PENDENTE":
            pendentes += 1
            continue

        encerradas.append(r)

    estatisticas = {}

    for classificacao in ("APPROVED", "REJECTED"):
        grupo = [
            r for r in encerradas
            if str(
                r.get("classificacao_gemini", "")
            ).upper() == classificacao
        ]

        valores = [
            numero(r.get("resultado_usdt"))
            for r in grupo
        ]

        valores = [v for v in valores if v is not None]

        positivos = sum(1 for v in valores if v > 0)
        negativos = sum(1 for v in valores if v < 0)

        estatisticas[classificacao] = {
            "total_encerradas": len(valores),
            "positivas": positivos,
            "negativas": negativos,
            "resultado_medio_usdt": (
                round(sum(valores) / len(valores), 4)
                if valores else None
            ),
            "resultado_total_usdt": round(
                sum(valores), 4
            ),
            "taxa_positiva_percentual": (
                round(positivos / len(valores) * 100, 2)
                if valores else None
            ),
        }

    ordenadas = sorted(
        encerradas,
        key=lambda r: (
            numero(r.get("resultado_usdt"))
            if numero(r.get("resultado_usdt")) is not None
            else 0
        ),
        reverse=True,
    )

    def resumir(r):
        campos = (
            "codigo",
            "classificacao_gemini",
            "direcao",
            "entrada",
            "stop_loss",
            "take_profit",
            "resultado_usdt",
            "status_resultado",
        )

        return {
            campo: r.get(campo)
            for campo in campos
            if campo in r
        }

    return {
        "disponibilidade": "DADOS_REAIS_CONSULTADOS",
        "total_encerradas": len(encerradas),
        "total_pendentes": pendentes,
        "estatisticas": estatisticas,
        "melhores_resultados": [
            resumir(r) for r in ordenadas[:10]
        ],
        "piores_resultados": [
            resumir(r) for r in ordenadas[-10:]
        ],
    }


def montar_prompt(sinal, historico):
    dados_sinal = dict(sinal)

    dados_sinal.pop("transferido_utc", None)

    return f"""
Voce e o AUDITOR GEMINI de sinais XAUUSD.

Sua tarefa e avaliar a qualidade do sinal recebido,
utilizando os dados reais fornecidos.

REGRAS DE PONTUACAO:

1. MTF e medias: 25 pontos.
2. Bordas e estrutura de preco: 25 pontos.
3. RSI e MACD: 15 pontos.
4. Probabilidade estatistica: 20 pontos.
5. Ranking historico de operacoes: 15 pontos.

Total: 100 pontos.

REGRAS DE DECISAO:

80 a 100 pontos: APPROVED.
65 a 79 pontos: APPROVED com lote reduzido (condicao_lote: REDUZIDO).
Abaixo de 65 pontos: REJECTED.

IMPORTANTE:
No campo "classificacao", use apenas "APPROVED" ou "REJECTED".
Se a nota ficar entre 65 e 79, preencha "classificacao": "APPROVED"
e "condicao_lote": "REDUZIDO".

REGRAS DE RISCO:

- Avalie o stop pela estrutura disponivel.
- Avalie TP1 com relacao risco/retorno de pelo menos 1:1.5.
- Avalie TP2 quando houver dados suficientes para
  relacao risco/retorno de pelo menos 1:2.
- Se a relacao estrutural disponivel for inferior a 1:1.2,
  considere veto.
- Nao invente entrada, stop, alvo ou cotacao.
- Nao altere os valores originais do sinal.
- Se nao houver dados suficientes para calcular risco,
  informe que o calculo nao pode ser confirmado.
- Nunca invente probabilidade ou historico ausente.

REGRAS HISTORICAS:

