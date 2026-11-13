#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLASSIFICADOR GEMINI XAUUSD

TRANSFERENCIA:
SINAIS_RECEBIDOS -> CLASSIFICACAO_GEMINI

- Mantem o codigo OP original.
- Envia cada sinal ao modelo selecionado pelo Testador Gemini.
- Registra APPROVED ou REJECTED.
- Guarda justificativa, modelo e resposta.
- Remove da origem somente depois de confirmar a gravacao.
- Se houver falha na API, preserva o sinal na origem.
- Nao envia Telegram.
- Nao executa ordens.
- Nao calcula ranking.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


ORIGEM = "SINAIS_RECEBIDOS"
DESTINO = "CLASSIFICACAO_GEMINI"

ARQUIVO_MODELO = (
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data",
        "modelo_gemini.json",
    )
)

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_BASE = (
    "https://generativelanguage.googleapis.com/v1beta"
)


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def identificador(nome):
    return '"' + str(nome).replace('"', '""') + '"'


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

    status = str(
        dados.get("status", "")
    ).lower()

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
            "O Testador Gemini nao informou o modelo escolhido."
        )

    return modelo


def requisicao_gemini(modelo, sinal):
    if not GEMINI_KEY:
        raise RuntimeError(
            "Secret GEMINI_API_KEY ausente."
        )

    dados_sinal = dict(sinal)

    # Dados administrativos nao precisam entrar na avaliacao.
    dados_sinal.pop("transferido_utc", None)

    prompt = f"""
Voce e o avaliador de sinais do sistema XAUUSD.

Sua tarefa e avaliar o sinal recebido e decidir entre:

APPROVED = sinal aprovado para a proxima etapa.
REJECTED = sinal reprovado nesta avaliacao.

Analise a direcao, a pontuacao e os indicadores disponiveis.
Considere o contexto conjunto dos dados, sem exigir que
todos os indicadores e tempos graficos estejam perfeitamente
alinhados.

Nao invente cotacoes, noticias, indicadores, entrada,
stop loss ou take profit que nao estejam nos dados.

Se os dados forem insuficientes para sustentar a decisao,
classifique como REJECTED e explique a insuficiencia.

Nao altere os valores recebidos.
Nao crie outro codigo.
Nao execute operacoes.

Responda exclusivamente em JSON valido:

{{
  "classificacao": "APPROVED ou REJECTED",
  "justificativa": "Motivo objetivo da decisao"
}}

DADOS DO SINAL:
{json.dumps(dados_sinal, ensure_ascii=False, default=str)}
"""

    corpo = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 600,
        },
    }

    url = (
        f"{GEMINI_BASE}/{modelo}:generateContent"
    )

    requisicao = urllib.request.Request(
        url,
        data=json.dumps(
            corpo,
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=60,
        ) as resposta:
            retorno = json.loads(
                resposta.read().decode("utf-8")
            )

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8",
            "replace",
        )[:1000]

        raise RuntimeError(
            f"Gemini HTTP {erro.code}: {detalhe}"
        )

    partes = (
        retorno.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )

    texto = "".join(
        parte.get("text", "")
        for parte in partes
    ).strip()

    if not texto:
        raise RuntimeError(
            "Gemini retornou resposta vazia."
        )

    # Remove cercas de codigo, caso o modelo as inclua.
    if texto.startswith("```"):
        texto = texto.replace("```json", "", 1)
        texto = texto.replace("```", "").strip()

    try:
        resultado = json.loads(texto)
    except json.JSONDecodeError:
        raise RuntimeError(
            "Gemini nao retornou JSON valido: "
            + texto[:500]
        )

    classificacao = str(
        resultado.get("classificacao", "")
    ).upper().strip()

    if classificacao not in (
        "APPROVED",
        "REJECTED",
    ):
        raise RuntimeError(
            "Classificacao retornada pelo Gemini e invalida."
        )

    justificativa = str(
        resultado.get("justificativa", "")
    ).strip()

    if not justificativa:
        raise RuntimeError(
            "Gemini nao informou a justificativa."
        )

    return {
        "classificacao": classificacao,
        "justificativa": justificativa[:3000],
        "resposta_json": json.dumps(
            retorno,
            ensure_ascii=False,
        ),
    }


