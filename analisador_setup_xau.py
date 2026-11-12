
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ANALISADOR DE SETUP XAUUSD

Fontes oficiais:
- data/graficos.db ou data/graficos.db.gz
- data/estatisticas_m5.db ou data/estatisticas_m5.db.gz

O histórico de operações NÃO é consultado aqui.
O histórico de operações pertence ao Condutor de Operações.

Modelo:
- Regressao logistica em Python puro.
- Treinamento historico M5.
- Calibracao probabilistica.
- Separacao cronologica entre treino, calibracao e teste.

Previsao:
- Proximas 3 velas M5 (15 minutos).

Saida:
- data/probabilidade_direcional_xau.json

Nao altera os bancos oficiais.
Nao analisa operacoes individualmente.
Nao executa ordens.
"""

import csv
import gzip
import io
import json
import math
import sqlite3
import tempfile

from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

HORIZONTE = 3
LARGURA_REGIAO = 1.50

MINIMO_AMOSTRAS = 240
MINIMO_TREINO = 100
MINIMO_CALIBRACAO = 30
MINIMO_TESTE = 30

EPOCAS = 700
TAXA_APRENDIZADO = 0.06
REGULARIZACAO = 0.02

SAIDA = DATA / "probabilidade_direcional_xau.json"


# ============================================================
# UTILIDADES
# ============================================================

def agora():
    return datetime.now(timezone.utc).isoformat()


def numero(valor, padrao=None):
    try:
        return float(
            str(valor).strip().replace(",", ".")
        )
    except (ValueError, TypeError):
        return padrao


def inteiro(valor, padrao=None):
    try:
        return int(float(str(valor).strip()))
    except (ValueError, TypeError):
        return padrao


def normalizar_nome(valor):
    return str(valor).strip().lower().replace("\\", "/")


def sigmoid(valor):
    valor = max(-30.0, min(30.0, valor))
    return 1.0 / (1.0 + math.exp(-valor))


# ============================================================
# ABRIR BANCO OFICIAL, INCLUSIVE COMPACTADO
# ============================================================

@contextmanager
def conectar_banco(nome):
    normal = DATA / f"{nome}.db"
    compactado = DATA / f"{nome}.db.gz"

    temporario = None

    if normal.is_file():
        caminho = normal

    elif compactado.is_file():
        temporario = tempfile.TemporaryDirectory(
            prefix=f"analisador_{nome}_"
        )

        caminho = Path(temporario.name) / f"{nome}.db"

        with gzip.open(compactado, "rb") as origem:
            caminho.write_bytes(origem.read())

    else:
        raise FileNotFoundError(
            f"Banco oficial ausente: {nome}.db ou {nome}.db.gz"
        )

    conexao = None

    try:
        conexao = sqlite3.connect(
            caminho.resolve().as_uri() + "?mode=ro",
            uri=True
        )

        conexao.row_factory = sqlite3.Row
        yield conexao

    finally:
        if conexao is not None:
            conexao.close()

        if temporario is not None:
            temporario.cleanup()


# ============================================================
# RECUPERAR ARQUIVOS ORIGINAIS DO BANCO
# ============================================================

def carregar_csv_banco(nome_banco, nomes_aceitos):
    """
    Recupera o conteudo original armazenado em arquivos_origem.
    Nao depende de CSV solto na pasta data.
    """

    nomes_aceitos = {
        normalizar_nome(nome)
        for nome in nomes_aceitos
    }

    with conectar_banco(nome_banco) as db:

        tabelas = {
            linha["name"]
            for linha in db.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type='table'
            """)
        }

        if "arquivos_origem" not in tabelas:
            raise RuntimeError(
                f"{nome_banco}.db sem tabela arquivos_origem."
            )

        registros = db.execute("""
            SELECT caminho, nome, conteudo
            FROM arquivos_origem
            ORDER BY caminho
        """).fetchall()

        for registro in registros:

            caminho = normalizar_nome(
                registro["caminho"]
            )

            nome = normalizar_nome(
                registro["nome"]
            )

            if nome not in nomes_aceitos and not any(
                caminho.endswith("/" + aceito)
                or caminho.endswith(aceito)
                for aceito in nomes_aceitos
            ):
                continue

            conteudo = registro["conteudo"]

            if conteudo is None:
                continue

            try:
                if isinstance(conteudo, str):
                    texto = conteudo
                else:
                    texto = bytes(conteudo).decode("utf-8-sig")

                leitor = csv.DictReader(
                    io.StringIO(texto)
                )

                if not leitor.fieldnames:
                    continue

                linhas = list(leitor)

                if linhas:
                    print(
                        f"Fonte carregada: {nome_banco}.db | "
                        f"{registro['nome']} | {len(linhas)} registros"
                    )

                    return linhas

            except (UnicodeDecodeError, csv.Error, TypeError):
                continue

    return []


