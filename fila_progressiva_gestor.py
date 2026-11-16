
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import gzip
import json
import sqlite3
import hashlib
import base64
import tempfile
from datetime import datetime, timezone

BANCOS = {
    "graficos": "data/graficos.db.gz",
    "estatisticas_m5": "data/estatisticas_m5.db.gz",
}

ARQUIVO_FILA = "data/fila_progressiva_gestor.db"
LEITURA_POR_LOTE = 200
NOVOS_POR_EXECUCAO = 3
ANTIGOS_POR_EXECUCAO = 3


def agora():
    return datetime.now(timezone.utc).isoformat()


def nome_seguro(nome):
    return '"' + nome.replace('"', '""') + '"'


def serializar_valor(valor):
    if isinstance(valor, (bytes, bytearray)):
        return {
            "__tipo__": "blob",
            "base64": base64.b64encode(valor).decode("ascii"),
        }
    return valor


def serializar_registro(registro):
    dados = {
        chave: serializar_valor(valor)
        for chave, valor in dict(registro).items()
    }
    return json.dumps(
        dados,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def hash_registro(banco, tabela, dados_json):
    conteudo = f"{banco}|{tabela}|{dados_json}"
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def conectar_fila():
    os.makedirs("data", exist_ok=True)

    conexao = sqlite3.connect(ARQUIVO_FILA)
    conexao.row_factory = sqlite3.Row

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS fila_gestor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            banco TEXT NOT NULL,
            tabela_origem TEXT NOT NULL,
            hash_registro TEXT NOT NULL UNIQUE,
            dados_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            criado_em TEXT NOT NULL,
            confirmado_em TEXT,
            colunas_json TEXT,
            total_origem INTEGER,
            novidade INTEGER NOT NULL DEFAULT 0
        )
    """)

    colunas = {
        linha["name"]
        for linha in conexao.execute(
            "PRAGMA table_info(fila_gestor)"
        ).fetchall()
    }

    if "colunas_json" not in colunas:
        conexao.execute(
            "ALTER TABLE fila_gestor ADD COLUMN colunas_json TEXT"
        )

    if "total_origem" not in colunas:
        conexao.execute(
            "ALTER TABLE fila_gestor ADD COLUMN total_origem INTEGER"
        )

    if "novidade" not in colunas:
        # Registros que já estavam na fila são tratados como antigos.
        conexao.execute("""
            ALTER TABLE fila_gestor
            ADD COLUMN novidade INTEGER NOT NULL DEFAULT 0
        """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_fila_gestor_status
        ON fila_gestor(status, id)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_fila_gestor_origem
        ON fila_gestor(banco, tabela_origem, status, id)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_fila_gestor_novidade
        ON fila_gestor(status, novidade, id)
    """)

    conexao.commit()
    return conexao


def abrir_banco_comprimido(caminho):
    if not os.path.isfile(caminho):
        raise FileNotFoundError(f"Banco não encontrado: {caminho}")

    temporario = tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False,
    )
    caminho_temp = temporario.name
    temporario.close()

    try:
        with gzip.open(caminho, "rb") as origem:
            with open(caminho_temp, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)
                    if not bloco:
                        break
                    destino.write(bloco)

        uri = "file:" + os.path.abspath(caminho_temp) + "?mode=ro"
        conexao = sqlite3.connect(uri, uri=True)
        conexao.row_factory = sqlite3.Row
        return conexao, caminho_temp

    except Exception:
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)
        raise


def listar_tabelas(conexao):
    linhas = conexao.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()

    return [linha["name"] for linha in linhas]


