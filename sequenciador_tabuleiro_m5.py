
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SEQUENCIADOR DO TABULEIRO M5

Lê os bancos oficiais:
- data/graficos.db
- data/estatisticas_m5.db

Identifica blocos de regiões que se repetem no histórico.
Calcula o que aconteceu depois de cada repetição.
Gera arquivos para entrega ao Coletor.

IMPORTANTE:
Este módulo NÃO grava em nenhum banco de dados.
"""

import csv
import gzip
import json
import os
import sqlite3
import tempfile

from collections import defaultdict, Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

BANCO_GRAFICOS = DATA / "graficos.db"
BANCO_GRAFICOS_GZ = DATA / "graficos.db.gz"

BANCO_ESTATISTICAS = DATA / "estatisticas_m5.db"
BANCO_ESTATISTICAS_GZ = DATA / "estatisticas_m5.db.gz"

SAIDA_SEQUENCIAS = DATA / "sequencias_provaveis_m5.csv"
SAIDA_JSON = DATA / "proximas_regioes_m5.json"

MIN_AMOSTRAS = 3
MIN_TAMANHO_PADRAO = 2
MAX_TAMANHO_PADRAO = 8
LIMITE_PADROES = 500
MIN_AMOSTRAS_TRANSICAO = 5


def agora():
    return datetime.now(timezone.utc).isoformat()


def inteiro(valor, padrao=None):
    try:
        return int(float(str(valor).replace(",", ".")))
    except (ValueError, TypeError):
        return padrao


def numero(valor, padrao=0.0):
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return padrao


def obter_campo(registro, nomes, padrao=None):
    campos = {
        str(chave).lower(): valor
        for chave, valor in registro.items()
    }

    for nome in nomes:
        valor = campos.get(nome.lower())

        if valor not in (None, ""):
            return valor

    return padrao


@contextmanager
def conectar_banco(nome):
    """
    Abre o banco oficial em modo somente leitura.

    Se existir apenas o arquivo .db.gz, cria uma cópia
    temporária para consulta e apaga ao terminar.
    """

    banco = DATA / f"{nome}.db"
    compactado = DATA / f"{nome}.db.gz"

    temporario = None
    caminho = None

    if banco.is_file():
        caminho = banco

    elif compactado.is_file():
        temporario = tempfile.TemporaryDirectory(
            prefix=f"consulta_{nome}_"
        )

        caminho = Path(temporario.name) / f"{nome}.db"

        with gzip.open(compactado, "rb") as origem:
            with open(caminho, "wb") as destino:
                destino.write(origem.read())

    else:
        raise FileNotFoundError(
            f"Banco oficial não encontrado: {nome}.db ou {nome}.db.gz"
        )

    conexao = None

    try:
        conexao = sqlite3.connect(
            caminho.resolve().as_uri() + "?mode=ro",
            uri=True,
        )

        conexao.row_factory = sqlite3.Row

        yield conexao

    finally:
        if conexao is not None:
            conexao.close()

        if temporario is not None:
            temporario.cleanup()


def carregar_arquivo_do_banco(
    conexao,
    tabela,
    nomes_arquivos,
):
    """
    Recupera as linhas originais de um CSV armazenado
    pelo Organizador dentro do banco oficial.
    """

    tabelas = conexao.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
    """).fetchall()

    nomes_tabelas = {
        linha["name"]
        for linha in tabelas
    }

    if tabela not in nomes_tabelas:
        return []

    resultados = []

    for nome_arquivo in nomes_arquivos:
        linhas = conexao.execute(
            f"""
            SELECT payload_json
            FROM "{tabela}"
            WHERE tipo_registro = 'linha_csv'
              AND lower(arquivo_origem) LIKE ?
            ORDER BY numero_registro
            """,
            (f"%{nome_arquivo.lower()}",),
        ).fetchall()

        registros = []

        for linha in linhas:
            try:
                objeto = json.loads(linha["payload_json"])

                if isinstance(objeto, dict):
                    registros.append(objeto)

            except (json.JSONDecodeError, TypeError):
                continue

        if registros:
            return registros

    return resultados