# ============================================================
# CARREGAR TABULEIRO E ESTATISTICAS
# ============================================================

def carregar_dados():

    tabuleiro = carregar_csv_banco(
        "graficos",
        [
            "tabuleiro_m5.csv",
            "tabuleiro_estavel_m5.csv"
        ]
    )

    if not tabuleiro:
        raise RuntimeError(
            "O banco graficos.db nao possui tabuleiro M5."
        )

    estatisticas = {}

    arquivos_estatisticos = {
        "regioes": [
            "estatisticas_regioes_m5.csv"
        ],
        "transicoes": [
            "transicoes_probabilidade_m5.csv"
        ],
        "sequencias": [
            "sequencias_provaveis_m5.csv"
        ],
        "eventos": [
            "eventos_bordas_m5.csv"
        ],
        "resultados_eventos": [
            "resultados_eventos_m5.csv"
        ],
        "resumo_resultados": [
            "resumo_resultados_m5.csv",
            "resumo_estatistico_m5.csv"
        ]
    }

    for chave, nomes in arquivos_estatisticos.items():
        estatisticas[chave] = carregar_csv_banco(
            "estatisticas_m5",
            nomes
        )

    # O terceiro retorno e mantido para compatibilidade
    # com os arquivos que ja chamam carregar_dados().
    # Nenhum banco de operacoes e consultado.
    return tabuleiro, estatisticas, None


# ============================================================
# PREPARAR VELAS DO TABULEIRO
# ============================================================

def preparar_velas(registros):

    por_tempo = {}

    for linha in registros:
        try:
            timestamp = str(
                linha.get("timestamp", "")
            ).strip()

            if not timestamp:
                continue

            vela = {
                "timestamp": timestamp,
                "eixo_x": inteiro(linha.get("eixo_x")),
                "regiao": inteiro(linha.get("eixo_y")),
                "open": numero(linha.get("open")),
                "high": numero(linha.get("high")),
                "low": numero(linha.get("low")),
                "close": numero(linha.get("close")),
                "atr": numero(linha.get("atr"))
            }

            if any(
                vela[chave] is None
                for chave in (
                    "eixo_x", "regiao", "open",
                    "high", "low", "close"
                )
            ):
                continue

            if vela["high"] < vela["low"]:
                continue

            if vela["atr"] is not None and vela["atr"] < 0:
                continue

            por_tempo[timestamp] = vela

        except (ValueError, TypeError):
            continue

    velas = sorted(
        por_tempo.values(),
        key=lambda item: (
            item["eixo_x"],
            item["timestamp"]
        )
    )

    if len(velas) < 60:
        raise RuntimeError(
            "Historico do Tabuleiro insuficiente."
        )

    for anterior, atual in zip(velas, velas[1:]):
        if atual["eixo_x"] <= anterior["eixo_x"]:
            raise RuntimeError(
                "Eixo X duplicado ou fora de ordem."
            )

    return velas


# ============================================================
# INDICADORES
# ============================================================

