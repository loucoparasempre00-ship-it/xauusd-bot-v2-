
from math import isfinite


def analisar_macro(dados):
    """
    Avalia informações macroeconômicas fornecidas ao módulo.
    Não busca dados externos, não envia sinais e não executa ordens.
    """

    if not isinstance(dados, dict):
        raise ValueError("Os dados macro devem ser um dicionário.")

    fontes = dados.get("fontes", [])
    if not isinstance(fontes, list):
        fontes = []

    fatores = dados.get("fatores", [])
    if not isinstance(fatores, list):
        fatores = []

    avaliados = []
    pontuacao = 0.0

    for fator in fatores:
        if not isinstance(fator, dict):
            continue

        nome = fator.get("nome", "Fator sem nome")
        impacto = fator.get("impacto_ouro")

        try:
            impacto = float(impacto)
        except (TypeError, ValueError):
            continue

        if not isfinite(impacto) or not -1 <= impacto <= 1:
            continue

        confianca = fator.get("confianca", 0)

        try:
            confianca = float(confianca)
        except (TypeError, ValueError):
            continue

        if not isfinite(confianca) or not 0 <= confianca <= 1:
            continue

        contribuicao = impacto * confianca
        pontuacao += contribuicao

        avaliados.append({
            "nome": str(nome),
            "impacto_ouro": impacto,
            "confianca": confianca,
            "contribuicao": round(contribuicao, 4),
        })

    if not avaliados:
        direcao = "INDEFINIDA"
        status = "SEM_DADOS_MACRO_VALIDOS"
    elif pontuacao > 0.15:
        direcao = "FAVORAVEL_AO_OURO"
        status = "AVALIACAO_PRELIMINAR"
    elif pontuacao < -0.15:
        direcao = "DESFAVORAVEL_AO_OURO"
        status = "AVALIACAO_PRELIMINAR"
    else:
        direcao = "NEUTRA"
        status = "AVALIACAO_PRELIMINAR"

    return {
        "direcao_macro": direcao,
        "pontuacao": round(pontuacao, 4),
        "fatores_avaliados": avaliados,
        "fontes_informadas": fontes,
        "status": status,
        "dados_externos_consultados": False,
        "pode_decidir_sinal_sozinho": False,
        "envio_telegram": False,
        "execucao_ordens": False,
    }


if __name__ == "__main__":
    print("Modulo de analise macro carregado.")
    print("Aguardando dados macroeconomicos validos.")
    print("Sem consulta externa, Telegram ou execucao de ordens.")
