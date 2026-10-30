import csv
import os
import sqlite3
from datetime import datetime, timezone

CSV_ENTRADA = "data/tabuleiro_m5.csv"
BANCO_SAIDA = "data/tabuleiro_m5.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS velas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL UNIQUE,
    eixo_x INTEGER NOT NULL,
    eixo_y INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    atr REAL NOT NULL,
    largura_regiao REAL NOT NULL,
    amplitude REAL NOT NULL,
    corpo REAL NOT NULL,
    direcao TEXT NOT NULL,
    evento TEXT NOT NULL,
    processado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regioes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eixo_y INTEGER NOT NULL,
    primeira_visita TEXT NOT NULL,
    ultima_visita TEXT NOT NULL,
    total_visitas INTEGER NOT NULL DEFAULT 0,
    UNIQUE(eixo_y)
);

CREATE TABLE IF NOT EXISTS transicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    regiao_origem INTEGER NOT NULL,
    regiao_destino INTEGER NOT NULL,
    timestamp_origem TEXT NOT NULL,
    timestamp_destino TEXT NOT NULL,
    intervalo_velas INTEGER NOT NULL,
    UNIQUE(timestamp_origem, timestamp_destino)
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    eixo_y INTEGER NOT NULL,
    tipo_evento TEXT NOT NULL,
    preco REAL NOT NULL,
    atr REAL NOT NULL,
    descricao TEXT
);

CREATE TABLE IF NOT EXISTS controle (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_velas_timestamp
ON velas(timestamp);

CREATE INDEX IF NOT EXISTS idx_velas_eixo_y
ON velas(eixo_y);

CREATE INDEX IF NOT EXISTS idx_transicoes_origem
ON transicoes(regiao_origem);

CREATE INDEX IF NOT EXISTS idx_transicoes_destino
ON transicoes(regiao_destino);

CREATE INDEX IF NOT EXISTS idx_eventos_regiao
ON eventos(eixo_y);
"""


def conectar():
    os.makedirs(os.path.dirname(BANCO_SAIDA), exist_ok=True)

    conexao = sqlite3.connect(BANCO_SAIDA)
    conexao.execute("PRAGMA journal_mode=WAL")
    conexao.execute("PRAGMA synchronous=FULL")
    conexao.execute("PRAGMA foreign_keys=ON")

    return conexao


def criar_estrutura(conexao):
    conexao.executescript(SCHEMA)
    conexao.commit()


def carregar_csv():
    if not os.path.exists(CSV_ENTRADA):
        raise FileNotFoundError(
            f"CSV não encontrado: {CSV_ENTRADA}"
        )

    with open(
        CSV_ENTRADA,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo)
        campos = leitor.fieldnames or []

        obrigatorios = [
            "timestamp",
            "eixo_x",
            "eixo_y",
            "open",
            "high",
            "low",
            "close",
            "atr",
            "largura_regiao",
            "amplitude",
            "corpo",
            "direcao",
            "evento"
        ]

        ausentes = [
            campo for campo in obrigatorios
            if campo not in campos
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes no CSV: {ausentes}"
            )

        registros = []

        for linha in leitor:
            try:
                registro = {
                    "timestamp": linha["timestamp"].strip(),
                    "eixo_x": int(linha["eixo_x"]),
                    "eixo_y": int(linha["eixo_y"]),
                    "open": float(linha["open"]),
                    "high": float(linha["high"]),
                    "low": float(linha["low"]),
                    "close": float(linha["close"]),
                    "atr": float(linha["atr"]),
                    "largura_regiao": float(
                        linha["largura_regiao"]
                    ),
                    "amplitude": float(linha["amplitude"]),
                    "corpo": float(linha["corpo"]),
                    "direcao": linha["direcao"],
                    "evento": linha["evento"]
                }

                if registro["timestamp"]:
                    registros.append(registro)

            except (ValueError, TypeError, KeyError):
                continue

    return registros


def registrar_velas(conexao, registros):
    inseridas = 0

    for registro in registros:
        cursor = conexao.execute(
            """
            INSERT OR IGNORE INTO velas (
                timestamp,
                eixo_x,
                eixo_y,
                open,
                high,
                low,
                close,
                atr,
                largura_regiao,
                amplitude,
                corpo,
                direcao,
                evento,
                processado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                registro["timestamp"],
                registro["eixo_x"],
                registro["eixo_y"],
                registro["open"],
                registro["high"],
                registro["low"],
                registro["close"],
                registro["atr"],
                registro["largura_regiao"],
                registro["amplitude"],
                registro["corpo"],
                registro["direcao"],
                registro["evento"],
                datetime.now(timezone.utc).isoformat()
            )
        )

        inseridas += cursor.rowcount

    return inseridas


