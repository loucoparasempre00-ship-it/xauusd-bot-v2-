
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PROCESSADOR ESTATISTICO M5 - TABULEIRO

ENTRADAS:
    data/graficos.db.gz
    data/eventos_bordas_m5.csv

SAIDA OFICIAL:
    data/estatisticas_m5.db.gz

TABELAS CRIADAS:
    estatisticas_regioes_m5
    transicoes_regioes_m5
    resumo_estatistico_m5
    execucoes_processador_m5

REGRAS:
    X = tempo cronologico.
    Y = preco.
    Largura fixa das regioes = US$ 1,50.
    ATR nao altera a largura.
    Nao cria banco paralelo.
    Nao modifica graficos.db.gz.
    Nao executa ordens.
    CSV de eventos e somente entrada intermediaria.
"""

import csv
import gzip
import json
import math
import os
import sqlite3
import tempfile

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

BANCO_GRAFICOS = DATA / "graficos.db.gz"
BANCO_ESTATISTICAS = DATA / "estatisticas_m5.db.gz"

ARQUIVO_EVENTOS = DATA / "eventos_bordas_m5.csv"

LARGURA_REGIAO = 1.50
MIN_AMOSTRAS = 20
Z_WILSON = 1.96

TIMEFRAME = "M5"
VERSAO = 1


def agora():
    return datetime.now(timezone.utc).isoformat()


def numero(valor, campo):
    try:
        resultado = float(
            str(valor).strip().replace(",", ".")
        )
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor invalido em {campo}: {valor}"
        )

    if not math.isfinite(resultado):
        raise ValueError(
            f"Valor nao finito em {campo}: {valor}"
        )

    return resultado


def encontrar_coluna(colunas, alternativas):
    mapa = {
        str(coluna).lower().strip(): coluna
        for coluna in colunas
    }

    for alternativa in alternativas:
        encontrada = mapa.get(alternativa.lower())

        if encontrada is not None:
            return encontrada

    return None


def abrir_banco_gzip(caminho_gzip):
    """
    Descompacta um SQLite gzip para arquivo temporario.
    O banco original nao e alterado.
    """

    temporario = tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False
    )

    caminho = temporario.name
    temporario.close()

    try:
        with gzip.open(caminho_gzip, "rb") as origem:
            with open(caminho, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

    except Exception:
        if os.path.exists(caminho):
            os.remove(caminho)
        raise

    return caminho


def compactar_banco(caminho_sqlite, destino_gzip):
    """
    Compactacao gzip com substituicao atomica.
    """

    DATA.mkdir(parents=True, exist_ok=True)

    temporario = tempfile.NamedTemporaryFile(
        suffix=".gz",
        dir=DATA,
        delete=False
    )

    caminho_temp = temporario.name
    temporario.close()

    try:
        with open(caminho_sqlite, "rb") as origem:
            with gzip.open(caminho_temp, "wb") as destino:
                while True:
                    bloco = origem.read(1024 * 1024)

                    if not bloco:
                        break

                    destino.write(bloco)

        os.replace(caminho_temp, destino_gzip)

    finally:
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)


def carregar_velas():
    if not BANCO_GRAFICOS.is_file():
        raise FileNotFoundError(
            f"Banco de graficos nao encontrado: {BANCO_GRAFICOS}"
        )

    caminho_temp = abrir_banco_gzip(BANCO_GRAFICOS)

    try:
        conexao = sqlite3.connect(caminho_temp)
        conexao.row_factory = sqlite3.Row

        tabelas = conexao.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

        nomes = [linha["name"] for linha in tabelas]

        if "candles" not in nomes:
            raise RuntimeError(
                "Tabela candles nao encontrada em graficos.db.gz."
            )

        colunas = [
            linha["name"]
            for linha in conexao.execute(
                "PRAGMA table_info(candles)"
            ).fetchall()
        ]

        campos = {
            "timeframe": encontrar_coluna(
                colunas, ["timeframe", "periodo"]
            ),
            "timestamp": encontrar_coluna(
                colunas, ["timestamp", "datetime", "data_hora"]
            ),
            "open": encontrar_coluna(
                colunas, ["open", "abertura"]
            ),
            "high": encontrar_coluna(
                colunas, ["high", "maxima", "máxima"]
            ),
            "low": encontrar_coluna(
                colunas, ["low", "minima", "mínima"]
            ),
            "close": encontrar_coluna(
                colunas, ["close", "fechamento"]
            ),
        }

        ausentes = [
            nome for nome, coluna in campos.items()
            if coluna is None
        ]

        if ausentes:
            raise RuntimeError(
                "Colunas ausentes em candles: "
                + ", ".join(ausentes)
            )

        consulta = f"""
            SELECT
                "{campos['timeframe']}" AS timeframe,
                "{campos['timestamp']}" AS timestamp,
                "{campos['open']}" AS open,
                "{campos['high']}" AS high,
                "{campos['low']}" AS low,
                "{campos['close']}" AS close
            FROM candles
            ORDER BY "{campos['timestamp']}" ASC
        """

        registros = conexao.execute(consulta).fetchall()
        conexao.close()

        velas_por_tempo = {}

        for linha in registros:
            periodo = str(
                linha["timeframe"]
            ).strip().lower()

            if periodo not in ("5min", "5m", "m5"):
                continue

            try:
                timestamp = str(
                    linha["timestamp"]
                ).strip()

                if not timestamp:
                    continue

                vela = {
                    "timestamp": timestamp,
                    "open": numero(linha["open"], "open"),
                    "high": numero(linha["high"], "high"),
                    "low": numero(linha["low"], "low"),
                    "close": numero(linha["close"], "close"),
                }

                if min(
                    vela["open"],
                    vela["high"],
                    vela["low"],
                    vela["close"]
                ) <= 0:
                    continue

                if vela["high"] < vela["low"]:
                    continue

                if vela["high"] < max(
                    vela["open"], vela["close"]
                ):
                    continue

                if vela["low"] > min(
                    vela["open"], vela["close"]
                ):
                    continue

                velas_por_tempo[timestamp] = vela

            except (ValueError, TypeError):
                continue

        velas = sorted(
            velas_por_tempo.values(),
            key=lambda item: item["timestamp"]
        )

        if len(velas) < 2:
            raise RuntimeError(
                "Historico M5 insuficiente."
            )

        return velas

    finally:
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)


def carregar_eventos():
    if not ARQUIVO_EVENTOS.is_file():
        raise FileNotFoundError(
            "Arquivo de eventos nao encontrado: "
            f"{ARQUIVO_EVENTOS}"
        )

    eventos = []
    descartados = 0

    with ARQUIVO_EVENTOS.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:

        leitor = csv.DictReader(arquivo)

        obrigatorias = [
            "timestamp",
            "nivel_borda",
            "evento",
        ]

        campos = leitor.fieldnames or []

        ausentes = [
            campo for campo in obrigatorias
            if campo not in campos
        ]

        if ausentes:
            raise ValueError(
                "Colunas ausentes nos eventos: "
                + ", ".join(ausentes)
            )

        for linha in leitor:
            try:
                timestamp = str(
                    linha["timestamp"] or ""
                ).strip()

                tipo = str(
                    linha["evento"] or ""
                ).strip().upper()

                if not timestamp or not tipo:
                    descartados += 1
                    continue

                nivel = numero(
                    linha["nivel_borda"],
                    "nivel_borda"
                )

                eventos.append({
                    "timestamp": timestamp,
                    "nivel_borda": nivel,
                    "evento": tipo,
                })

            except (ValueError, TypeError, KeyError):
                descartados += 1

    return eventos, descartados


def indice_regiao(preco):
    return math.floor(
        preco / LARGURA_REGIAO
    )


def limites_regiao(indice):
    inferior = indice * LARGURA_REGIAO
    superior = inferior + LARGURA_REGIAO

    return (
        round(inferior, 5),
        round(superior, 5)
    )


def calcular_wilson(total, sucessos):
    if total <= 0:
        return 0.0

    p = sucessos / total
    z2 = Z_WILSON ** 2

    centro = p + z2 / (2 * total)

    margem = Z_WILSON * math.sqrt(
        (p * (1 - p) / total)
        + z2 / (4 * total ** 2)
    )

    denominador = 1 + z2 / total

    return round(
        max(
            0.0,
            (centro - margem) / denominador
        ),
        6
    )


def criar_estatisticas_regioes(velas, eventos):
    regioes = defaultdict(
        lambda: {
            "visitas": 0,
            "toques": 0,
            "rejeicoes": 0,
            "rompimentos": 0,
            "rejeicoes_superiores": 0,
            "rejeicoes_inferiores": 0,
            "rompimentos_alta": 0,
            "rompimentos_baixa": 0,
            "toques_superiores": 0,
            "toques_inferiores": 0,
        }
    )

    # Visitas sao calculadas pelo fechamento de cada vela.
    for vela in velas:
        indice = indice_regiao(vela["close"])
        regioes[indice]["visitas"] += 1

    # Eventos sao associados a faixa de preco da borda.
    for evento in eventos:
        indice = indice_regiao(
            evento["nivel_borda"]
        )

        dados = regioes[indice]
        tipo = evento["evento"]

        if tipo == "TOQUE_SUPERIOR":
            dados["toques"] += 1
            dados["toques_superiores"] += 1

        elif tipo == "TOQUE_INFERIOR":
            dados["toques"] += 1
            dados["toques_inferiores"] += 1

        elif tipo == "REJEICAO_SUPERIOR":
            dados["rejeicoes"] += 1
            dados["rejeicoes_superiores"] += 1

        elif tipo == "REJEICAO_INFERIOR":
            dados["rejeicoes"] += 1
            dados["rejeicoes_inferiores"] += 1

        elif tipo == "ROMPIMENTO_ALTA":
            dados["rompimentos"] += 1
            dados["rompimentos_alta"] += 1

        elif tipo == "ROMPIMENTO_BAIXA":
            dados["rompimentos"] += 1
            dados["rompimentos_baixa"] += 1

    resultados = []

    for indice, dados in sorted(regioes.items()):
        inferior, superior = limites_regiao(indice)

        testes = (
            dados["toques"]
            + dados["rejeicoes"]
            + dados["rompimentos"]
        )

        prob_rejeicao = (
            dados["rejeicoes"] / testes
            if testes else 0.0
        )

        prob_rompimento = (
            dados["rompimentos"] / testes
            if testes else 0.0
        )

        prob_toque = (
            dados["toques"] / testes
            if testes else 0.0
        )

        limite_wilson = calcular_wilson(
            testes,
            dados["rejeicoes"]
        )

        if testes < MIN_AMOSTRAS:
            classificacao = "AMOSTRA_INSUFICIENTE"

        elif dados["rompimentos"] > dados["rejeicoes"]:
            classificacao = "BORDA_FRAGIL"

        elif prob_rejeicao >= 0.60:
            classificacao = "BORDA_FORTE"

        elif dados["rejeicoes"] > 0:
            classificacao = "BORDA_EM_OBSERVACAO"

        else:
            classificacao = "BORDA_NAO_CONFIRMADA"

        resultados.append({
            "regiao": indice,
            "limite_inferior": inferior,
            "limite_superior": superior,
            "visitas": dados["visitas"],
            "toques": dados["toques"],
            "rejeicoes": dados["rejeicoes"],
            "rompimentos": dados["rompimentos"],
            "prob_toque": round(prob_toque, 6),
            "prob_rejeicao": round(prob_rejeicao, 6),
            "prob_rompimento": round(prob_rompimento, 6),
            "wilson_rejeicao_inferior": limite_wilson,
            "amostras": testes,
            "amostra_suficiente": int(
                testes >= MIN_AMOSTRAS
            ),
            "rejeicoes_superiores": dados[
                "rejeicoes_superiores"
            ],
            "rejeicoes_inferiores": dados[
                "rejeicoes_inferiores"
            ],
            "rompimentos_alta": dados[
                "rompimentos_alta"
            ],
            "rompimentos_baixa": dados[
                "rompimentos_baixa"
            ],
            "toques_superiores": dados[
                "toques_superiores"
            ],
            "toques_inferiores": dados[
                "toques_inferiores"
            ],
            "classificacao": classificacao,
        })

    return resultados


def calcular_transicoes(velas):
    """
    Conta mudancas reais de regiao entre fechamentos.
    Permanencia consecutiva na mesma regiao nao cria
    uma nova transicao.
    """

    sequencia = []

    for vela in velas:
        indice = indice_regiao(vela["close"])

        if not sequencia or sequencia[-1] != indice:
            sequencia.append(indice)

    contagem = Counter()
    saidas = Counter()
    visitas = Counter(sequencia)

    for origem, destino in zip(
        sequencia[:-1],
        sequencia[1:]
    ):
        contagem[(origem, destino)] += 1
        saidas[origem] += 1

    resultados = []

    for (origem, destino), quantidade in sorted(
        contagem.items()
    ):
        total = saidas[origem]

        probabilidade = (
            quantidade / total
            if total else 0.0
        )

        resultados.append({
            "origem": origem,
            "destino": destino,
            "transicoes": quantidade,
            "saidas_origem": total,
            "probabilidade": round(probabilidade, 6),
            "visitas_origem": visitas[origem],
            "amostra_suficiente": int(
                total >= MIN_AMOSTRAS
            ),
        })

    return resultados


def criar_resumo(velas, regioes, transicoes,
                 eventos, descartados_eventos):

    contagem_eventos = Counter(
        evento["evento"]
        for evento in eventos
    )

    regioes_validas = [
        item for item in regioes
        if item["amostra_suficiente"] == 1
    ]

    melhores_rejeicoes = sorted(
        regioes_validas,
        key=lambda item: (
            item["prob_rejeicao"],
            item["amostras"]
        ),
        reverse=True
    )[:10]

    melhores_transicoes = sorted(
        [
            item for item in transicoes
            if item["amostra_suficiente"] == 1
        ],
        key=lambda item: (
            item["probabilidade"],
            item["transicoes"]
        ),
        reverse=True
    )[:15]

    return {
        "timeframe": TIMEFRAME,
        "versao": VERSAO,
        "atualizado_em": agora(),

        "metodo": {
            "eixo_x": "Tempo cronologico",
            "eixo_y": "Preco",
            "largura_regiao_usd": LARGURA_REGIAO,
            "atr_altera_largura": False,
            "intervalo_confianca": "Wilson 95%",
            "minimo_amostras": MIN_AMOSTRAS,
        },

        "resumo": {
            "total_velas": len(velas),
            "total_regioes": len(regioes),
            "total_visitas": sum(
                item["visitas"] for item in regioes
            ),
            "total_eventos": len(eventos),
            "total_transicoes": sum(
                item["transicoes"] for item in transicoes
            ),
            "regioes_com_amostra_suficiente": len(
                regioes_validas
            ),
            "transicoes_com_amostra_suficiente": sum(
                item["amostra_suficiente"]
                for item in transicoes
            ),
            "eventos_descartados": descartados_eventos,
        },

        "eventos": dict(contagem_eventos),

        "melhores_regioes_rejeicao": melhores_rejeicoes,

        "transicoes_mais_frequentes": melhores_transicoes,

        "observacao": (
            "Probabilidades representam frequencias historicas. "
            "Nao garantem resultados futuros."
        ),
    }


def preparar_banco(conexao):
    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS estatisticas_regioes_m5 (
            regiao INTEGER PRIMARY KEY,
            limite_inferior REAL NOT NULL,
            limite_superior REAL NOT NULL,
            visitas INTEGER NOT NULL,
            toques INTEGER NOT NULL,
            rejeicoes INTEGER NOT NULL,
            rompimentos INTEGER NOT NULL,
            prob_toque REAL NOT NULL,
            prob_rejeicao REAL NOT NULL,
            prob_rompimento REAL NOT NULL,
            wilson_rejeicao_inferior REAL NOT NULL,
            amostras INTEGER NOT NULL,
            amostra_suficiente INTEGER NOT NULL,
            rejeicoes_superiores INTEGER NOT NULL,
            rejeicoes_inferiores INTEGER NOT NULL,
            rompimentos_alta INTEGER NOT NULL,
            rompimentos_baixa INTEGER NOT NULL,
            toques_superiores INTEGER NOT NULL,
            toques_inferiores INTEGER NOT NULL,
            classificacao TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
        """
    )

    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS transicoes_regioes_m5 (
            origem INTEGER NOT NULL,
            destino INTEGER NOT NULL,
            transicoes INTEGER NOT NULL,
            saidas_origem INTEGER NOT NULL,
            probabilidade REAL NOT NULL,
            visitas_origem INTEGER NOT NULL,
            amostra_suficiente INTEGER NOT NULL,
            atualizado_em TEXT NOT NULL,
            PRIMARY KEY (origem, destino)
        )
        """
    )

    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS resumo_estatistico_m5 (
            timeframe TEXT PRIMARY KEY,
            versao INTEGER NOT NULL,
            atualizado_em TEXT NOT NULL,
            total_velas INTEGER NOT NULL,
            total_regioes INTEGER NOT NULL,
            total_visitas INTEGER NOT NULL,
            total_eventos INTEGER NOT NULL,
            total_transicoes INTEGER NOT NULL,
            dados_json TEXT NOT NULL
        )
        """
    )

    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS execucoes_processador_m5 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            iniciado_em TEXT NOT NULL,
            concluido_em TEXT NOT NULL,
            total_velas INTEGER NOT NULL,
            total_eventos INTEGER NOT NULL,
            total_regioes INTEGER NOT NULL,
            total_transicoes INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )


def gravar_resultados(regioes, transicoes, resumo):
    DATA.mkdir(parents=True, exist_ok=True)

    if BANCO_ESTATISTICAS.exists():
        caminho_temp = abrir_banco_gzip(
            BANCO_ESTATISTICAS
        )
    else:
        temporario = tempfile.NamedTemporaryFile(
            suffix=".db",
            delete=False
        )
        caminho_temp = temporario.name
        temporario.close()

    iniciado = agora()
    concluido = agora()

    try:
        conexao = sqlite3.connect(caminho_temp)

        try:
            conexao.execute("PRAGMA journal_mode=DELETE")

            preparar_banco(conexao)

            # Atualiza a fotografia atual das estatisticas.
            conexao.execute(
                "DELETE FROM estatisticas_regioes_m5"
            )

            conexao.execute(
                "DELETE FROM transicoes_regioes_m5"
            )

            conexao.execute(
                """
                DELETE FROM resumo_estatistico_m5
                WHERE timeframe = ?
                """,
                (TIMEFRAME,)
            )

            for item in regioes:
                conexao.execute(
                    """
                    INSERT INTO estatisticas_regioes_m5 (
                        regiao,
                        limite_inferior,
                        limite_superior,
                        visitas,
                        toques,
                        rejeicoes,
                        rompimentos,
                        prob_toque,
                        prob_rejeicao,
                        prob_rompimento,
                        wilson_rejeicao_inferior,
                        amostras,
                        amostra_suficiente,
                        rejeicoes_superiores,
                        rejeicoes_inferiores,
                        rompimentos_alta,
                        rompimentos_baixa,
                        toques_superiores,
                        toques_inferiores,
                        classificacao,
                        atualizado_em
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        item["regiao"],
                        item["limite_inferior"],
                        item["limite_superior"],
                        item["visitas"],
                        item["toques"],
                        item["rejeicoes"],
                        item["rompimentos"],
                        item["prob_toque"],
                        item["prob_rejeicao"],
                        item["prob_rompimento"],
                        item["wilson_rejeicao_inferior"],
                        item["amostras"],
                        item["amostra_suficiente"],
                        item["rejeicoes_superiores"],
                        item["rejeicoes_inferiores"],
                        item["rompimentos_alta"],
                        item["rompimentos_baixa"],
                        item["toques_superiores"],
                        item["toques_inferiores"],
                        item["classificacao"],
                        concluido,
                    )
                )

            for item in transicoes:
                conexao.execute(
                    """
                    INSERT INTO transicoes_regioes_m5 (
                        origem,
                        destino,
                        transicoes,
                        saidas_origem,
                        probabilidade,
                        visitas_origem,
                        amostra_suficiente,
                        atualizado_em
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["origem"],
                        item["destino"],
                        item["transicoes"],
                        item["saidas_origem"],
                        item["probabilidade"],
                        item["visitas_origem"],
                        item["amostra_suficiente"],
                        concluido,
                    )
                )

            dados_resumo = resumo["resumo"]

            conexao.execute(
                """
                INSERT INTO resumo_estatistico_m5 (
                    timeframe,
                    versao,
                    atualizado_em,
                    total_velas,
                    total_regioes,
                    total_visitas,
                    total_eventos,
                    total_transicoes,
                    dados_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TIMEFRAME,
                    VERSAO,
                    concluido,
                    dados_resumo["total_velas"],
                    dados_resumo["total_regioes"],
                    dados_resumo["total_visitas"],
                    dados_resumo["total_eventos"],
                    dados_resumo["total_transicoes"],
                    json.dumps(
                        resumo,
                        ensure_ascii=False
                    ),
                )
            )

            conexao.execute(
                """
                INSERT INTO execucoes_processador_m5 (
                    iniciado_em,
                    concluido_em,
                    total_velas,
                    total_eventos,
                    total_regioes,
                    total_transicoes,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iniciado,
                    concluido,
                    dados_resumo["total_velas"],
                    dados_resumo["total_eventos"],
                    dados_resumo["total_regioes"],
                    dados_resumo["total_transicoes"],
                    "SUCESSO",
                )
            )

            conexao.commit()

            conexao.execute("VACUUM")
            conexao.commit()

        except Exception:
            conexao.rollback()
            raise

        finally:
            conexao.close()

        compactar_banco(
            caminho_temp,
            BANCO_ESTATISTICAS
        )

    finally:
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)


