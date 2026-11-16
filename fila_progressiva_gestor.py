
import os
import sys
import gzip
import json
import sqlite3
import hashlib
import base64
import tempfile
from datetime import datetime, timezone


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BANCOS = {
    "graficos": "data/graficos.db.gz",
    "estatisticas_m5": "data/estatisticas_m5.db.gz",
}

ARQUIVO_FILA = "data/fila_progressiva_gestor.db"

LEITURA_POR_LOTE = 200
ENVIO_POR_LOTE = 50


# ============================================================
# UTILITÁRIOS
# ============================================================

def agora():
    return datetime.now(timezone.utc).isoformat()


def serializar_valor(valor):
    if isinstance(valor, bytes):
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


def preparar_pasta():
    os.makedirs("data", exist_ok=True)


# ============================================================
# BANCO DA FILA
# ============================================================

def conectar_fila():
    preparar_pasta()

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
            confirmado_em TEXT
        )
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_fila_gestor_status
        ON fila_gestor(status, id)
    """)

    conexao.commit()
    return conexao


# ============================================================
# ABERTURA DOS BANCOS OFICIAIS
# ============================================================

def abrir_banco_comprimido(caminho):
    if not os.path.isfile(caminho):
        raise FileNotFoundError(
            f"Banco não encontrado: {caminho}"
        )

    arquivo_temporario = tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False,
    )

    caminho_temporario = arquivo_temporario.name
    arquivo_temporario.close()

    try:
        with gzip.open(caminho, "rb") as origem:
            with open(caminho_temporario, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

        uri = (
            "file:"
            + os.path.abspath(caminho_temporario)
            + "?mode=ro"
        )

        conexao = sqlite3.connect(uri, uri=True)
        conexao.row_factory = sqlite3.Row

        return conexao, caminho_temporario

    except Exception:
        if os.path.exists(caminho_temporario):
            os.remove(caminho_temporario)

        raise


# ============================================================
# IDENTIFICAÇÃO DAS TABELAS
# ============================================================

def listar_tabelas(conexao):
    registros = conexao.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()

    return [registro["name"] for registro in registros]


# ============================================================
# COPIAR REGISTROS PARA A FILA
# ============================================================

def copiar_tabela(
    conexao_origem,
    conexao_fila,
    nome_banco,
    nome_tabela,
):
    total_lidos = 0
    total_novos = 0

    cursor = conexao_origem.execute(
        f'SELECT * FROM "{nome_tabela}"'
    )

    while True:
        registros = cursor.fetchmany(LEITURA_POR_LOTE)

        if not registros:
            break

        insercoes = []

        for registro in registros:
            dados_json = serializar_registro(registro)

            identificador = hash_registro(
                nome_banco,
                nome_tabela,
                dados_json,
            )

            insercoes.append((
                nome_banco,
                nome_tabela,
                identificador,
                dados_json,
                agora(),
            ))

        antes = conexao_fila.total_changes

        conexao_fila.executemany("""
            INSERT OR IGNORE INTO fila_gestor (
                banco,
                tabela_origem,
                hash_registro,
                dados_json,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?)
        """, insercoes)

        total_novos += conexao_fila.total_changes - antes
        total_lidos += len(registros)

        conexao_fila.commit()

    return total_lidos, total_novos


# ============================================================
# PREPARAR FILA PROGRESSIVA
# ============================================================

def preparar_fila():
    conexao_fila = conectar_fila()

    total_lidos = 0
    total_novos = 0

    try:
        for nome_banco, caminho in BANCOS.items():
            print(f"\nBANCO: {nome_banco}")
            print(f"Arquivo: {caminho}")

            conexao_origem = None
            caminho_temporario = None

            try:
                conexao_origem, caminho_temporario = (
                    abrir_banco_comprimido(caminho)
                )

                tabelas = listar_tabelas(conexao_origem)

                for tabela in tabelas:
                    lidos, novos = copiar_tabela(
                        conexao_origem,
                        conexao_fila,
                        nome_banco,
                        tabela,
                    )

                    total_lidos += lidos
                    total_novos += novos

                    print(
                        f"{tabela}: "
                        f"{lidos} lidos, "
                        f"{novos} novos na fila"
                    )

            finally:
                if conexao_origem:
                    conexao_origem.close()

                if (
                    caminho_temporario
                    and os.path.exists(caminho_temporario)
                ):
                    os.remove(caminho_temporario)

    finally:
        conexao_fila.close()

    print("\nPREPARAÇÃO CONCLUÍDA")
    print(f"Registros examinados: {total_lidos}")
    print(f"Novos registros na fila: {total_novos}")


# ============================================================
# BUSCAR PRÓXIMO LOTE
# ============================================================

def obter_lote(limite=ENVIO_POR_LOTE):
    conexao = conectar_fila()

    try:
        registros = conexao.execute("""
            SELECT
                id,
                banco,
                tabela_origem,
                dados_json
            FROM fila_gestor
            WHERE status = 'pendente'
            ORDER BY id
            LIMIT ?
        """, (limite,)).fetchall()

        lote = []

        for registro in registros:
            lote.append({
                "id": registro["id"],
                "banco": registro["banco"],
                "tabela_origem": registro["tabela_origem"],
                "dados": json.loads(registro["dados_json"]),
            })

        return {
            "tipo": "lote_progressivo_gestor",
            "somente_leitura": True,
            "quantidade": len(lote),
            "registros": lote,
        }

    finally:
        conexao.close()


# ============================================================
# CONFIRMAR RECEBIMENTO PELO GESTOR
# ============================================================

def confirmar_lote(ids):
    if not ids:
        return 0

    ids_validos = []

    for identificador in ids:
        try:
            ids_validos.append(int(identificador))
        except (TypeError, ValueError):
            continue

    if not ids_validos:
        return 0

    conexao = conectar_fila()

    try:
        marcadores = ",".join("?" for _ in ids_validos)

        cursor = conexao.execute(
            f"""
            DELETE FROM fila_gestor
            WHERE status = 'pendente'
              AND id IN ({marcadores})
            """,
            ids_validos,
        )

        conexao.commit()
        return cursor.rowcount

    finally:
        conexao.close()


# ============================================================
# RESUMO DA FILA
# ============================================================

def resumo_fila():
    conexao = conectar_fila()

    try:
        pendentes = conexao.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
            WHERE status = 'pendente'
        """).fetchone()[0]

        total = conexao.execute("""
            SELECT COUNT(*)
            FROM fila_gestor
        """).fetchone()[0]

        por_banco = conexao.execute("""
            SELECT banco, COUNT(*) AS quantidade
            FROM fila_gestor
            WHERE status = 'pendente'
            GROUP BY banco
            ORDER BY banco
        """).fetchall()

        print("\nRESUMO DA FILA PROGRESSIVA")
        print(f"Total armazenado: {total}")
        print(f"Pendentes: {pendentes}")
        print(f"Lote máximo de envio: {ENVIO_POR_LOTE}")

        for registro in por_banco:
            print(
                f"{registro['banco']}: "
                f"{registro['quantidade']} pendentes"
            )

    finally:
        conexao.close()


# ============================================================
# EXECUÇÃO
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(
            "Uso: python fila_progressiva_gestor.py "
            "preparar | lote | resumo"
        )
        sys.exit(1)

    comando = sys.argv[1].lower()

    if comando == "preparar":
        preparar_fila()

    elif comando == "lote":
        print(
            json.dumps(
                obter_lote(),
                ensure_ascii=False,
            )
        )

    elif comando == "resumo":
        resumo_fila()

    else:
        print(f"Comando desconhecido: {comando}")
        sys.exit(1)


if __name__ == "__main__":
    main()
