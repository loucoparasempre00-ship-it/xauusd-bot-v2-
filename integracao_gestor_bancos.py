#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CENTRAL -> GESTOR XAUUSD

Espelhamento somente de leitura dos bancos oficiais.
Não altera os bancos originais.
Não envia sinais nem executa operações.
"""

import gzip
import json
import os
import sqlite3
import sys
import tempfile
import urllib.request
import urllib.error

from datetime import datetime, timezone


BANCOS = {
    "graficos": "data/graficos.db.gz",
    "estatisticas_m5": "data/estatisticas_m5.db.gz",
}

LIMITE_CANDLES_POR_TF = 500
LIMITE_REQUISICAO = 500_000


def abrir_leitura(caminho):
    temporario = tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False
    )

    nome_temp = temporario.name
    temporario.close()

    try:
        with gzip.open(caminho, "rb") as origem:
            with open(nome_temp, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

        conexao = sqlite3.connect(
            "file:" + nome_temp + "?mode=ro",
            uri=True
        )

        conexao.row_factory = sqlite3.Row

        return conexao, nome_temp

    except Exception:
        if os.path.exists(nome_temp):
            os.remove(nome_temp)

        raise


def serializar(pacote):
    return json.dumps(
        pacote,
        ensure_ascii=False,
        default=str,
        separators=(",", ":")
    ).encode("utf-8")


def enviar(url, token, pacote):
    corpo = serializar(pacote)

    if len(corpo) > LIMITE_REQUISICAO:
        raise RuntimeError(
            "Lote excedeu o limite de segurança: "
            + str(len(corpo))
            + " bytes."
        )

    requisicao = urllib.request.Request(
        url,
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Central-Secret": token,
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=45
        ) as resposta:

            retorno = resposta.read().decode(
                "utf-8",
                errors="replace"
            )

            if not 200 <= resposta.status < 300:
                raise RuntimeError(
                    f"HTTP {resposta.status}: {retorno}"
                )

            dados = pacote["dados"]

            print(
                "Recebido pelo Gestor:",
                dados["banco"],
                dados["tabela"],
                "lote",
                dados["lote"],
                "|",
                len(corpo),
                "bytes | HTTP",
                resposta.status
            )

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Erro HTTP {erro.code}: {detalhe}"
        )

    except urllib.error.URLError as erro:
        raise RuntimeError(
            f"Falha de conexão: {erro}"
        )


def nome_seguro(nome):
    return '"' + nome.replace('"', '""') + '"'


def normalizar_registro(linha):
    resultado = {}

    for chave, valor in dict(linha).items():
        if isinstance(valor, (bytes, bytearray)):
            resultado[chave] = valor.hex()
        else:
            resultado[chave] = valor

    return resultado


def criar_pacote(
    ciclo_id,
    banco,
    tabela,
    colunas,
    total,
    numero_lote,
    registros,
    ultimo_lote,
    extras=None
):
    dados = {
        "banco": banco,
        "tabela": tabela,
        "colunas": colunas,
        "total_registros_origem": total,
        "lote": numero_lote,
        "registros": registros,
        "ultimo_lote": ultimo_lote
    }

    if extras:
        dados.update(extras)

    return {
        "tipo": "espelho_bancos_leitura",
        "versao": 1,
        "ciclo_id": ciclo_id,
        "somente_leitura": True,
        "dados": dados
    }


def transmitir_registros(
    url,
    token,
    ciclo_id,
    banco,
    tabela,
    colunas,
    total,
    registros,
    proximo_lote,
    extras=None
):
    atual = []

    for registro in registros:
        candidato = atual + [registro]

        pacote_teste = criar_pacote(
            ciclo_id,
            banco,
            tabela,
            colunas,
            total,
            proximo_lote[0],
            candidato,
            True,
            extras
        )

        tamanho = len(serializar(pacote_teste))

        if tamanho <= LIMITE_REQUISICAO:
            atual = candidato
            continue

        if not atual:
            raise RuntimeError(
                f"Um registro isolado da tabela "
                f"{banco}.{tabela} excede o limite de "
                f"{LIMITE_REQUISICAO} bytes."
            )

        pacote_anterior = criar_pacote(
            ciclo_id,
            banco,
            tabela,
            colunas,
            total,
            proximo_lote[0],
            atual,
            False,
            extras
        )

        enviar(url, token, pacote_anterior)

        proximo_lote[0] += 1
        atual = [registro]

        pacote_individual = criar_pacote(
            ciclo_id,
            banco,
            tabela,
            colunas,
            total,
            proximo_lote[0],
            atual,
            True,
            extras
        )

        tamanho_individual = len(
            serializar(pacote_individual)
        )

        if tamanho_individual > LIMITE_REQUISICAO:
            raise RuntimeError(
                f"Um registro isolado da tabela "
                f"{banco}.{tabela} excede o limite de "
                f"{LIMITE_REQUISICAO} bytes."
            )

    if atual:
        pacote_final = criar_pacote(
            ciclo_id,
            banco,
            tabela,
            colunas,
            total,
            proximo_lote[0],
            atual,
            True,
            extras
        )

        enviar(url, token, pacote_final)

        proximo_lote[0] += 1

    else:
        pacote_vazio = criar_pacote(
            ciclo_id,
            banco,
            tabela,
            colunas,
            total,
            proximo_lote[0],
            [],
            True,
            extras
        )

        enviar(url, token, pacote_vazio)

        proximo_lote[0] += 1


def transmitir_tabela(
    conexao,
    url,
    token,
    ciclo_id,
    banco,
    tabela
):
    identificador = nome_seguro(tabela)

    colunas = [
        linha["name"]
        for linha in conexao.execute(
            f"PRAGMA table_info({identificador})"
        ).fetchall()
    ]

    total = conexao.execute(
        f"SELECT COUNT(*) FROM {identificador}"
    ).fetchone()[0]

    proximo_lote = [0]

    if tabela == "candles" and "timeframe" in colunas:
        for timeframe in ("M5", "M15", "H1"):
            cursor = conexao.execute(
                f"""
                SELECT *
                FROM {identificador}
                WHERE timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (timeframe, LIMITE_CANDLES_POR_TF)
            )

            linhas = cursor.fetchall()

            registros = [
                normalizar_registro(linha)
                for linha in reversed(linhas)
            ]

            extras = {
                "timeframe": timeframe,
                "registros_enviados": len(registros)
            }

            transmitir_registros(
                url,
                token,
                ciclo_id,
                banco,
                tabela,
                colunas,
                total,
                iter(registros),
                proximo_lote,
                extras
            )

            print(
                f"{banco}.{tabela}.{timeframe}: "
                f"{len(registros)} velas processadas."
            )

        return

    cursor = conexao.execute(
        f"SELECT * FROM {identificador}"
    )

    def gerar_registros():
        while True:
            linhas = cursor.fetchmany(200)

            if not linhas:
                break

            for linha in linhas:
                yield normalizar_registro(linha)

    transmitir_registros(
        url,
        token,
        ciclo_id,
        banco,
        tabela,
        colunas,
        total,
        gerar_registros(),
        proximo_lote
    )

    print(
        f"{banco}.{tabela}: "
        f"{total} registros processados."
    )


