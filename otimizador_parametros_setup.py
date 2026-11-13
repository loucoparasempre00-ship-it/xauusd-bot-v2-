
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OTIMIZADOR DO SETUP MATEMATICO XAUUSD - CALIBRACAO INDEPENDENTE

Fonte: data/graficos.db.gz
Destino: data/sinais.db.gz

- Janela movel de 48 candles M5.
- Notas LONG e SHORT independentes.
- EMA 9/21, RSI 14 e MACD em M5, M15 e H1.
- Soma das notas, sem media.
- Pesos ausentes, zero ou invalidos recebem 1.
- Dois gatilhos independentes: LONG e SHORT.
- Busca de gatilhos de 5 a 90.
- Prioriza vantagem direcional acima de 50%.
- Se nao houver vantagem no treino, registra a melhor alternativa,
  mas a aprovacao continua dependendo do validador.
- Cada leitura historica usa somente informacoes disponiveis naquele instante.
- Nao consulta bancos de operacoes nem Gemini.
"""

import bisect
import gzip
import json
import math
import os
import shutil
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timedelta, timezone


BASE = Path(__file__).resolve().parent
GRAFICOS = BASE / "data" / "graficos.db.gz"
SINAIS = BASE / "data" / "sinais.db.gz"

JANELA = 48
HORIZONTE = 3
PERCENTUAL_TREINO = 0.70

# Cada lado tem sua propria regulagem.
GATILHOS = tuple(range(5, 91))

MIN_AMOSTRAS = 100
MIN_SINAIS_TREINO = 50
PERCENTIL_ESCALA = 0.85

TIMEFRAMES = {
    "M5": 5,
    "M15": 15,
    "H1": 60,
}


def agora_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numero(valor):
    try:
        n = float(valor)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def parse_data(valor):
    texto = str(valor).strip()

    try:
        data = datetime.fromisoformat(
            texto.replace("Z", "+00:00")
        )

        if data.tzinfo is not None:
            data = data.astimezone(timezone.utc).replace(tzinfo=None)

        return data

    except ValueError:
        return datetime.strptime(
            texto,
            "%Y-%m-%d %H:%M:%S"
        )


def percentil(valores, p):
    dados = sorted(
        v for v in valores
        if v is not None and math.isfinite(v)
    )

    if not dados:
        return 0.0

    pos = (len(dados) - 1) * p
    baixo = int(pos)
    alto = min(baixo + 1, len(dados) - 1)

    return dados[baixo] + (
        dados[alto] - dados[baixo]
    ) * (pos - baixo)


def abrir_sqlite_gz(caminho, exigir_existencia=True):
    if exigir_existencia and not caminho.is_file():
        raise FileNotFoundError(
            f"Banco oficial nao encontrado: {caminho}"
        )

    temporario = tempfile.TemporaryDirectory(
        prefix="xau_setup_"
    )

    db_path = Path(temporario.name) / caminho.name.replace(
        ".gz", ""
    )

    if caminho.exists():
        with gzip.open(caminho, "rb") as origem:
            with open(db_path, "wb") as destino:
                shutil.copyfileobj(origem, destino)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    return temporario, db_path, con


def compactar_sqlite_gz(temporario, db_path, destino):
    destino.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporario_gz = destino.with_name(
        destino.name + ".tmp"
    )

    try:
        with open(db_path, "rb") as origem:
            with open(temporario_gz, "wb") as bruto:
                with gzip.GzipFile(
                    fileobj=bruto,
                    mode="wb",
                    mtime=0
                ) as saida:
                    shutil.copyfileobj(origem, saida)

        os.replace(temporario_gz, destino)

    finally:
        if temporario_gz.exists():
            temporario_gz.unlink()

        temporario.cleanup()


def fechar_leitura(temporario, con):
    try:
        con.close()
    finally:
        temporario.cleanup()


def criar_tabelas_setup(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_parametros (
            versao INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            pesos_json TEXT NOT NULL,
            limite_long REAL NOT NULL,
            limite_short REAL NOT NULL,
            horizonte_velas INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            treino_json TEXT NOT NULL,
            validacao_json TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_decisoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_utc TEXT NOT NULL,
            timestamp_candle TEXT NOT NULL,
            decisao TEXT NOT NULL,
            pontuacao REAL NOT NULL,
            limite REAL NOT NULL,
            parametro_versao INTEGER NOT NULL,
            indicadores_json TEXT NOT NULL
        )
    """)

    # Migracoes somente aditivas.
    colunas = {
        r["name"]
        for r in con.execute(
            "PRAGMA table_info(setup_decisoes)"
        )
    }

    novas = {
        "nota_long": "REAL NOT NULL DEFAULT 0",
        "nota_short": "REAL NOT NULL DEFAULT 0",
        "gatilho_long": "REAL NOT NULL DEFAULT 0",
        "gatilho_short": "REAL NOT NULL DEFAULT 0",
    }

    for nome, definicao in novas.items():
        if nome not in colunas:
            con.execute(
                f'ALTER TABLE setup_decisoes '
                f'ADD COLUMN "{nome}" {definicao}'
            )

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_setup_decisoes_data
        ON setup_decisoes(timestamp_candle)
    """)


def carregar_banco_graficos():
    temporario, db_path, con = abrir_sqlite_gz(GRAFICOS)

    try:
        tabelas = {
            r[0]
            for r in con.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type='table'
            """)
        }

        necessarias = {
            "candles",
            "indicadores_tecnicos"
        }

        if not necessarias.issubset(tabelas):
            faltam = necessarias - tabelas

            raise RuntimeError(
                "Faltam tabelas em graficos.db.gz: "
                + ", ".join(sorted(faltam))
            )

        candles = [
            dict(r)
            for r in con.execute("""
                SELECT timestamp, fechamento
                FROM candles
                WHERE timeframe='M5'
                ORDER BY timestamp
            """)
        ]

        indicadores = [
            dict(r)
            for r in con.execute("""
                SELECT
                    timeframe,
                    timestamp,
                    mme_9,
                    mme_21,
                    rsi_14,
                    macd_histograma
                FROM indicadores_tecnicos
                WHERE timeframe IN ('M5','M15','H1')
                ORDER BY timeframe, timestamp
            """)
        ]

    finally:
        fechar_leitura(temporario, con)

    if len(candles) < JANELA + HORIZONTE:
        raise RuntimeError(
            "Historico M5 insuficiente para a janela de 48 velas."
        )

    if not indicadores:
        raise RuntimeError(
            "Indicadores tecnicos nao encontrados."
        )

    return candles, indicadores


