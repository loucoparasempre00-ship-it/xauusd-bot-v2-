
import csv
import json
import os
from datetime import datetime, timezone


ARQUIVOS = {
    "regioes": "data/estatisticas_regioes_m5.csv",
    "transicoes": "data/transicoes_probabilidade_m5.csv",
    "sequencias": "data/sequencias_provaveis_m5.csv",
    "resultados": "data/resultados_eventos_m5.csv",
    "resumo": "data/resumo_resultados_m5.csv",
    "eventos": "data/eventos_bordas_m5.csv",
    "tabuleiro": "data/tabuleiro_m5.csv"
}

SAIDA_JSON = "data/relatorio_tabuleiro_m5.json"
SAIDA_CSV = "data/relatorio_geral_tabuleiro_m5.csv"


def ler_csv(caminho):
    if not os.path.exists(caminho):
        print(f"AVISO: arquivo ainda não disponível: {caminho}")
        return []

    try:
        with open(
            caminho,
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as arquivo:
            return list(csv.DictReader(arquivo))

    except (OSError, csv.Error) as erro:
        print(f"ERRO ao ler {caminho}: {erro}")
        return []


def numero(valor, padrao=0.0):
    try:
        if valor is None or str(valor).strip() == "":
            return padrao

        texto = str(valor).strip()
        texto = texto.replace("%", "")
        texto = texto.replace(",", ".")

        return float(texto)

    except (ValueError, TypeError):
        return padrao


def inteiro(valor, padrao=0):
    try:
        return int(numero(valor, padrao))
    except (ValueError, TypeError):
        return padrao


def carregar_dados():
    return {
        nome: ler_csv(caminho)
        for nome, caminho in ARQUIVOS.items()
    }


def contar_eventos(eventos):
    contagem = {}

    for evento in eventos:
        tipo = (
            evento.get("event")
            or evento.get("tipo_evento")
            or evento.get("evento")
            or "DESCONHECIDO"
        )

        tipo = str(tipo).strip().upper()

        contagem[tipo] = contagem.get(tipo, 0) + 1

    return contagem


def melhores_regioes(regioes):
    def pontuacao(item):
        return numero(
            item.get("probabilidade_rejeicao"),
            numero(item.get("taxa_rejeicao"))
        )

    ordenadas = sorted(
        regioes,
        key=pontuacao,
        reverse=True
    )

    return ordenadas[:10]


def melhores_transicoes(transicoes):
    def pontuacao(item):
        return numero(
            item.get("probabilidade"),
            numero(item.get("probabilidade_percentual"))
        )

    ordenadas = sorted(
        transicoes,
        key=pontuacao,
        reverse=True
    )

    return ordenadas[:15]


def melhores_sequencias(sequencias):
    def pontuacao(item):
        return numero(
            item.get("probabilidade"),
            numero(item.get("taxa_acerto"))
        )

    ordenadas = sorted(
        sequencias,
        key=pontuacao,
        reverse=True
    )

    return ordenadas[:15]


def consolidar_resultados(resumos):
    resultado = []

    for item in resumos:
        total = inteiro(
            item.get("eventos_avaliados"),
            inteiro(item.get("total_eventos"))
        )

        resultado.append({
            "evento": (
                item.get("tipo_evento")
                or item.get("evento")
                or "DESCONHECIDO"
            ),
            "amostras": total,
            "taxa_favoravel_05": numero(
                item.get("taxa_favoravel_05")
            ),
            "taxa_favoravel_10": numero(
                item.get("taxa_favoravel_10")
            ),
            "favoraveis_05": inteiro(
                item.get("favoravel_05")
            ),
            "desfavoraveis_05": inteiro(
                item.get("desfavoravel_05")
            ),
            "ambiguos_05": inteiro(
                item.get("ambiguo_05")
            )
        })

    return resultado


def gerar_diagnostico(dados):
    quantidade_velas = len(dados["tabuleiro"])
    quantidade_eventos = len(dados["eventos"])
    quantidade_resultados = len(dados["resultados"])

    if quantidade_velas == 0:
        estado = "SEM_DADOS"

    elif quantidade_eventos == 0:
        estado = "AGUARDANDO_EVENTOS"

    elif quantidade_resultados == 0:
        estado = "AGUARDANDO_HORIZONTE"

    else:
        estado = "DADOS_DISPONIVEIS"

    return {
        "estado": estado,
        "velas": quantidade_velas,
        "eventos": quantidade_eventos,
        "eventos_avaliados": quantidade_resultados,
        "regioes_analisadas": len(dados["regioes"]),
        "transicoes_analisadas": len(dados["transicoes"]),
        "sequencias_calculadas": len(dados["sequencias"]),
        "resumos_de_resultados": len(dados["resumo"])
    }


def gerar_relatorio(dados):
    diagnostico = gerar_diagnostico(dados)

    return {
        "atualizado_em": datetime.now(
            timezone.utc
        ).isoformat(),

        "diagnostico": diagnostico,

        "eventos": contar_eventos(
            dados["eventos"]
        ),

        "melhores_regioes": melhores_regioes(
            dados["regioes"]
        ),

        "melhores_transicoes": melhores_transicoes(
            dados["transicoes"]
        ),

        "melhores_sequencias": melhores_sequencias(
            dados["sequencias"]
        ),

        "desempenho_eventos": consolidar_resultados(
            dados["resumo"]
        ),

        "limites": [
            "Probabilidades representam frequências históricas.",
            "Sequências não garantem movimentos futuros.",
            "Resultados recentes podem estar incompletos.",
            "Regiões precisam manter identificação estável.",
            "Amostras pequenas não devem ser tratadas como evidência forte.",
            "Toques e hipóteses não representam confirmação de resultado."
        ]
    }


def salvar_json(relatorio):
    os.makedirs("data", exist_ok=True)

    with open(
        SAIDA_JSON,
        "w",
        encoding="utf-8"
    ) as arquivo:
        json.dump(
            relatorio,
            arquivo,
            indent=4,
            ensure_ascii=False
        )


def salvar_csv(relatorio):
    os.makedirs("data", exist_ok=True)

    linhas = []

    diagnostico = relatorio["diagnostico"]

    for chave, valor in diagnostico.items():
        linhas.append({
            "categoria": "DIAGNOSTICO",
            "indicador": chave,
            "valor": valor
        })

    for tipo, quantidade in relatorio["eventos"].items():
        linhas.append({
            "categoria": "EVENTO",
            "indicador": tipo,
            "valor": quantidade
        })

    for item in relatorio["melhores_regioes"]:
        identificacao = (
            item.get("regiao")
            or item.get("id_regiao")
            or item.get("codigo_regiao")
            or "REGIAO"
        )

        linhas.append({
            "categoria": "MELHOR_REGIAO",
            "indicador": identificacao,
            "valor": json.dumps(
                item,
                ensure_ascii=False
            )
        })

    for item in relatorio["melhores_transicoes"]:
        origem = (
            item.get("regiao_origem")
            or item.get("origem")
            or "?"
        )

        destino = (
            item.get("regiao_destino")
            or item.get("destino")
            or "?"
        )

        linhas.append({
            "categoria": "TRANSICAO",
            "indicador": f"{origem} -> {destino}",
            "valor": json.dumps(
                item,
                ensure_ascii=False
            )
        })

    for item in relatorio["melhores_sequencias"]:
        identificador = (
            item.get("sequencia")
            or item.get("padrao")
            or item.get("bloco")
            or "SEQUENCIA"
        )

        linhas.append({
            "categoria": "SEQUENCIA",
            "indicador": identificador,
            "valor": json.dumps(
                item,
                ensure_ascii=False
            )
        })

    for item in relatorio["desempenho_eventos"]:
        linhas.append({
            "categoria": "DESEMPENHO",
            "indicador": item["evento"],
            "valor": json.dumps(
                item,
                ensure_ascii=False
            )
        })

    with open(
        SAIDA_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=[
                "categoria",
                "indicador",
                "valor"
            ]
        )

        escritor.writeheader()
        escritor.writerows(linhas)


def main():
    print("1/3 - Carregando dados do Tabuleiro...")

    dados = carregar_dados()

    print("2/3 - Consolidando estatísticas e resultados...")

    relatorio = gerar_relatorio(dados)

    print("3/3 - Salvando relatório geral...")

    salvar_json(relatorio)
    salvar_csv(relatorio)

    print("\nDIAGNÓSTICO DO TABULEIRO")

    for chave, valor in relatorio["diagnostico"].items():
        print(f"{chave}: {valor}")

    print("\nRelatórios gerados:")
    print(SAIDA_JSON)
    print(SAIDA_CSV)

    print("\nConsolidação concluída.")


if __name__ == "__main__":
    main()