def calcular_ema(valores, periodo):

    fator = 2.0 / (periodo + 1)
    resultado = []
    atual = valores[0]

    for valor in valores:
        atual = (
            valor * fator
            + atual * (1.0 - fator)
        )
        resultado.append(atual)

    return resultado


def calcular_rsi(fechamentos, periodo=14):

    saida = [50.0] * len(fechamentos)

    ganhos = []
    perdas = []

    for i in range(1, len(fechamentos)):

        variacao = (
            fechamentos[i] - fechamentos[i - 1]
        )

        ganhos.append(max(variacao, 0.0))
        perdas.append(max(-variacao, 0.0))

        if len(ganhos) < periodo:
            continue

        g = sum(ganhos[-periodo:]) / periodo
        p = sum(perdas[-periodo:]) / periodo

        if p == 0:
            saida[i] = 100.0
        else:
            rs = g / p
            saida[i] = 100.0 - 100.0 / (1.0 + rs)

    return saida


def calcular_atr(velas, periodo=14):

    trs = []
    saida = []

    for i, vela in enumerate(velas):

        if i == 0:
            tr = vela["high"] - vela["low"]
        else:
            anterior = velas[i - 1]["close"]

            tr = max(
                vela["high"] - vela["low"],
                abs(vela["high"] - anterior),
                abs(vela["low"] - anterior)
            )

        trs.append(tr)

        janela = trs[-periodo:]
        saida.append(sum(janela) / len(janela))

    return saida


# ============================================================
# ESTATISTICAS DO TABULEIRO
# ============================================================

def calcular_visitas(regioes, indice, janela=48):

    inicio = max(0, indice - janela + 1)
    atual = regioes[indice]

    return sum(
        1
        for regiao in regioes[inicio:indice + 1]
        if regiao == atual
    ) / float(janela)


def calcular_pressao_transicoes(regioes, indice, janela=100):

    inicio = max(1, indice - janela + 1)

    altas = 0
    baixas = 0

    for i in range(inicio, indice + 1):

        diferenca = (
            regioes[i] - regioes[i - 1]
        )

        if diferenca > 0:
            altas += 1
        elif diferenca < 0:
            baixas += 1

    total = altas + baixas

    if total == 0:
        return 0.0

    return (altas - baixas) / total


def calcular_caracteristicas(velas):

    fechamentos = [
        v["close"] for v in velas
    ]

    regioes = [
        v["regiao"] for v in velas
    ]

    ema9 = calcular_ema(fechamentos, 9)
    ema21 = calcular_ema(fechamentos, 21)
    rsi = calcular_rsi(fechamentos, 14)
    atr = calcular_atr(velas, 14)

    caracteristicas = []

    for i, vela in enumerate(velas):

        if i < 30:
            caracteristicas.append(None)
            continue

        preco = vela["close"]
        volatilidade = atr[i]

        if volatilidade <= 0:
            caracteristicas.append(None)
            continue

        amplitude = max(
            vela["high"] - vela["low"],
            0.000001
        )

        corpo = vela["close"] - vela["open"]

        pavio_superior = (
            vela["high"]
            - max(vela["open"], vela["close"])
        )

        pavio_inferior = (
            min(vela["open"], vela["close"])
            - vela["low"]
        )

        posicao_regiao = (
            preco - regioes[i] * LARGURA_REGIAO
        ) / LARGURA_REGIAO

        posicao_regiao = max(
            0.0,
            min(1.0, posicao_regiao)
        )

        visitas = calcular_visitas(
            regioes, i
        )

        transicoes = calcular_pressao_transicoes(
            regioes, i
        )

        movimentos = []

        for j in range(max(1, i - 4), i + 1):
            movimentos.append(
                fechamentos[j] - fechamentos[j - 1]
            )

        positivas = sum(1 for x in movimentos if x > 0)
        negativas = sum(1 for x in movimentos if x < 0)

        total = positivas + negativas

        sequencia = (
            (positivas - negativas) / total
            if total else 0.0
        )

        valores = [
            (ema9[i] - ema21[i]) / volatilidade,
            (ema9[i] - ema9[i - 3]) / volatilidade,
            (ema21[i] - ema21[i - 3]) / volatilidade,
            (rsi[i] - 50.0) / 50.0,
            (rsi[i] - rsi[i - 3]) / 50.0,
            (preco - fechamentos[i - 1]) / volatilidade,
            (preco - fechamentos[i - 3]) / volatilidade,
            (preco - fechamentos[i - 6]) / volatilidade,
            volatilidade / preco * 100.0,
            corpo / volatilidade,
            amplitude / volatilidade,
            (pavio_inferior - pavio_superior) / amplitude,
            (posicao_regiao * 2.0) - 1.0,
            (1.0 - posicao_regiao) * LARGURA_REGIAO / volatilidade,
            posicao_regiao * LARGURA_REGIAO / volatilidade,
            visitas,
            transicoes,
            sequencia
        ]

        caracteristicas.append(valores)

    return caracteristicas, atr