def construir_indices_indicadores(indicadores):
    indices = {}

    for tf, minutos in TIMEFRAMES.items():
        linhas = [
            r for r in indicadores
            if r["timeframe"] == tf
        ]

        tempos = [
            parse_data(r["timestamp"])
            for r in linhas
        ]

        indices[tf] = (
            tempos,
            linhas,
            minutos
        )

    return indices


def obter_indicador_fechado(
    indices,
    timeframe,
    fechamento_m5
):
    tempos, linhas, minutos = indices[timeframe]

    # O timestamp representa a abertura da vela.
    # So usa o indicador depois do fechamento completo.
    ultimo_inicio_permitido = (
        fechamento_m5 - timedelta(minutes=minutos)
    )

    pos = bisect.bisect_right(
        tempos,
        ultimo_inicio_permitido
    ) - 1

    return linhas[pos] if pos >= 0 else None


def extrair_valores_brutos(linhas, fechamento):
    resultado = {}

    if fechamento is None or fechamento <= 0:
        return resultado

    for tf in TIMEFRAMES:
        linha = linhas.get(tf)

        if not linha:
            continue

        ema9 = numero(linha.get("mme_9"))
        ema21 = numero(linha.get("mme_21"))
        rsi = numero(linha.get("rsi_14"))
        macd = numero(linha.get("macd_histograma"))

        if ema9 is not None and ema21 is not None:
            resultado[f"{tf}_EMA"] = (
                (ema9 - ema21) / fechamento
            ) * 10000.0

        if rsi is not None:
            resultado[f"{tf}_RSI"] = rsi - 50.0

        if macd is not None:
            resultado[f"{tf}_MACD"] = (
                macd / fechamento
            ) * 10000.0

    return resultado


