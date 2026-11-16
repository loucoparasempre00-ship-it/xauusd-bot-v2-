
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CENTRAL -> GESTOR XAUUSD
Espelhamento de leitura dos bancos oficiais.

- Lê graficos.db.gz e estatisticas_m5.db.gz.
- Não altera os bancos originais.
- Envia os dados em lotes para evitar requisições grandes.
- Não envia sinais nem executa operações.
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

TAMANHO_LOTE = 200
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


def enviar(url, token, pacote):
    corpo = json.dumps(
        pacote,
        ensure_ascii=False,
        default=str,
        separators=(",", ":")
    ).encode("utf-8")

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

            print(
                "Recebido pelo Gestor:",
                pacote["dados"]["banco"],
                pacote["dados"]["tabela"],
                "lote",
                pacote["dados"]["lote"],
                "| HTTP",
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

    if tabela == "candles" and "timeframe" in colunas:
        for numero_lote, timeframe in enumerate(
            ("M5", "M15", "H1")
        ):
            enviar_candles(
                conexao,
                url,
                token,
                ciclo_id,
                banco,
                identificador,
                tabela,
                colunas,
                total,
                timeframe,
                numero_lote
            )
        return

    cursor = conexao.execute(
        f"SELECT * FROM {identificador}"
    )

    lote = 0

    while True:
        linhas = cursor.fetchmany(TAMANHO_LOTE)

        if not linhas:
            break

        registros = [
            {
                chave: (
                    valor.hex()
                    if isinstance(valor, (bytes, bytearray))
                    else valor
                )
                for chave, valor in dict(linha).items()
            }
            for linha in linhas
        ]

        pacote = {
            "tipo": "espelho_bancos_leitura",
            "versao": 1,
            "ciclo_id": ciclo_id,
            "somente_leitura": True,
            "dados": {
                "banco": banco,
                "tabela": tabela,
                "colunas": colunas,
                "total_registros_origem": total,
                "lote": lote,
                "registros": registros,
                "ultimo_lote": (lote + 1) * TAMANHO_LOTE >= total
            }
        }

        enviar(url, token, pacote)
        lote += 1

    if total == 0:
        enviar(url, token, {
            "tipo": "espelho_bancos_leitura",
            "versao": 1,
            "ciclo_id": ciclo_id,
            "somente_leitura": True,
            "dados": {
                "banco": banco,
                "tabela": tabela,
                "colunas": colunas,
                "total_registros_origem": 0,
                "lote": 0,
                "registros": [],
                "ultimo_lote": True
            }
        })

    print(f"{banco}.{tabela}: {total} registros enviados.")


def enviar_candles(
    conexao,
    url,
    token,
    ciclo_id,
    banco,
    identificador,
    tabela,
    colunas,
    total,
    timeframe,
    numero_lote
):
    cursor = conexao.execute(
        f"""
        SELECT * FROM {identificador}
        WHERE timeframe = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (timeframe, LIMITE_CANDLES_POR_TF)
    )

    linhas = cursor.fetchall()
    registros = [dict(linha) for linha in reversed(linhas)]

    pacote = {
        "tipo": "espelho_bancos_leitura",
        "versao": 1,
        "ciclo_id": ciclo_id,
        "somente_leitura": True,
        "dados": {
            "banco": banco,
            "tabela": tabela,
            "timeframe": timeframe,
            "colunas": colunas,
            "total_registros_origem": total,
            "registros_enviados": len(registros),
            "lote": numero_lote,
            "registros": registros,
            "ultimo_lote": True
        }
    }

    enviar(url, token, pacote)

    print(
        f"{banco}.{tabela}.{timeframe}: "
        f"{len(registros)} velas enviadas. "
        f"Lote: {numero_lote}"
    )


def transmitir_banco(nome, caminho, url, token, ciclo_id):
    if not os.path.isfile(caminho):
        raise RuntimeError(f"Banco ausente: {caminho}")

    conexao, temporario = abrir_leitura(caminho)

    try:
        tabelas = [
            linha["name"]
            for linha in conexao.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type='table'
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
            "GESTOR_XAUUSD_URL ou GESTOR_XAUUSD_TOKEN ausente."
        )

    ciclo_id = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    print("====================================")
    print("ESPELHAMENTO CENTRAL -> GESTOR")
    print("MODO: SOMENTE LEITURA")
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