def carregar_tabuleiro():
    """
    A sequência histórica vem do banco de gráficos,
    onde o Coletor recebeu o tabuleiro M5.
    """

    print("Consultando banco oficial de gráficos...")

    with conectar_banco("graficos") as conexao:
        registros = carregar_arquivo_do_banco(
            conexao,
            "graficos",
            [
                "tabuleiro_m5.csv",
                "tabuleiro_estavel_m5.csv",
            ],
        )

    if not registros:
        raise RuntimeError(
            "O banco graficos.db não contém "
            "o histórico do tabuleiro M5."
        )

    velas = []

    for registro in registros:
        regiao = inteiro(
            obter_campo(
                registro,
                ["eixo_y", "regiao", "nivel"],
            )
        )

        timestamp = obter_campo(
            registro,
            ["timestamp", "timestamp_utc"],
            "",
        )

        eixo_x = inteiro(
            obter_campo(registro, ["eixo_x"]),
            0,
        )

        if regiao is None:
            continue

        velas.append({
            "regiao": regiao,
            "timestamp": str(timestamp),
            "eixo_x": eixo_x,
        })

    if not velas:
        raise RuntimeError(
            "Não foi possível reconstruir as regiões do tabuleiro."
        )

    velas.sort(
        key=lambda item: (
            item["timestamp"],
            item["eixo_x"],
        )
    )

    return velas


def construir_sequencia(velas):
    """
    Transforma as velas em uma sequência de regiões.

    Repetições consecutivas da mesma região são agrupadas.
    Exemplo:
    6, 6, 6, 4, 8, 8, 9, 0
    torna-se:
    6, 4, 8, 9, 0
    """

    sequencia = []

    for vela in velas:
        regiao = vela["regiao"]

        if not sequencia or sequencia[-1] != regiao:
            sequencia.append(regiao)

    return sequencia


def identificar_padroes(sequencia):
    """
    Procura blocos históricos de 2 a 8 regiões.

    Para cada bloco, registra a região que apareceu
    imediatamente depois dele.
    """

    ocorrencias = defaultdict(Counter)

    tamanho_maximo = min(
        MAX_TAMANHO_PADRAO,
        len(sequencia) - 1,
    )

    for tamanho in range(
        MIN_TAMANHO_PADRAO,
        tamanho_maximo + 1,
    ):

        for inicio in range(
            0,
            len(sequencia) - tamanho,
        ):

            padrao = tuple(
                sequencia[inicio:inicio + tamanho]
            )

            proxima = sequencia[inicio + tamanho]

            ocorrencias[padrao][proxima] += 1

    resultados = []

    for padrao, destinos in ocorrencias.items():
        total = sum(destinos.values())

        if total < MIN_AMOSTRAS:
            continue

        proxima_regiao, acertos = destinos.most_common(1)[0]

        erros = total - acertos

        resultados.append({
            "padrao": list(padrao),
            "tamanho": len(padrao),
            "amostras": total,
            "proxima_regiao": proxima_regiao,
            "acertos": acertos,
            "erros": erros,
            "taxa_acerto": round(
                acertos / total * 100,
                2,
            ),
            "taxa_erro": round(
                erros / total * 100,
                2,
            ),
            "distribuicao": dict(destinos),
        })

    resultados.sort(
        key=lambda item: (
            item["taxa_acerto"],
            item["amostras"],
            item["tamanho"],
        ),
        reverse=True,
    )

    return resultados[:LIMITE_PADROES]


def encontrar_padroes_atuais(sequencia, padroes):
    """
    Verifica quais blocos históricos aparecem
    no final da sequência atual.
    """

    encontrados = []

    indice = {
        tuple(item["padrao"]): item
        for item in padroes
    }

    tamanho_maximo = min(
        MAX_TAMANHO_PADRAO,
        len(sequencia),
    )

    for tamanho in range(
        MIN_TAMANHO_PADRAO,
        tamanho_maximo + 1,
    ):

        padrao_atual = tuple(
            sequencia[-tamanho:]
        )

        resultado = indice.get(padrao_atual)

        if resultado:
            encontrados.append(resultado)

    encontrados.sort(
        key=lambda item: (
            item["taxa_acerto"],
            item["amostras"],
            item["tamanho"],
        ),
        reverse=True,
    )

    return encontrados