def normalizar_janela(historico, atual):
    """
    Converte cada leitura em nota assinada de -10 a +10.
    A escala usa somente as ultimas 48 leituras disponiveis.
    """

    notas = {}

    for nome, valor in atual.items():
        serie = []

        for item in historico:
            leitura = numero(item.get(nome))

            if leitura is not None:
                serie.append(abs(leitura))

        escala = percentil(
            serie,
            PERCENTIL_ESCALA
        )

        if escala <= 1e-12 or valor is None:
            nota = 0.0
        else:
            intensidade = min(
                10.0,
                abs(valor) / escala * 10.0
            )

            nota = (
                intensidade
                if valor > 0
                else -intensidade
            )

        notas[nome] = round(nota, 4)

    return notas


def carregar_leituras():
    candles, indicadores = carregar_banco_graficos()

    indices = construir_indices_indicadores(
        indicadores
    )

    leituras_brutas = []

    for candle in candles:
        inicio = parse_data(candle["timestamp"])

        fechamento_m5 = (
            inicio + timedelta(minutes=5)
        )

        preco = numero(candle["fechamento"])

        if preco is None or preco <= 0:
            leituras_brutas.append({
                "timestamp": candle["timestamp"],
                "preco": None,
                "brutos": {},
            })
            continue

        linhas = {
            tf: obter_indicador_fechado(
                indices,
                tf,
                fechamento_m5
            )
            for tf in TIMEFRAMES
        }

        brutos = extrair_valores_brutos(
            linhas,
            preco
        )

        leituras_brutas.append({
            "timestamp": candle["timestamp"],
            "preco": preco,
            "brutos": brutos,
        })

    leituras = []

    for i, atual in enumerate(leituras_brutas):
        if i + 1 < JANELA:
            continue

        historico = [
            item["brutos"]
            for item in leituras_brutas[
                i - JANELA + 1:i + 1
            ]
        ]

        notas = normalizar_janela(
            historico,
            atual["brutos"]
        )

        if len(notas) < 3:
            continue

        leituras.append({
            "timestamp": atual["timestamp"],
            "preco": atual["preco"],
            "features": notas,
            "janela": JANELA,
        })

    if len(leituras) < MIN_AMOSTRAS:
        raise RuntimeError(
            f"Leituras historicas insuficientes: {len(leituras)}."
        )

    return leituras


def carregar_amostras():
    leituras = carregar_leituras()

    amostras = []

    for i in range(len(leituras) - HORIZONTE):
        atual = leituras[i]
        futuro = leituras[i + HORIZONTE]

        preco_atual = atual["preco"]
        preco_futuro = futuro["preco"]

        if not preco_atual or not preco_futuro:
            continue

        retorno = preco_futuro - preco_atual

        amostras.append({
            "timestamp": atual["timestamp"],
            "features": atual["features"],
            "retorno": retorno,
        })

    if len(amostras) < MIN_AMOSTRAS:
        raise RuntimeError(
            "Amostras validas insuficientes."
        )

    return amostras


def pontuar(features, pesos=None):
    pesos = pesos or {}

    nota_long = 0.0
    nota_short = 0.0
    usados = {}

    for nome, leitura in features.items():
        peso = numero(pesos.get(nome))

        # Peso ausente, zero ou invalido recebe 1.
        if peso is None or peso <= 0:
            peso = 1.0

        valor = numero(leitura)

        if valor is None:
            continue

        contribuicao = (
            max(-10.0, min(10.0, valor)) * peso
        )

        if contribuicao > 0:
            nota_long += contribuicao

        elif contribuicao < 0:
            nota_short += abs(contribuicao)

        usados[nome] = {
            "nota_assinada": round(valor, 4),
            "peso": peso,
            "contribuicao": round(contribuicao, 4),
        }

    return {
        "nota_long": round(nota_long, 4),
        "nota_short": round(nota_short, 4),
        "score_final": round(
            nota_long - nota_short,
            4
        ),
        "indicadores": usados,
    }


