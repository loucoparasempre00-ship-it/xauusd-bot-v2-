
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR DE MIGRACAO XAUUSD
Migra dados antigos para quatro bancos SQLite.
Nao exclui arquivos originais.
"""

import csv
import gzip
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

BANCOS = {
    "graficos": DATA / "graficos.db",
    "sinais": DATA / "sinais.db",
    "memoria": DATA / "memoria.db",
    "operacoes": DATA / "operacoes.db",
}


def classificar(path):
    nome = path.name.lower()
    caminho = str(path).lower()

    if any(x in nome or x in caminho for x in (
        "sinal", "ranking", "marcador", "decisao"
    )):
        return "sinais"

    if any(x in nome or x in caminho for x in (
        "operac", "acompanhamento", "acompanhador",
        "condutor", "agente_operacional",
        "cadastro_mestre", "pacote_memoria_condutor"
    )):
        return "operacoes"

    if any(x in nome or x in caminho for x in (
        "memoria", "gemini", "conexao", "indice_arquivos"
    )):
        return "memoria"

    return "graficos"


def conectar(arquivo):
    banco = sqlite3.connect(arquivo)

    banco.execute("""
        CREATE TABLE IF NOT EXISTS origens (
            caminho TEXT PRIMARY KEY,
            categoria TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            tamanho_bytes INTEGER NOT NULL,
            importado_utc TEXT NOT NULL,
            conteudo BLOB NOT NULL
        )
    """)

    banco.execute("""
        CREATE TABLE IF NOT EXISTS registros (
            caminho TEXT NOT NULL,
            numero INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            chave TEXT,
            payload_json TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY (caminho, numero)
        )
    """)

    banco.execute("""
        CREATE TABLE IF NOT EXISTS metadados (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
    """)

    return banco


def inserir(banco, caminho, numero, tipo, objeto):
    texto = json.dumps(
        objeto,
        ensure_ascii=False,
        sort_keys=True,
        default=str
    )

    resumo = hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()

    banco.execute("""
        INSERT OR REPLACE INTO registros
        (caminho, numero, tipo, chave, payload_json, sha256)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        caminho,
        numero,
        tipo,
        None,
        texto,
        resumo
    ))


def importar_json(banco, caminho, conteudo):
    objeto = json.loads(conteudo.decode("utf-8-sig"))

    if isinstance(objeto, list):
        for numero, item in enumerate(objeto, 1):
            inserir(banco, caminho, numero, "json_item", item)
    else:
        inserir(banco, caminho, 1, "json_objeto", objeto)


def importar_csv(banco, caminho, conteudo, comprimido=False):
    if comprimido:
        conteudo = gzip.decompress(conteudo)

    texto = conteudo.decode("utf-8-sig")

    try:
        dialeto = csv.Sniffer().sniff(
            texto[:8192],
            delimiters=",;\t|"
        )
    except csv.Error:
        dialeto = csv.excel

    leitor = csv.DictReader(
        io.StringIO(texto),
        dialect=dialeto
    )

    if not leitor.fieldnames:
        inserir(
            banco, caminho, 1,
            "csv_vazio", {"conteudo": texto}
        )
        return

    for numero, linha in enumerate(leitor, 1):
        inserir(
            banco, caminho, numero,
            "csv_linha", linha
        )


