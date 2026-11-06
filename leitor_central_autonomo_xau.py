import json
import math
from pathlib import Path
from datetime import datetime, timezone


ARQUIVO_CENTRAL = Path(
    "data/contexto_tabuleiro_m5.json"
)

LARGURA_REGIAO = 1.50
ANCORAGEM = 0.0


def agora_utc():
    return datetime.now(timezone.utc)


def converter_timestamp(valor):
    if not valor:
        return None

    try:
        texto = str(valor).replace("Z", "+00:00")
        data = datetime.fromisoformat(texto)

        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)

        return data.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def calcular_regiao(preco):
    """
    Mesma fórmula de tabuleiro_estavel_m5.py:
    floor(preço / 1.50), com ancoragem zero.
    """

    preco = float(preco)

    regiao = math.floor(
        (preco - ANCORAGEM) / LARGURA_REGIAO
    )

    limite_inferior = (
        ANCORAGEM + regiao * LARGURA_REGIAO
    )

    limite_superior = (
        limite_inferior + LARGURA_REGIAO
    )

    return {
        "regiao": int(regiao),
        "limite_inferior": round(
            limite_inferior, 5
        ),
        "limite_superior": round(
            limite_superior, 5
        ),
        "largura": LARGURA_REGIAO
    }


def carregar_contexto(caminho=ARQUIVO_CENTRAL):
    caminho = Path(caminho)

    if not caminho.exists():
        return {
            "disponivel": False,
            "motivo": "Arquivo da Central não encontrado."
        }

    try:
        with caminho.open(
            "r",
            encoding="utf-8"
        ) as arquivo:
            dados = json.load(arquivo)

    except Exception as erro:
        return {
            "disponivel": False,
            "motivo": f"Falha ao ler a Central: {erro}"
        }

    mercado = dados.get("mercado_atual") or {}

    if not mercado:
        return {
            "disponivel": False,
            "motivo": "Central sem mercado_atual."
        }

    atualizado = converter_timestamp(
        dados.get("atualizado_em")
    )

    idade_minutos = None

    if atualizado:
        idade_minutos = max(
            0.0,
            (
                agora_utc() - atualizado
            ).total_seconds() / 60
        )

    return {
        "disponivel": True,
        "dados": dados,
        "mercado_atual": mercado,
        "atualizado_em": dados.get("atualizado_em"),
        "idade_minutos": (
            round(idade_minutos, 1)
            if idade_minutos is not None
            else None
        )
    }


def analisar_preco(preco, contexto):
    regiao_preco = calcular_regiao(preco)

    if not contexto or not contexto.get("disponivel"):
        return {
            "disponivel": False,
            "preco": round(float(preco), 5),
            "regiao_preco": regiao_preco,
            "motivo": (
                contexto.get("motivo")
                if contexto
                else "Contexto indisponível."
            )
        }

    dados = contexto["dados"]
    mercado = contexto["mercado_atual"]

    regiao_central = mercado.get("regiao")

    transicoes = dados.get(
        "transicoes_historicas_da_regiao",
        []
    ) or []

    transicoes_regiao = [
        item for item in transicoes
        if str(item.get("origem")) == str(
            regiao_preco["regiao"]
        )
    ]

    transicoes_regiao.sort(
        key=lambda item: int(
            item.get("transicoes", 0)
        ),
        reverse=True
    )

    confiaveis = [
        item for item in transicoes_regiao
        if item.get("amostra_suficiente") is True
    ]

    visitas = None

    if (
        regiao_central is not None
        and int(regiao_central) == regiao_preco["regiao"]
    ):
        visitas = mercado.get("visitas_regiao")

    return {
        "disponivel": True,
        "preco": round(float(preco), 5),
        "timestamp_central": mercado.get("timestamp"),
        "atualizado_em": contexto.get("atualizado_em"),
        "idade_minutos": contexto.get("idade_minutos"),
        "central_desatualizada": (
            contexto.get("idade_minutos") is not None
            and contexto["idade_minutos"] > 30
        ),
        "regiao": regiao_preco["regiao"],
        "limite_inferior": regiao_preco["limite_inferior"],
        "limite_superior": regiao_preco["limite_superior"],
        "regiao_registrada_na_central": regiao_central,
        "visitas_regiao": visitas,
        "transicoes_historicas": transicoes_regiao,
        "transicoes_confiaveis": confiaveis
    }


def identificar_pressao_contraria(
    leitura,
    direcao
):
    """
    Identifica se as transições históricas confiáveis
    apontam principalmente para a direção contrária.

    Não transforma frequência histórica em certeza.
    """

    if not leitura or not leitura.get("disponivel"):
        return {
            "disponivel": False,
            "pressao_contraria": False
        }

    if leitura.get("central_desatualizada"):
        return {
            "disponivel": False,
            "pressao_contraria": False,
            "motivo": "Dados da Central com mais de 30 minutos."
        }

    direcao = str(direcao).upper()

    regiao_atual = int(leitura["regiao"])

    transicoes = leitura.get(
        "transicoes_confiaveis", []
    )

    total = sum(
        int(item.get("transicoes", 0))
        for item in transicoes
    )

    if total <= 0:
        return {
            "disponivel": False,
            "pressao_contraria": False
        }

    contrarias = []

    for item in transicoes:
        destino = int(item.get("destino", regiao_atual))

        if direcao == "COMPRA" and destino < regiao_atual:
            contrarias.append(item)

        elif direcao == "VENDA" and destino > regiao_atual:
            contrarias.append(item)

    quantidade_contraria = sum(
        int(item.get("transicoes", 0))
        for item in contrarias
    )

    proporcao = quantidade_contraria / total

    return {
        "disponivel": True,
        "pressao_contraria": proporcao >= 0.60,
        "proporcao_contraria": round(proporcao, 4),
        "amostra_total": total,
        "amostra_contraria": quantidade_contraria
    }