def copiar_tabela(origem, fila, banco, tabela):
    identificador = nome_seguro(tabela)

    colunas = [
        linha["name"]
        for linha in origem.execute(
            f"PRAGMA table_info({identificador})"
        ).fetchall()
    ]

    if not colunas:
        return 0, 0

    total_origem = origem.execute(
        f"SELECT COUNT(*) FROM {identificador}"
    ).fetchone()[0]

    colunas_json = json.dumps(
        colunas,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    cursor_origem = origem.execute(
        f"SELECT * FROM {identificador}"
    )

    total_lidos = 0
    total_novos = 0

    while True:
        registros = cursor_origem.fetchmany(LEITURA_POR_LOTE)
        if not registros:
            break

        insercoes = []

        for registro in registros:
            dados_json = serializar_registro(registro)
            identificador_registro = hash_registro(
                banco,
                tabela,
                dados_json,
            )

            insercoes.append((
                banco,
                tabela,
                identificador_registro,
                dados_json,
                agora(),
                colunas_json,
                total_origem,
            ))

        cursor = fila.executemany("""
            INSERT OR IGNORE INTO fila_gestor (
                banco,
                tabela_origem,
                hash_registro,
                dados_json,
                criado_em,
                colunas_json,
                total_origem,
                novidade
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, insercoes)

        total_novos += max(cursor.rowcount, 0)
        total_lidos += len(registros)
        fila.commit()

    return total_lidos, total_novos


def preparar_fila():
    fila = conectar_fila()
    total_lidos = 0
    total_novos = 0

    try:
        for banco, caminho in BANCOS.items():
            print(f"\nBANCO: {banco}", flush=True)

            origem = None
            caminho_temp = None

            try:
                origem, caminho_temp = abrir_banco_comprimido(caminho)

                for tabela in listar_tabelas(origem):
                    lidos, novos = copiar_tabela(
                        origem,
                        fila,
                        banco,
                        tabela,
                    )

                    total_lidos += lidos
                    total_novos += novos

                    print(
                        f"{tabela}: {lidos} examinados, "
                        f"{novos} novos na fila",
                        flush=True,
                    )

            finally:
                if origem:
                    origem.close()

                if caminho_temp and os.path.exists(caminho_temp):
                    os.remove(caminho_temp)

    finally:
        fila.close()

    print("\nPREPARAÇÃO CONCLUÍDA")
    print(f"Registros examinados: {total_lidos}")
    print(f"Novos registros adicionados: {total_novos}")


def obter_lote():
    fila = conectar_fila()

    try:
        # Prioriza uma tabela que tenha informações novas.
        primeira = fila.execute("""
            SELECT banco, tabela_origem
            FROM fila_gestor
            WHERE status = 'pendente'
              AND novidade = 1
            ORDER BY id
            LIMIT 1
        """).fetchone()

        # Se não houver novidades, continua o histórico antigo.
        if not primeira:
            primeira = fila.execute("""
                SELECT banco, tabela_origem
                FROM fila_gestor
                WHERE status = 'pendente'
                ORDER BY id
                LIMIT 1
            """).fetchone()

        if not primeira:
            return {
                "quantidade": 0,
                "registros": [],
                "ids": [],
            }

        banco = primeira["banco"]
        tabela = primeira["tabela_origem"]

        novos = fila.execute("""
            SELECT id, dados_json, colunas_json, total_origem
            FROM fila_gestor
            WHERE status = 'pendente'
              AND banco = ?
              AND tabela_origem = ?
              AND novidade = 1
            ORDER BY id
            LIMIT ?
        """, (
            banco,
            tabela,
            NOVOS_POR_EXECUCAO,
        )).fetchall()

        antigos = fila.execute("""
            SELECT id, dados_json, colunas_json, total_origem
            FROM fila_gestor
            WHERE status = 'pendente'
              AND banco = ?
              AND tabela_origem = ?
              AND novidade = 0
            ORDER BY id
            LIMIT ?
        """, (
            banco,
            tabela,
            ANTIGOS_POR_EXECUCAO,
        )).fetchall()

        selecionados = list(novos) + list(antigos)

        if not selecionados:
            return {
                "quantidade": 0,
                "registros": [],
                "ids": [],
            }

        primeiro_registro = selecionados[0]

        total_pendente = fila.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
            WHERE status = 'pendente'
              AND banco = ?
              AND tabela_origem = ?
        """, (banco, tabela)).fetchone()[0]

        return {
            "banco": banco,
            "tabela": tabela,
            "colunas": json.loads(
                primeiro_registro["colunas_json"] or "[]"
            ),
            "total_origem": primeiro_registro["total_origem"] or 0,
            "ultimo_lote": total_pendente <= len(selecionados),
            "quantidade": len(selecionados),
            "novos_quantidade": len(novos),
            "antigos_quantidade": len(antigos),
            "ids": [r["id"] for r in selecionados],
            "registros": [
                json.loads(r["dados_json"])
                for r in selecionados
            ],
        }

    finally:
        fila.close()


def confirmar_lote(ids):
    if not ids:
        return 0

    ids_validos = []

    for valor in ids:
        try:
            ids_validos.append(int(valor))
        except (TypeError, ValueError):
            continue

    if not ids_validos:
        return 0

    fila = conectar_fila()

    try:
        removidos = 0

        for inicio in range(0, len(ids_validos), 500):
            parte = ids_validos[inicio:inicio + 500]
            marcadores = ",".join("?" for _ in parte)

            cursor = fila.execute(
                f"""
                DELETE FROM fila_gestor
                WHERE status = 'pendente'
                  AND id IN ({marcadores})
                """,
                parte,
            )

            removidos += cursor.rowcount

        fila.commit()
        return removidos

    finally:
        fila.close()


def resumo_fila():
    fila = conectar_fila()

    try:
        total = fila.execute(
            "SELECT COUNT(*) FROM fila_gestor"
        ).fetchone()[0]

        pendentes = fila.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
            WHERE status = 'pendente'
        """).fetchone()[0]

        novos = fila.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
            WHERE status = 'pendente'
              AND novidade = 1
        """).fetchone()[0]

        antigos = fila.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
            WHERE status = 'pendente'
              AND novidade = 0
        """).fetchone()[0]

        print("\nRESUMO DA FILA PROGRESSIVA")
        print(f"Total armazenado: {total}")
        print(f"Pendentes: {pendentes}")
        print(f"Novos pendentes: {novos}")
        print(f"Antigos pendentes: {antigos}")
        print(
            "Por execução: até "
            f"{NOVOS_POR_EXECUCAO} novos + "
            f"{ANTIGOS_POR_EXECUCAO} antigos"
        )

        por_banco = fila.execute("""
            SELECT banco, COUNT(*) AS quantidade
            FROM fila_gestor
            WHERE status = 'pendente'
            GROUP BY banco
            ORDER BY banco
        """).fetchall()

        for linha in por_banco:
            print(
                f"{linha['banco']}: "
                f"{linha['quantidade']} pendentes"
            )

    finally:
        fila.close()


def main():
    if len(sys.argv) < 2:
        print("Uso: preparar | lote | resumo")
        sys.exit(1)

    comando = sys.argv[1].lower()

    if comando == "preparar":
        preparar_fila()
    elif comando == "lote":
        print(json.dumps(obter_lote(), ensure_ascii=False))
    elif comando == "resumo":
        resumo_fila()
    else:
        raise RuntimeError(f"Comando desconhecido: {comando}")


if __name__ == "__main__":
    main()