def avaliar_direcao(amostras, gatilho, direcao):
    selecionadas = []
    acertos = 0
    retornos = []

    for amostra in amostras:
        pontos = pontuar(
            amostra["features"]
        )

        score = pontos["score_final"]

        if direcao == "LONG" and score >= gatilho:
            selecionadas.append(amostra)
            retornos.append(amostra["retorno"])

            if amostra["retorno"] > 0:
                acertos += 1

        elif direcao == "SHORT" and score <= -gatilho:
            selecionadas.append(amostra)
            retornos.append(amostra["retorno"])

            if amostra["retorno"] < 0:
                acertos += 1

    total = len(selecionadas)

    taxa = (
        acertos / total
        if total
        else 0.0
    )

    cobertura = (
        total / len(amostras)
        if amostras
        else 0.0
    )

    media_retorno = (
        sum(retornos) / total
        if total
        else 0.0
    )

    return {
        "direcao": direcao,
        "gatilho": gatilho,
        "amostras": len(amostras),
        "sinais": total,
        "acertos": acertos,
        "erros": total - acertos,
        "taxa_acerto": round(taxa, 6),
        "cobertura": round(cobertura, 6),
        "media_movimento_preco": round(
            media_retorno,
            6
        ),
        # Mantido para compatibilidade com o validador.
        "objetivo": round(
            taxa * math.sqrt(cobertura),
            6
        ),
    }


def calibrar_gatilho(amostras, direcao):
    """
    Calibra LONG e SHORT de forma independente.

    Primeira prioridade:
    encontrar gatilhos com taxa de acerto >= 50%.

    Entre esses gatilhos:
    equilibrar vantagem direcional e cobertura,
    considerando tambem a quantidade de amostras.

    Se nenhum atingir 50%:
    escolher a melhor taxa observada com amostra minima.
    Essa escolha NAO significa aprovacao.
    O validador continua tendo a palavra final.
    """

    candidatos = []

    for gatilho in GATILHOS:
        resultado = avaliar_direcao(
            amostras,
            gatilho,
            direcao
        )

        if resultado["sinais"] < MIN_SINAIS_TREINO:
            continue

        total = resultado["sinais"]
        taxa = resultado["taxa_acerto"]
        cobertura = resultado["cobertura"]

        # Penaliza resultados baseados em poucas ocorrencias.
        confiabilidade = math.sqrt(
            total / (total + 100.0)
        )

        # Vantagem acima de 50%, ponderada pela cobertura.
        vantagem = taxa - 0.50

        objetivo_calibracao = (
            vantagem
            * math.sqrt(cobertura)
            * confiabilidade
        )

        resultado["objetivo_calibracao"] = round(
            objetivo_calibracao,
            8
        )

        resultado["confiabilidade_amostral"] = round(
            confiabilidade,
            6
        )

        candidatos.append(resultado)

    if not candidatos:
        raise RuntimeError(
            f"Nenhum gatilho com pelo menos "
            f"{MIN_SINAIS_TREINO} sinais para {direcao}. "
            "Nenhum limite sera reduzido automaticamente."
        )

    # Se existe pelo menos um candidato com 50% ou mais,
    # os candidatos abaixo de 50% nao disputam a regulagem.
    candidatos_com_vantagem = [
        r for r in candidatos
        if r["taxa_acerto"] >= 0.50
    ]

    if candidatos_com_vantagem:
        melhor = max(
            candidatos_com_vantagem,
            key=lambda r: (
                r["objetivo_calibracao"],
                r["taxa_acerto"],
                r["sinais"],
                r["cobertura"],
            )
        )

        criterio = (
            "VANTAGEM_50_PORCENTO_COM_COBERTURA"
        )

    else:
        # Sem candidato acima de 50%, escolhe a melhor
        # taxa observada, sem declarar vantagem.
        melhor = max(
            candidatos,
            key=lambda r: (
                r["taxa_acerto"],
                r["sinais"],
                r["cobertura"],
            )
        )

        criterio = (
            "SEM_VANTAGEM_50_PORCENTO_NO_TREINO"
        )

    resultado_final = dict(melhor)

    resultado_final["criterio_selecao"] = criterio
    resultado_final["candidatos_testados"] = len(candidatos)
    resultado_final["candidatos_com_50_porcento"] = len(
        candidatos_com_vantagem
    )

    resultado_final["menor_gatilho_testado"] = min(
        r["gatilho"] for r in candidatos
    )

    resultado_final["maior_gatilho_testado"] = max(
        r["gatilho"] for r in candidatos
    )

    return melhor["gatilho"], resultado_final