def criar_tabela(con):
    tabelas = {
        linha["name"]
        for linha in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if ORIGEM not in tabelas:
        raise RuntimeError(
            "Tabela SINAIS_RECEBIDOS nao encontrada."
        )

    colunas_origem = con.execute(
        f'PRAGMA table_info({identificador(ORIGEM)})'
    ).fetchall()

    nomes_origem = {
        coluna["name"]
        for coluna in colunas_origem
    }

    obrigatorias = {
        "codigo",
        "origem_id",
    }

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
    ]

    # Preserva todos os campos do sinal.
    for coluna in colunas_origem:
        nome = coluna["name"]

        if nome in (
            "codigo",
            "origem_id",
            "transferido_utc",
        ):
            continue

        tipo = coluna["type"] or "TEXT"

        definicoes.append(
            f'{identificador(nome)} {tipo}'
        )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS
        {identificador(DESTINO)}
        ({", ".join(definicoes)})
        """
    )

    colunas_destino = {
        linha["name"]
        for linha in con.execute(
            f'PRAGMA table_info({identificador(DESTINO)})'
        ).fetchall()
    }

    necessarias = {
        "codigo",
        "origem_id",
        "classificacao",
        "justificativa",
        "modelo",
        "classificado_utc",
        "resposta_json",
    }

    necessarias.update(
        nome
        for nome in nomes_origem
        if nome not in (
            "codigo",
            "origem_id",
            "transferido_utc",
        )
    )

    faltando = necessarias - colunas_destino

    if faltando:
        raise RuntimeError(
            "Tabela CLASSIFICACAO_GEMINI possui estrutura "
            "incompativel. Colunas ausentes: "
            + ", ".join(sorted(faltando))
        )

    con.commit()

    return colunas_origem


def transferir_classificacao(
    con,
    sinal,
    resultado,
    modelo,
    colunas_origem,
):
    codigo = sinal["codigo"]

    # Inicia a transacao somente depois da resposta do Gemini.
    con.execute("BEGIN IMMEDIATE")

    existente = con.execute("""
        SELECT codigo, origem_id
        FROM CLASSIFICACAO_GEMINI
        WHERE codigo = ?
    """, (codigo,)).fetchone()

    if existente:
        if existente["origem_id"] != sinal["origem_id"]:
            raise RuntimeError(
                f"Codigo {codigo} ja pertence a outro sinal."
            )

        # Se ja existe no destino, confirma a identidade
        # antes de retirar eventual registro repetido da origem.
    else:
        nomes = [
            "codigo",
            "origem_id",
            "classificacao",
            "justificativa",
            "modelo",
            "classificado_utc",
            "resposta_json",
        ]

        valores = [
            codigo,
            sinal["origem_id"],
            resultado["classificacao"],
            resultado["justificativa"],
            modelo,
            agora(),
            resultado["resposta_json"],
        ]

        for coluna in colunas_origem:
            nome = coluna["name"]

            if nome in (
                "codigo",
                "origem_id",
                "transferido_utc",
            ):
                continue

            nomes.append(nome)
            valores.append(sinal[nome])

        sql_colunas = ", ".join(
            identificador(nome)
            for nome in nomes
        )

        marcadores = ", ".join(
            "?" for _ in valores
        )

        con.execute(
            f"""
            INSERT INTO CLASSIFICACAO_GEMINI
            ({sql_colunas})
            VALUES ({marcadores})
            """,
            valores,
        )

    confirmado = con.execute("""
        SELECT codigo, classificacao
        FROM CLASSIFICACAO_GEMINI
        WHERE codigo = ?
    """, (codigo,)).fetchone()

    if confirmado is None:
        raise RuntimeError(
            f"Falha ao confirmar classificacao de {codigo}."
        )

    # Remove da origem somente depois da confirmacao.
    removido = con.execute("""
        DELETE FROM SINAIS_RECEBIDOS
        WHERE codigo = ?
          AND origem_id = ?
    """, (
        codigo,
        sinal["origem_id"],
    ))

    if removido.rowcount != 1:
        raise RuntimeError(
            f"Falha ao transferir o sinal {codigo}."
        )

    con.commit()


def executar():
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    erro_processamento = None

    try:
        con.row_factory = __import__("sqlite3").Row

        colunas_origem = criar_tabela(con)
        modelo = carregar_modelo()

        sinais = con.execute("""
            SELECT *
            FROM SINAIS_RECEBIDOS
            ORDER BY codigo ASC
        """).fetchall()

        total = len(sinais)
        transferidos = 0

        print("Modelo selecionado:", modelo)
        print("Sinais aguardando Gemini:", total)

        for linha in sinais:
            sinal = dict(linha)
            codigo = sinal["codigo"]

            try:
                print("Analisando:", codigo)

                resultado = classificar_gemini(
                    sinal,
                    modelo,
                )

                transferir_classificacao(
                    con,
                    sinal,
                    resultado,
                    modelo,
                    colunas_origem,
                )

                transferidos += 1

                print(
                    codigo,
                    "=>",
                    resultado["classificacao"],
                    "=> TRANSFERIDO",
                )

            except Exception as erro:
                con.rollback()

                erro_processamento = (
                    f"{codigo}: {str(erro)}"
                )

                print(
                    "SINAL MANTIDO NA ORIGEM:",
                    erro_processamento,
                )

                # Interrompe para nao insistir em uma API
                # que apresentou falha.
                break

        restantes = con.execute("""
            SELECT COUNT(*)
            FROM SINAIS_RECEBIDOS
        """).fetchone()[0]

        total_destino = con.execute("""
            SELECT COUNT(*)
            FROM CLASSIFICACAO_GEMINI
        """).fetchone()[0]

        con.close()

        # Salva todas as transferencias que foram confirmadas.
        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS,
        )

        print("\nPROCESSAMENTO GEMINI")
        print("Novos classificados:", transferidos)
        print("Total na classificacao:", total_destino)
        print("Sinais ainda na origem:", restantes)

        if erro_processamento:
            raise RuntimeError(
                "Processamento interrompido. "
                "O sinal com erro permaneceu na origem. "
                + erro_processamento
            )

    except Exception:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass

        temporario.cleanup()
        raise


if __name__ == "__main__":
    executar()