NOMES_VARIAVEIS = [
    "tendencia_ema",
    "inclinacao_ema9",
    "inclinacao_ema21",
    "rsi",
    "evolucao_rsi",
    "velocidade_1",
    "velocidade_3",
    "velocidade_6",
    "volatilidade_atr",
    "corpo_candle",
    "amplitude_candle",
    "equilibrio_pavios",
    "posicao_tabuleiro",
    "distancia_borda_superior",
    "distancia_borda_inferior",
    "visitas_regiao",
    "pressao_transicoes",
    "sequencia_candles"
]


# ============================================================
# MONTAR AMOSTRAS HISTORICAS
# ============================================================

def montar_amostras(velas, caracteristicas):

    amostras = []

    for i in range(len(velas) - HORIZONTE):

        x = caracteristicas[i]

        if x is None:
            continue

        variacao = (
            velas[i + HORIZONTE]["close"]
            - velas[i]["close"]
        )

        if variacao == 0:
            continue

        amostras.append({
            "x": x,
            "y": 1 if variacao > 0 else 0
        })

    return amostras


# ============================================================
# NORMALIZACAO
# ============================================================

def ajustar_normalizacao(amostras):

    medias = []
    desvios = []

    for j in range(len(NOMES_VARIAVEIS)):

        valores = [
            item["x"][j]
            for item in amostras
        ]

        media = sum(valores) / len(valores)

        variancia = sum(
            (v - media) ** 2
            for v in valores
        ) / len(valores)

        desvio = math.sqrt(variancia)

        medias.append(media)
        desvios.append(
            desvio if desvio > 0.000001 else 1.0
        )

    return medias, desvios


def normalizar(x, medias, desvios):

    return [
        (x[i] - medias[i]) / desvios[i]
        for i in range(len(x))
    ]


# ============================================================
# REGRESSAO LOGISTICA
# ============================================================

def treinar(amostras, medias, desvios):

    pesos = [0.0] * len(NOMES_VARIAVEIS)
    vies = 0.0

    quantidade = len(amostras)

    for _ in range(EPOCAS):

        gradientes = [0.0] * len(pesos)
        gradiente_vies = 0.0

        for item in amostras:

            x = normalizar(
                item["x"], medias, desvios
            )

            z = vies + sum(
                pesos[j] * x[j]
                for j in range(len(pesos))
            )

            erro = sigmoid(z) - item["y"]

            gradiente_vies += erro

            for j in range(len(pesos)):
                gradientes[j] += erro * x[j]

        vies -= (
            TAXA_APRENDIZADO
            * gradiente_vies / quantidade
        )

        for j in range(len(pesos)):

            gradiente = (
                gradientes[j] / quantidade
                + REGULARIZACAO * pesos[j]
            )

            pesos[j] -= (
                TAXA_APRENDIZADO * gradiente
            )

    return {
        "pesos": pesos,
        "vies": vies,
        "medias": medias,
        "desvios": desvios
    }


