
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR DE SINAIS XAUUSD

TRANSFERENCIA:
setup_decisoes -> SINAIS_RECEBIDOS

- Transfere todos os campos da origem.
- Acrescenta codigo sequencial permanente.
- Preserva o ID original como origem_id.
- Cria automaticamente no destino colunas novas
  que existam na origem.
- Apaga o registro da origem somente após confirmar
  a insercao no destino.
- Executa tudo em uma transacao SQLite.
- Mantem o banco oficial sinais.db.gz.
"""

import sqlite3
from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


ORIGEM = "setup_decisoes"
DESTINO = "SINAIS_RECEBIDOS"


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def identificador(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def criar_tabela_destino(con, colunas_origem):
    """
    Cria o destino e acrescenta colunas novas,
    sem apagar ou recriar registros existentes.
    """

    definicoes = [
        '"codigo" TEXT PRIMARY KEY',
        '"origem_id" INTEGER NOT NULL UNIQUE',
        '"transferido_utc" TEXT NOT NULL',
    ]

    for coluna in colunas_origem:
        nome = coluna["name"]

        if nome == "id":
            continue

        tipo = coluna["type"] or "TEXT"

        definicoes.append(
            f'{identificador(nome)} {tipo}'
        )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {identificador(DESTINO)}
        ({", ".join(definicoes)})
        """
    )

    existentes = {
        linha["name"]
        for linha in con.execute(
            f'PRAGMA table_info({identificador(DESTINO)})'
        ).fetchall()
    }

    # Acrescenta as colunas que surgiram na origem.
    # Os registros antigos permanecem intactos.
    for coluna in colunas_origem:
        nome = coluna["name"]

        if nome in ("id", "codigo", "origem_id", "transferido_utc"):
            continue

        if nome not in existentes:
            tipo = coluna["type"] or "TEXT"

            con.execute(
                f"""
                ALTER TABLE {identificador(DESTINO)}
                ADD COLUMN {identificador(nome)} {tipo}
                """
            )

            existentes.add(nome)

            print(
                f"Nova coluna criada em {DESTINO}: {nome}"
            )

    obrigatorias = {
        "codigo",
        "origem_id",
        "transferido_utc",
    }

    obrigatorias.update(
        coluna["name"]
        for coluna in colunas_origem
        if coluna["name"] != "id"
    )

    faltando = obrigatorias - existentes

    if faltando:
        raise RuntimeError(
            "Nao foi possivel preparar o destino. "
            "Colunas ausentes: "
            + ", ".join(sorted(faltando))
        )


def proximo_codigo(con):
    """
    Busca o maior codigo utilizado.
    Nunca reutiliza codigos existentes.
    """

    registros = con.execute("""
        SELECT codigo
        FROM SINAIS_RECEBIDOS
        WHERE codigo LIKE 'OP-%'
    """).fetchall()

    maior = 0

    for registro in registros:
        codigo = str(registro["codigo"])

        try:
            maior = max(maior, int(codigo[3:]))
        except (ValueError, TypeError):
            continue

    return maior + 1


def transferir(con):
    """
    Move os registros da origem para o destino.
    INSERT e DELETE acontecem na mesma transacao.
    """

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
            f"Tabela de origem {ORIGEM} nao encontrada."
        )

    colunas_origem = con.execute(
        f'PRAGMA table_info({identificador(ORIGEM)})'
    ).fetchall()

    nomes_colunas = [
        coluna["name"]
        for coluna in colunas_origem
    ]

    if "id" not in nomes_colunas:
        raise RuntimeError(
            "A tabela de origem nao possui coluna id."
        )

    criar_tabela_destino(con, colunas_origem)

    con.execute("BEGIN IMMEDIATE")

    registros = con.execute(
        f"""
        SELECT *
        FROM {identificador(ORIGEM)}
        ORDER BY id ASC
        """
    ).fetchall()

    total = len(registros)
    transferidos = 0
    sequencia = proximo_codigo(con)

    for registro in registros:
        dados = dict(registro)
        codigo = f"OP-{sequencia:06d}"

        nomes_destino = [
            "codigo",
            "origem_id",
            "transferido_utc",
        ]

        valores = [
            codigo,
            dados["id"],
            agora(),
        ]

        for coluna in nomes_colunas:
            if coluna == "id":
                continue

            nomes_destino.append(coluna)
            valores.append(dados[coluna])

        colunas_sql = ", ".join(
            identificador(nome)
            for nome in nomes_destino
        )

        marcadores = ", ".join(
            "?" for _ in valores
        )

        con.execute(
            f"""
            INSERT INTO {identificador(DESTINO)}
            ({colunas_sql})
            VALUES ({marcadores})
            """,
            valores,
        )

        confirmado = con.execute(
            f"""
            SELECT codigo
            FROM {identificador(DESTINO)}
            WHERE origem_id = ?
            """,
            (dados["id"],),
        ).fetchone()

        if confirmado is None or confirmado["codigo"] != codigo:
            raise RuntimeError(
                f"Falha ao confirmar transferencia do ID {dados['id']}."
            )

        cursor = con.execute(
            f"""
            DELETE FROM {identificador(ORIGEM)}
            WHERE id = ?
            """,
            (dados["id"],),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Falha ao remover registro original ID {dados['id']}."
            )

        transferidos += 1
        sequencia += 1

        print(
            f"Transferido: ID {dados['id']} -> {codigo}"
        )

    restantes = con.execute(
        f'SELECT COUNT(*) FROM {identificador(ORIGEM)}'
    ).fetchone()[0]

    if restantes != 0:
        raise RuntimeError(
            f"Ainda existem {restantes} registros na origem."
        )

    con.commit()

    return total, transferidos, restantes


def executar():
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        con.row_factory = sqlite3.Row

        total, transferidos, restantes = transferir(con)

        print("\nTRANSFERENCIA CONCLUIDA")
        print("Registros encontrados:", total)
        print("Registros transferidos:", transferidos)
        print("Registros restantes na origem:", restantes)

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS,
        )

        print("Banco sinais.db.gz atualizado com sucesso.")

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