def transmitir_banco(
    nome,
    caminho,
    url,
    token,
    ciclo_id
):
    if not os.path.isfile(caminho):
        raise RuntimeError(
            f"Banco ausente: {caminho}"
        )

    conexao, temporario = abrir_leitura(caminho)

    try:
        tabelas = [
            linha["name"]
            for linha in conexao.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).fetchall()
        ]

        if not tabelas:
            raise RuntimeError(
                f"Nenhuma tabela encontrada em {caminho}."
            )

        print(f"\nBanco: {nome}")
        print("Tabelas:", ", ".join(tabelas))

        for tabela in tabelas:
            transmitir_tabela(
                conexao,
                url,
                token,
                ciclo_id,
                nome,
                tabela
            )

    finally:
        conexao.close()

        if os.path.exists(temporario):
            os.remove(temporario)


def main():
    url = os.environ.get(
        "GESTOR_XAUUSD_URL",
        ""
    ).strip()

    token = os.environ.get(
        "GESTOR_XAUUSD_TOKEN",
        ""
    ).strip()

    if not url or not token:
        raise RuntimeError(
            "GESTOR_XAUUSD_URL ou "
            "GESTOR_XAUUSD_TOKEN ausente."
        )

    ciclo_id = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    print("====================================")
    print("ESPELHAMENTO CENTRAL -> GESTOR")
    print("MODO: SOMENTE LEITURA")
    print("LIMITE POR REQUISICAO:", LIMITE_REQUISICAO)
    print("CICLO:", ciclo_id)
    print("====================================")

    for nome, caminho in BANCOS.items():
        transmitir_banco(
            nome,
            caminho,
            url,
            token,
            ciclo_id
        )

    print("====================================")
    print("ESPELHAMENTO CONCLUIDO")
    print("BANCOS ORIGINAIS: INALTERADOS")
    print("====================================")


if __name__ == "__main__":
    try:
        main()

    except Exception as erro:
        print("ERRO NO ESPELHAMENTO:", erro)
        sys.exit(1)