def carregar_transicoes():
    """
    Consulta as transições já armazenadas no banco
    oficial de estatísticas.

    Mantém compatibilidade com os caminhos antigos.
    """

    print("Consultando banco oficial de estatísticas...")

    with conectar_banco("estatisticas_m5") as conexao:
        registros = carregar_arquivo_do_banco(
            conexao,
            "estatisticas_m5",
            ["transicoes_probabilidade_m5.csv"],
        )

    transicoes = defaultdict(list)

    for linha in registros:
        origem = inteiro(
            obter_campo(
                linha,
                ["origem", "regiao_origem", "de", "eixo_y_origem"],
            )
        )

        destino = inteiro(
            obter_campo(
                linha,
                ["destino", "regiao_destino", "para", "eixo_y_destino"],
            )
        )

        probabilidade = numero(
            obter_campo(
                linha,
                [
                    "probabilidade",
                    "probabilidade_transicao",
                    "taxa",
                ],
            )
        )

        quantidade = inteiro(
            obter_campo(
                linha,
                [
                    "quantidade",
                    "contagem",
                    "ocorrencias",
                    "total",
                ],
            ),
            0,
        )

        if origem is None or destino is None:
            continue

        if probabilidade > 1:
            probabilidade /= 100

        transicoes[origem].append({
            "destino": destino,
            "probabilidade": probabilidade,
            "quantidade": quantidade,
        })

    return transicoes


def normalizar_transicoes(transicoes):
    resultado = {}

    for origem, destinos in transicoes.items():
        total = sum(
            item["quantidade"]
            for item in destinos
        )

        resultado[origem] = []

        for item in destinos:
            if total > 0:
                probabilidade = (
                    item["quantidade"] / total
                )
            else:
                probabilidade = item["probabilidade"]

            resultado[origem].append({
                "destino": item["destino"],
                "probabilidade": probabilidade,
                "quantidade": item["quantidade"],
            })

        resultado[origem].sort(
            key=lambda item: item["probabilidade"],
            reverse=True,
        )

    return resultado


def gerar_caminhos(regiao_atual, transicoes):
    caminhos = []

    for primeiro in transicoes.get(regiao_atual, []):
        if primeiro["quantidade"] < MIN_AMOSTRAS_TRANSICAO:
            continue

        regiao_2 = primeiro["destino"]
        prob_1 = primeiro["probabilidade"]

        caminhos.append({
            "origem": regiao_atual,
            "regiao_2": regiao_2,
            "regiao_3": "",
            "probabilidade": prob_1,
            "amostras": primeiro["quantidade"],
            "etapas": 1,
        })

        for segundo in transicoes.get(regiao_2, []):
            if segundo["quantidade"] < MIN_AMOSTRAS_TRANSICAO:
                continue

            caminhos.append({
                "origem": regiao_atual,
                "regiao_2": regiao_2,
                "regiao_3": segundo["destino"],
                "probabilidade": (
                    prob_1 * segundo["probabilidade"]
                ),
                "amostras": segundo["quantidade"],
                "etapas": 2,
            })

    caminhos.sort(
        key=lambda item: item["probabilidade"],
        reverse=True,
    )

    return caminhos[:100]


def salvar_csv(padroes_atuais):
    DATA.mkdir(parents=True, exist_ok=True)

    campos = [
        "padrao",
        "tamanho",
        "amostras",
        "proxima_regiao",
        "acertos",
        "erros",
        "taxa_acerto",
        "taxa_erro",
        "distribuicao",
    ]

    with open(
        SAIDA_SEQUENCIAS,
        "w",
        newline="",
        encoding="utf-8",
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
        )

        escritor.writeheader()

        for item in padroes_atuais:
            escritor.writerow({
                "padrao": " ".join(
                    map(str, item["padrao"])
                ),
                "tamanho": item["tamanho"],
                "amostras": item["amostras"],
                "proxima_regiao": item["proxima_regiao"],
                "acertos": item["acertos"],
                "erros": item["erros"],
                "taxa_acerto": item["taxa_acerto"],
                "taxa_erro": item["taxa_erro"],
                "distribuicao": json.dumps(
                    item["distribuicao"],
                    ensure_ascii=False,
                ),
            })