def atualizar_regioes(conexao):
    conexao.execute(
        """
        INSERT INTO regioes (
            eixo_y,
            primeira_visita,
            ultima_visita,
            total_visitas
        )
        SELECT
            eixo_y,
            MIN(timestamp),
            MAX(timestamp),
            COUNT(*)
        FROM velas
        GROUP BY eixo_y
        ON CONFLICT(eixo_y) DO UPDATE SET
            primeira_visita = excluded.primeira_visita,
            ultima_visita = excluded.ultima_visita,
            total_visitas = excluded.total_visitas
        """
    )


def atualizar_transicoes(conexao):
    conexao.execute(
        """
        INSERT OR IGNORE INTO transicoes (
            regiao_origem,
            regiao_destino,
            timestamp_origem,
            timestamp_destino,
            intervalo_velas
        )
        SELECT
            anterior.eixo_y,
            atual.eixo_y,
            anterior.timestamp,
            atual.timestamp,
            atual.eixo_x - anterior.eixo_x
        FROM velas atual
        JOIN velas anterior
          ON anterior.timestamp = (
              SELECT v.timestamp
              FROM velas v
              WHERE v.timestamp < atual.timestamp
              ORDER BY v.timestamp DESC
              LIMIT 1
          )
        WHERE anterior.eixo_y != atual.eixo_y
        """
    )


def atualizar_controle(conexao):
    total = conexao.execute(
        "SELECT COUNT(*) FROM velas"
    ).fetchone()[0]

    ultima = conexao.execute(
        "SELECT MAX(timestamp) FROM velas"
    ).fetchone()[0]

    agora = datetime.now(timezone.utc).isoformat()

    dados = {
        "total_velas": str(total),
        "ultima_vela": str(ultima or ""),
        "ultima_atualizacao": agora,
        "versao_banco": "1"
    }

    for chave, valor in dados.items():
        conexao.execute(
            """
            INSERT INTO controle (chave, valor)
            VALUES (?, ?)
            ON CONFLICT(chave) DO UPDATE SET
                valor = excluded.valor
            """,
            (chave, valor)
        )


def gerar_resumo(conexao):
    total_velas = conexao.execute(
        "SELECT COUNT(*) FROM velas"
    ).fetchone()[0]

    total_regioes = conexao.execute(
        "SELECT COUNT(*) FROM regioes"
    ).fetchone()[0]

    total_transicoes = conexao.execute(
        "SELECT COUNT(*) FROM transicoes"
    ).fetchone()[0]

    total_eventos = conexao.execute(
        "SELECT COUNT(*) FROM eventos"
    ).fetchone()[0]

    ultima = conexao.execute(
        "SELECT MAX(timestamp) FROM velas"
    ).fetchone()[0]

    print("\n========== BANCO DO TABULEIRO ==========")
    print(f"Velas armazenadas: {total_velas}")
    print(f"Regiões identificadas: {total_regioes}")
    print(f"Transições registradas: {total_transicoes}")
    print(f"Eventos registrados: {total_eventos}")
    print(f"Última vela: {ultima}")
    print(f"Banco: {BANCO_SAIDA}")
    print("========================================\n")


def processar():
    registros = carregar_csv()

    conexao = conectar()

    try:
        criar_estrutura(conexao)

        novas = registrar_velas(
            conexao,
            registros
        )

        atualizar_regioes(conexao)
        atualizar_transicoes(conexao)
        atualizar_controle(conexao)

        conexao.commit()

        print(f"Registros recebidos: {len(registros)}")
        print(f"Novas velas armazenadas: {novas}")

        gerar_resumo(conexao)

    except Exception:
        conexao.rollback()
        raise

    finally:
        conexao.close()


if __name__ == "__main__":
    processar()