def prever_bruto(x, modelo):

    valores = normalizar(
        x,
        modelo["medias"],
        modelo["desvios"]
    )

    logit = (
        modelo["vies"]
        + sum(
            modelo["pesos"][j] * valores[j]
            for j in range(len(valores))
        )
    )

    return logit


# ============================================================
# CALIBRACAO PLATT
# ============================================================

def calibrar(amostras, modelo):

    dados = [
        (prever_bruto(item["x"], modelo), item["y"])
        for item in amostras
    ]

    a = 1.0
    b = 0.0

    for _ in range(500):

        ga = 0.0
        gb = 0.0

        for logit, y in dados:

            p = sigmoid(a * logit + b)
            erro = p - y

            ga += erro * logit
            gb += erro

        a -= 0.025 * ga / len(dados)
        b -= 0.025 * gb / len(dados)

    return {"a": a, "b": b}


def probabilidade(x, modelo, calibracao):

    logit = prever_bruto(x, modelo)

    return sigmoid(
        calibracao["a"] * logit
        + calibracao["b"]
    )


# ============================================================
# METRICAS DO TESTE
# ============================================================

def avaliar(teste, modelo, calibracao):

    brier = 0.0
    acertos = 0
    logloss = 0.0

    for item in teste:

        p = probabilidade(
            item["x"],
            modelo,
            calibracao
        )

        y = item["y"]

        brier += (p - y) ** 2

        if (p >= 0.5 and y == 1) or (
            p < 0.5 and y == 0
        ):
            acertos += 1

        p = max(0.000001, min(0.999999, p))

        logloss -= (
            y * math.log(p)
            + (1 - y) * math.log(1 - p)
        )

    n = len(teste)

    return {
        "amostras": n,
        "acuracia_percentual": round(
            acertos / n * 100, 2
        ),
        "brier_score": round(
            brier / n, 5
        ),
        "log_loss": round(
            logloss / n, 5
        )
    }


# ============================================================
# SAIDA NEUTRA
# ============================================================

def salvar_neutro(motivo, amostras=0):

    resultado = {
        "ativo": "XAUUSD",
        "atualizado_utc": agora(),
        "status_modelo": "AMOSTRA_INSUFICIENTE",
        "horizonte_velas_m5": HORIZONTE,
        "horizonte_minutos": HORIZONTE * 5,
        "probabilidade_subir": 50.0,
        "probabilidade_cair": 50.0,
        "amostras_historicas": amostras,
        "motivo": motivo,
        "execucao_ordens": False
    }

    DATA.mkdir(parents=True, exist_ok=True)

    SAIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("Probabilidade neutra: 50% / 50%")
    print("Motivo:", motivo)


# ============================================================
# EXECUCAO PRINCIPAL
# ============================================================

