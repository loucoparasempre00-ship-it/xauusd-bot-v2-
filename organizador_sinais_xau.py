
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR DE SINAIS XAUUSD

TRANSFERENCIA:
setup_decisoes -> SINAIS_RECEBIDOS

- Transfere todos os campos da origem.
- Acrescenta codigo sequencial permanente.
- Preserva o ID original como origem_id.
- Cria automaticamente colunas novas no destino.
- Apaga a origem somente apos confirmar a transferencia.
- Executa tudo em uma unica transacao SQLite.
- Mantem o banco oficial sinais.db.gz.
- Nunca reutiliza codigos OP ja utilizados.
"""

import sqlite3
import re

from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


ORIGEM = "setup_decisoes"
DESTINO = "SINAIS_RECEBIDOS"
CONTROLE = "CONTROLE_SEQUENCIA_OP"


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def identificador(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def criar_controle(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {identificador(CONTROLE)} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            ultimo_numero INTEGER NOT NULL DEFAULT 0
        )
    """)

    con.execute(f"""
        INSERT OR IGNORE INTO {identificador(CONTROLE)}
        (id, ultimo_numero)
        VALUES (1, 0)
    """)


def criar_tabela_destino(con, colunas_origem):
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

    for coluna in colunas_origem:
        nome = coluna["name"]

        if nome in (
            "id",
            "codigo",
            "origem_id",
            "transferido_utc",
        ):
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
            "Colunas ausentes no destino: "
            + ", ".join(sorted(faltando))
        )


def maior_codigo_utilizado(con):
    """
    Procura o maior codigo OP existente em todas
    as tabelas do banco atual que possuam coluna codigo.
    """

    maior = 0

    tabelas = con.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
    """).fetchall()

    for linha in tabelas:
        tabela = linha["name"]

        if tabela == CONTROLE:
            continue

        colunas = {
            coluna["name"]
            for coluna in con.execute(
                f"PRAGMA table_info({identificador(tabela)})"
            ).fetchall()
        }

        if "codigo" not in colunas:
            continue

        registros = con.execute(
            f"""
            SELECT codigo
            FROM {identificador(tabela)}
            WHERE codigo LIKE 'OP-%'
            """
        ).fetchall()

        for registro in registros:
            codigo = str(
                registro["codigo"] or ""
            ).strip()

            correspondencia = re.fullmatch(
                r"OP-(\d+)",
                codigo
            )

            if correspondencia:
                maior = max(
                    maior,
                    int(correspondencia.group(1))
                )

    return maior


def proximo_codigo(con):
    """
    Combina o maior codigo historico encontrado
    com o contador permanente.

    A sequencia nao reinicia quando
    SINAIS_RECEBIDOS fica vazia.
    """

    criar_controle(con)

    controle = con.execute(
        f"""
        SELECT ultimo_numero
        FROM {identificador(CONTROLE)}
        WHERE id = 1
        """
    ).fetchone()

    ultimo_contador = int(
        controle["ultimo_numero"]
    )

    maior_historico = maior_codigo_utilizado(con)

    maior = max(
        ultimo_contador,
        maior_historico
    )

    return maior + 1


def atualizar_contador(con, numero):
    con.execute(
        f"""
        UPDATE {identificador(CONTROLE)}
        SET ultimo_numero = MAX(ultimo_numero, ?)
        WHERE id = 1
        """,
        (numero,)
    )


def transferir(con):
    """
    Transfere os registros da origem para o destino.

    IMPORTANTE:
    BEGIN IMMEDIATE acontece antes de qualquer
    criacao ou alteracao de tabela.

    INSERT e DELETE acontecem na mesma transacao.
    Se ocorrer erro, tudo sera revertido.
    """

    con.execute("BEGIN IMMEDIATE")

    try:
        tabelas = {
            linha["name"]
            for linha in con.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
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

        # Preparacao agora acontece dentro da transacao.
        criar_tabela_destino(con, colunas_origem)
        criar_controle(con)

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

            # Primeiro grava no destino.
            con.execute(
                f"""
                INSERT INTO {identificador(DESTINO)}
                ({colunas_sql})
                VALUES ({marcadores})
                """,
                valores,
            )

            # Confirma que a gravacao foi realizada.
            confirmado = con.execute(
                f"""
                SELECT codigo
                FROM {identificador(DESTINO)}
                WHERE origem_id = ?
                """,
                (dados["id"],),
            ).fetchone()

            if (
                confirmado is None
                or confirmado["codigo"] != codigo
            ):
                raise RuntimeError(
                    f"Falha ao confirmar transferencia "
                    f"do ID {dados['id']}."
                )

            # Somente depois da confirmacao apaga a origem.
            cursor = con.execute(
                f"""
                DELETE FROM {identificador(ORIGEM)}
                WHERE id = ?
                """,
                (dados["id"],),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Falha ao remover registro original "
                    f"ID {dados['id']}."
                )

            atualizar_contador(con, sequencia)

            transferidos += 1

            print(
                f"Transferido: ID {dados['id']} -> {codigo}"
            )

            sequencia += 1

        restantes = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {identificador(ORIGEM)}
            """
        ).fetchone()[0]

        if restantes != 0:
            raise RuntimeError(
                f"Ainda existem {restantes} "
                "registros na origem."
            )

        # Confirma todas as operacoes de uma vez.
        con.commit()

        return total, transferidos, restantes

    except Exception:
        # Reverte criacoes, gravacoes e exclusoes
        # realizadas nesta transacao.
        con.rollback()
        raise


def executar():
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        con.row_factory = sqlite3.Row

        total, transferidos, restantes = transferir(con)

        print("\nTRANSFERENCIA CONCLUIDA")
        print("Registros encontrados:", total)
        print("Registros transferidos:", transferidos)
        print("Registros restantes na origem:", restantes)

        controle = con.execute(
            f"""
            SELECT ultimo_numero
            FROM {identificador(CONTROLE)}
            WHERE id = 1
            """
        ).fetchone()

        print(
            "Ultimo codigo numerico utilizado:",
            controle["ultimo_numero"]
        )

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS,
        )

        print(
            "Banco sinais.db.gz atualizado com sucesso."
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