def salvar_json(
    velas,
    sequencia,
    padroes_historicos,
    padroes_atuais,
    caminhos,
):
    ultima = velas[-1]

    diretos = [
        item
        for item in caminhos
        if item["etapas"] == 1
    ]

    resultado = {
        "atualizado_em": agora(),
        "origem_dados": [
            "graficos.db",
            "estatisticas_m5.db",
        ],
        "ultima_vela": ultima["timestamp"],
        "regiao_atual": ultima["regiao"],
        "sequencia_atual": sequencia[-MAX_TAMANHO_PADRAO:],
        "quantidade_regioes_historicas": len(sequencia),
        "quantidade_padroes_historicos": len(
            padroes_historicos
        ),
        "quantidade_padroes_atuais": len(
            padroes_atuais
        ),
        "padroes_atuais": padroes_atuais,
        "melhores_padroes_historicos": padroes_historicos,
        "proximas_regioes": [
            {
                "regiao": item["regiao_2"],
                "probabilidade": round(
                    item["probabilidade"],
                    6,
                ),
                "percentual": round(
                    item["probabilidade"] * 100,
                    2,
                ),
                "amostras": item["amostras"],
            }
            for item in diretos
        ],
        "melhores_caminhos": caminhos[:10],
        "observacao": (
            "Estatísticas históricas de blocos repetidos. "
            "Acertos representam ocorrências em que a região "
            "seguinte coincidiu com a região mais frequente. "
            "Não representam garantia de movimento futuro."
        ),
    }

    with open(
        SAIDA_JSON,
        "w",
        encoding="utf-8",
    ) as arquivo:
        json.dump(
            resultado,
            arquivo,
            indent=4,
            ensure_ascii=False,
        )


def main():
    print("=" * 55)
    print("SEQUENCIADOR DO TABULEIRO M5")
    print("=" * 55)

    print("\n1/6 - Lendo histórico do banco de gráficos...")

    velas = carregar_tabuleiro()

    print("Registros recuperados:", len(velas))

    print("\n2/6 - Construindo sequência de regiões...")

    sequencia = construir_sequencia(velas)

    if len(sequencia) < MIN_TAMANHO_PADRAO + 1:
        raise RuntimeError(
            "Histórico insuficiente para identificar blocos."
        )

    print("Regiões na sequência:", len(sequencia))
    print("Região atual:", sequencia[-1])

    print("\n3/6 - Identificando blocos repetidos...")

    padroes_historicos = identificar_padroes(
        sequencia
    )

    print(
        "Padrões históricos identificados:",
        len(padroes_historicos),
    )

    print("\n4/6 - Conferindo padrões na posição atual...")

    padroes_atuais = encontrar_padroes_atuais(
        sequencia,
        padroes_historicos,
    )

    print(
        "Padrões encontrados na sequência atual:",
        len(padroes_atuais),
    )

    print("\n5/6 - Consultando transições históricas...")

    transicoes = carregar_transicoes()
    transicoes = normalizar_transicoes(transicoes)

    caminhos = gerar_caminhos(
        sequencia[-1],
        transicoes,
    )

    print("Caminhos antigos recuperados:", len(caminhos))

    print("\n6/6 - Gerando arquivos para o Coletor...")

    salvar_csv(padroes_atuais)

    salvar_json(
        velas,
        sequencia,
        padroes_historicos,
        padroes_atuais,
        caminhos,
    )

    print("\n========== RESULTADO ==========")

    for item in padroes_atuais[:10]:
        bloco = " ".join(
            map(str, item["padrao"])
        )

        print(
            f"Bloco: {bloco} | "
            f"Próxima região: {item['proxima_regiao']} | "
            f"Amostras: {item['amostras']} | "
            f"Acertos: {item['taxa_acerto']}% | "
            f"Erros: {item['taxa_erro']}%"
        )

    print("\nArquivos produzidos:")
    print(SAIDA_SEQUENCIAS)
    print(SAIDA_JSON)

    print("\nNenhum banco foi alterado.")
    print("Entrega dos arquivos: Coletor XAUUSD.")
    print("\nSEQUENCIADOR CONCLUÍDO.")


if __name__ == "__main__":
    main()