def processar():
    print("========================================")
    print("PROCESSADOR ESTATISTICO M5")
    print("========================================")

    print("Lendo velas do banco de graficos...")

    velas = carregar_velas()

    print("Velas M5:", len(velas))

    print("Lendo eventos do detector...")

    eventos, descartados = carregar_eventos()

    print("Eventos validos:", len(eventos))
    print("Eventos descartados:", descartados)

    print("Calculando estatisticas das regioes...")

    regioes = criar_estatisticas_regioes(
        velas,
        eventos
    )

    print("Calculando transicoes...")

    transicoes = calcular_transicoes(velas)

    print("Montando resumo estatistico...")

    resumo = criar_resumo(
        velas,
        regioes,
        transicoes,
        eventos,
        descartados
    )

    print("Gravando no banco oficial de estatisticas...")

    gravar_resultados(
        regioes,
        transicoes,
        resumo
    )

    print("----------------------------------------")
    print("REGIOES:", len(regioes))
    print("TRANSICOES:", len(transicoes))
    print(
        "REGIOES COM AMOSTRA SUFICIENTE:",
        resumo["resumo"][
            "regioes_com_amostra_suficiente"
        ]
    )
    print("----------------------------------------")
    print("BANCO:", BANCO_ESTATISTICAS)
    print("CSV DE SAIDA: NENHUM")
    print("BANCO PARALELO: NAO")
    print("BANCO DE GRAFICOS ALTERADO: NAO")
    print("OUTROS BANCOS OFICIAIS ALTERADOS: NAO")
    print("----------------------------------------")
    print("PROCESSAMENTO CONCLUIDO")
    print("========================================")


if __name__ == "__main__":
    processar()