def importar_sqlite(banco, caminho, conteudo):
    temporario = DATA / ".origem_temporaria.db"
    temporario.write_bytes(conteudo)

    try:
        origem = sqlite3.connect(
            f"file:{temporario}?mode=ro",
            uri=True
        )

        tabelas = origem.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            AND name NOT LIKE 'sqlite_%'
        """).fetchall()

        for tabela_num, (tabela,) in enumerate(tabelas, 1):
            colunas = [
                linha[1]
                for linha in origem.execute(
                    f'PRAGMA table_info("{tabela}")'
                )
            ]

            for numero, linha in enumerate(
                origem.execute(f'SELECT * FROM "{tabela}"'),
                1
            ):
                inserir(
                    banco,
                    f"{caminho}#{tabela}",
                    numero,
                    "sqlite_linha",
                    dict(zip(colunas, linha))
                )

        origem.close()

    finally:
        temporario.unlink(missing_ok=True)


def importar_arquivo(banco, arquivo, categoria):
    caminho = arquivo.relative_to(BASE).as_posix()
    conteudo = arquivo.read_bytes()

    resumo = hashlib.sha256(conteudo).hexdigest()

    banco.execute("""
        INSERT OR REPLACE INTO origens
        (caminho, categoria, sha256, tamanho_bytes,
         importado_utc, conteudo)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        caminho,
        categoria,
        resumo,
        len(conteudo),
        datetime.now(timezone.utc).isoformat(),
        sqlite3.Binary(conteudo)
    ))

    banco.execute(
        "DELETE FROM registros WHERE caminho=? OR caminho LIKE ?",
        (caminho, caminho + "#%")
    )

    nome = arquivo.name.lower()

    if nome.endswith(".json"):
        importar_json(banco, caminho, conteudo)

    elif nome.endswith(".csv"):
        importar_csv(banco, caminho, conteudo)

    elif nome.endswith(".gz"):
        importar_csv(
            banco, caminho, conteudo,
            comprimido=True
        )

    elif nome.endswith((".db", ".sqlite", ".sqlite3")):
        importar_sqlite(banco, caminho, conteudo)

    else:
        inserir(
            banco, caminho, 1,
            "arquivo_preservado",
            {
                "bytes": len(conteudo),
                "sha256": resumo
            }
        )


def main():
    DATA.mkdir(parents=True, exist_ok=True)

    arquivos = [
        arquivo
        for arquivo in DATA.rglob("*")
        if arquivo.is_file()
        and arquivo.name not in {
            "graficos.db",
            "sinais.db",
            "memoria.db",
            "operacoes.db"
        }
        and not arquivo.name.startswith(".")
        and not arquivo.name.endswith(
            (".tmp", ".lock", "-journal", "-wal", "-shm")
        )
    ]

    if not arquivos:
        raise SystemExit(
            "Nenhum arquivo antigo encontrado. "
            "Migracao cancelada."
        )

    bancos = {}

    try:
        for categoria, arquivo in BANCOS.items():
            bancos[categoria] = conectar(arquivo)

        erros = []

        for arquivo in sorted(arquivos):
            categoria = classificar(arquivo)

            try:
                importar_arquivo(
                    bancos[categoria],
                    arquivo,
                    categoria
                )

                print(
                    f"IMPORTADO [{categoria}]: "
                    f"{arquivo.relative_to(BASE)}"
                )

            except Exception as erro:
                erros.append(
                    f"{arquivo}: {erro}"
                )
                print(f"ERRO: {arquivo}: {erro}")

        if erros:
            raise RuntimeError(
                "MIGRACAO INCOMPLETA:\n"
                + "\n".join(erros)
            )

        relatorio = {
            "versao": 1,
            "modo": "MIGRACAO_NAO_DESTRUTIVA",
            "data_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "arquivos_lidos": len(arquivos),
            "originais_excluidos": 0,
            "bancos": {}
        }

        for categoria, banco in bancos.items():
            total = banco.execute(
                "SELECT COUNT(*) FROM origens"
            ).fetchone()[0]

            registros = banco.execute(
                "SELECT COUNT(*) FROM registros"
            ).fetchone()[0]

            banco.execute("""
                INSERT OR REPLACE INTO metadados
                (chave, valor)
                VALUES (?, ?)
            """, (
                "ultima_migracao",
                relatorio["data_utc"]
            ))

            banco.commit()

            integridade = banco.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            if integridade != "ok" or total == 0:
                raise RuntimeError(
                    f"Falha na validacao: {categoria}"
                )

            relatorio["bancos"][categoria] = {
                "arquivos": total,
                "registros": registros,
                "integridade": integridade
            }

        (BASE / "relatorio_migracao_xauusd.json").write_text(
            json.dumps(
                relatorio,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print(json.dumps(
            relatorio,
            ensure_ascii=False,
            indent=2
        ))

        print("MIGRACAO_XAUUSD_OK")

    finally:
        for banco in bancos.values():
            banco.close()


if __name__ == "__main__":
    main()