def salvar_parametros(parametros):
    temporario, db_path, con = abrir_sqlite_gz(
        SINAIS
    )

    try:
        criar_tabelas_setup(con)

        cursor = con.execute("""
            INSERT INTO setup_parametros (
                criado_utc,
                status,
                pesos_json,
                limite_long,
                limite_short,
                horizonte_velas,
                features_json,
                treino_json,
                validacao_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            agora_utc(),
            "PENDENTE_VALIDACAO",
            json.dumps(
                parametros["pesos"],
                ensure_ascii=False,
                sort_keys=True
            ),
            parametros["limite_long"],
            parametros["limite_short"],
            HORIZONTE,
            json.dumps(
                parametros["features"],
                ensure_ascii=False
            ),
            json.dumps(
                parametros["treino"],
                ensure_ascii=False
            ),
        ))

        versao = cursor.lastrowid

        con.commit()
        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS
        )

        return versao

    except Exception:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass

        temporario.cleanup()
        raise


def otimizar():
    amostras = carregar_amostras()

    corte = int(
        len(amostras) * PERCENTUAL_TREINO
    )

    # Purga tres velas antes da validacao.
    treino = amostras[
        :max(0, corte - HORIZONTE)
    ]

    if len(treino) < MIN_AMOSTRAS:
        raise RuntimeError(
            "Treino historico insuficiente."
        )

    # Os dois lados sao regulados separadamente.
    limite_long, resultado_long = calibrar_gatilho(
        treino,
        "LONG"
    )

    limite_short_abs, resultado_short = calibrar_gatilho(
        treino,
        "SHORT"
    )

    features = sorted({
        nome
        for amostra in treino
        for nome in amostra["features"]
    })

    pesos = {
        nome: 1
        for nome in features
    }

    parametros = {
        "pesos": pesos,

        "limite_long": float(limite_long),

        "limite_short": float(
            -limite_short_abs
        ),

        "features": {
            "indicadores": features,
            "janela_m5": JANELA,
            "normalizacao": (
                "percentil_85_das_ultimas_48_leituras"
            ),
            "nota_por_indicador": "de -10 a +10",
            "soma": (
                "long e short separados; "
                "score final = long - short"
            ),
            "horizonte_validacao": HORIZONTE,
            "calibracao": (
                "gatilhos LONG e SHORT independentes"
            ),
            "faixa_gatilhos": {
                "minimo": min(GATILHOS),
                "maximo": max(GATILHOS),
                "passo": 1,
            },
        },

        "treino": {
            "amostras_treino": len(treino),
            "amostras_total": len(amostras),
            "percentual_treino": PERCENTUAL_TREINO,
            "janela_m5": JANELA,
            "minimo_sinais_por_candidato": (
                MIN_SINAIS_TREINO
            ),
            "gatilho_long": resultado_long,
            "gatilho_short": resultado_short,
        },
    }

    versao = salvar_parametros(
        parametros
    )

    print(json.dumps({
        "versao": versao,
        "status": "PENDENTE_VALIDACAO",
        **parametros,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    otimizar()