def main():

    print("======================================")
    print("ANALISADOR DE SETUP XAUUSD")
    print("======================================")

    tabuleiro, estatisticas, _ = carregar_dados()

    velas = preparar_velas(tabuleiro)

    caracteristicas, atr = calcular_caracteristicas(
        velas
    )

    amostras = montar_amostras(
        velas,
        caracteristicas
    )

    total = len(amostras)

    print("Velas do Tabuleiro:", len(velas))
    print("Amostras direcionais:", total)

    print(
        "Registros estatisticos:",
        {
            chave: len(valor)
            for chave, valor in estatisticas.items()
        }
    )

    if total < MINIMO_AMOSTRAS:

        salvar_neutro(
            "Historico insuficiente para treinamento, "
            "calibracao e teste.",
            total
        )
        return

    limite_treino = int(total * 0.70)
    limite_calibracao = int(total * 0.85)

    treino = amostras[:limite_treino]

    calibracao_amostras = amostras[
        limite_treino + HORIZONTE:
        limite_calibracao
    ]

    teste = amostras[
        limite_calibracao + HORIZONTE:
    ]

    if (
        len(treino) < MINIMO_TREINO
        or len(calibracao_amostras) < MINIMO_CALIBRACAO
        or len(teste) < MINIMO_TESTE
    ):

        salvar_neutro(
            "Divisao temporal sem amostras suficientes.",
            total
        )
        return

    if (
        len({item["y"] for item in treino}) < 2
        or len({item["y"] for item in calibracao_amostras}) < 2
    ):

        salvar_neutro(
            "Treinamento ou calibracao sem exemplos de alta e baixa.",
            total
        )
        return

    medias, desvios = ajustar_normalizacao(treino)

    modelo = treinar(
        treino,
        medias,
        desvios
    )

    parametros_calibracao = calibrar(
        calibracao_amostras,
        modelo
    )

    metricas = avaliar(
        teste,
        modelo,
        parametros_calibracao
    )

    atual = caracteristicas[-1]

    if atual is None:
        salvar_neutro(
            "Caracteristicas da ultima vela indisponiveis.",
            total
        )
        return

    p_alta = probabilidade(
        atual,
        modelo,
        parametros_calibracao
    )

    p_alta = max(0.0, min(1.0, p_alta))
    p_baixa = 1.0 - p_alta

    resultado = {
        "ativo": "XAUUSD",
        "atualizado_utc": agora(),
        "candle_utc": velas[-1]["timestamp"],
        "preco_atual": velas[-1]["close"],

        "status_modelo": "TREINADO_CALIBRADO",

        "horizonte_velas_m5": HORIZONTE,
        "horizonte_minutos": HORIZONTE * 5,

        "probabilidade_subir": round(
            p_alta * 100, 2
        ),

        "probabilidade_cair": round(
            p_baixa * 100, 2
        ),

        "direcao_predominante": (
            "ALTA" if p_alta > 0.5
            else "BAIXA" if p_alta < 0.5
            else "NEUTRA"
        ),

        "amostras_historicas": total,

        "divisao_temporal": {
            "treinamento": len(treino),
            "calibracao": len(calibracao_amostras),
            "teste": len(teste)
        },

        "metricas_teste": metricas,

        "calibracao": {
            "metodo": "PLATT",
            "coeficiente": round(
                parametros_calibracao["a"], 8
            ),
            "intercepto": round(
                parametros_calibracao["b"], 8
            )
        },

        "fontes": {
            "graficos": "graficos.db ou graficos.db.gz",
            "estatisticas": "estatisticas_m5.db ou .db.gz",
            "estatisticas_lidas": {
                chave: len(valor)
                for chave, valor in estatisticas.items()
            }
        },

        "variaveis": NOMES_VARIAVEIS,

        "tabuleiro": {
            "eixo_x": "TEMPO_M5",
            "eixo_y": "REGIOES_DE_PRECO",
            "largura_regiao_usd": LARGURA_REGIAO,
            "atr_altera_largura": False
        },

        "metodo": (
            "REGRESSAO_LOGISTICA_COM_CALIBRACAO_PLATT "
            "E VALIDACAO_TEMPORAL"
        ),

        "observacao": (
            "Estimativa direcional baseada no historico. "
            "Metricas de teste nao garantem desempenho futuro."
        ),

        "execucao_ordens": False,
        "gravacao_bancos_oficiais": False
    }

    DATA.mkdir(parents=True, exist_ok=True)

    SAIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("--------------------------------------")
    print(
        "Probabilidade de subir:",
        resultado["probabilidade_subir"],
        "%"
    )
    print(
        "Probabilidade de cair:",
        resultado["probabilidade_cair"],
        "%"
    )
    print(
        "Acuracia no teste:",
        metricas["acuracia_percentual"],
        "%"
    )
    print(
        "Brier Score:",
        metricas["brier_score"]
    )
    print("Saida:", SAIDA)
    print("--------------------------------------")
    print("ANALISE CONCLUIDA")


if __name__ == "__main__":
    main()
