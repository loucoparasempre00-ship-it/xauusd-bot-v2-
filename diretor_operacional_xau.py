from leitor_central_autonomo_xau import (
    identificar_pressao_contraria
)


def numero(valor):
    try:
        if valor is None:
            return None

        return float(valor)

    except (ValueError, TypeError):
        return None


def distancia_favoravel(preco, entrada, direcao):
    if direcao == "COMPRA":
        return preco - entrada

    return entrada - preco


def stop_mais_protetor(novo, atual, direcao):
    if direcao == "COMPRA":
        return novo > atual

    return novo < atual


def calcular_decisao(
    operacao,
    vela,
    ema9,
    ema21,
    valor_rsi,
    atr,
    leitura_central=None
):
    """
    Diretor operacional inicial.

    Regras:
    - Nunca afrouxa o stop.
    - Ao atingir 1R, sugere stop no preço de entrada.
    - Acima de 1.25R, passa a acompanhar o preço por ATR.
    - Exige duas velas consecutivas de confirmação para
      recomendar encerramento por reversão.
    - A Central é contexto complementar, não um voto
      automático para fechar a operação.
    """

    direcao = str(
        operacao.get("direcao", "")
    ).upper()

    if direcao not in ("COMPRA", "VENDA"):
        raise ValueError("Direção inválida.")

    entrada = numero(
        operacao.get("entrada_real")
    )

    stop_atual = numero(
        operacao.get("stop")
    )

    alvo = numero(
        operacao.get("alvo")
    )

    preco = numero(
        vela.get("close")
    )

    atr = numero(atr)
    ema9 = numero(ema9)
    ema21 = numero(ema21)
    valor_rsi = numero(valor_rsi)

    if None in (
        entrada,
        stop_atual,
        alvo,
        preco,
        atr
    ):
        raise ValueError(
            "Dados insuficientes para decisão operacional."
        )

    risco_inicial = abs(
        entrada - numero(
            operacao.get("stop_original", stop_atual)
        )
    )

    if risco_inicial <= 0 or atr <= 0:
        raise ValueError(
            "Risco inicial ou ATR inválido."
        )

    movimento = distancia_favoravel(
        preco,
        entrada,
        direcao
    )

    resultado_r = movimento / risco_inicial

    novo_stop = stop_atual
    acao_stop = None
    motivo_stop = None

    # Primeiro nível: proteger o capital ao alcançar 1R.
    if resultado_r >= 1.0:

        candidato = entrada

        if stop_mais_protetor(
            candidato,
            novo_stop,
            direcao
        ):
            novo_stop = candidato
            acao_stop = "MUDA_STOP"
            motivo_stop = (
                "O preço alcançou 1R. "
                "Stop sugerido no preço de entrada."
            )

    # Segundo nível: trailing stop após avanço adicional.
    if resultado_r >= 1.25:

        distancia_trailing = max(
            atr * 0.80,
            0.10
        )

        if direcao == "COMPRA":
            candidato = preco - distancia_trailing
        else:
            candidato = preco + distancia_trailing

        if stop_mais_protetor(
            candidato,
            novo_stop,
            direcao
        ):
            novo_stop = candidato
            acao_stop = "MUDA_STOP"
            motivo_stop = (
                "O preço avançou mais de 1.25R. "
                "Stop móvel calculado por 0.80 ATR."
            )

    # Confirmação de reversão.
    reversao = False

    if (
        ema9 is not None
        and ema21 is not None
        and valor_rsi is not None
    ):

        if direcao == "COMPRA":
            reversao = (
                preco < ema21
                and ema9 < ema21
                and valor_rsi < 45
            )

        else:
            reversao = (
                preco > ema21
                and ema9 > ema21
                and valor_rsi > 55
            )

    if reversao:
        sequencia = int(
            operacao.get("sequencia_reversao", 0)
        ) + 1
    else:
        sequencia = 0

    operacao["sequencia_reversao"] = sequencia

    if sequencia >= 2:
        return {
            "acao": "FECHA",
            "motivo": (
                "Duas velas M5 consecutivas confirmaram "
                "perda estrutural a favor da direção contrária."
            ),
            "preco_referencia": preco,
            "novo_stop": novo_stop,
            "resultado_r": round(resultado_r, 2),
            "sequencia_reversao": sequencia,
            "central": leitura_central
        }

    pressao = identificar_pressao_contraria(
        leitura_central,
        direcao
    )

    if pressao.get("pressao_contraria"):
        return {
            "acao": "REDUZ_RISCO",
            "motivo": (
                "As transições históricas confiáveis da região "
                "indicam maior frequência de deslocamento "
                "contrário à operação. Isso é um alerta, "
                "não uma previsão."
            ),
            "preco_referencia": preco,
            "novo_stop": novo_stop,
            "resultado_r": round(resultado_r, 2),
            "sequencia_reversao": sequencia,
            "central": leitura_central,
            "estatistica_central": pressao
        }

    if acao_stop:
        return {
            "acao": "MUDA_STOP",
            "motivo": motivo_stop,
            "preco_referencia": preco,
            "novo_stop": round(novo_stop, 3),
            "resultado_r": round(resultado_r, 2),
            "sequencia_reversao": sequencia,
            "central": leitura_central
        }

    return {
        "acao": "CONTINUA",
        "motivo": (
            "Não houve confirmação suficiente para encerrar "
            "nem condição para elevar o stop nesta vela."
        ),
        "preco_referencia": preco,
        "novo_stop": round(novo_stop, 3),
        "resultado_r": round(resultado_r, 2),
        "sequencia_reversao": sequencia,
        "central": leitura_central
    }


def formatar_mensagem(decisao, operacao):
    acao = decisao["acao"]

    titulos = {
        "CONTINUA": "CONTINUA",
        "FECHA": "FECHA",
        "REDUZ_RISCO": "REDUZ RISCO",
        "MUDA_STOP": "MUDA STOP"
    }

    direcao = operacao.get("direcao", "")
    preco = decisao.get("preco_referencia")
    stop = decisao.get("novo_stop")
    alvo = operacao.get("alvo")

    linhas = [
        f"XAUUSD | {titulos.get(acao, acao)}",
        "",
        f"Operação: {direcao}",
        f"Preço atual: {preco:.3f}",
        f"Stop atual/sugerido: {stop:.3f}",
        f"Alvo: {float(alvo):.3f}",
        f"Resultado: {decisao.get('resultado_r', 0):.2f}R",
        "",
        f"Por quê: {decisao.get('motivo', '')}"
    ]

    central = decisao.get("central") or {}

    if central.get("disponivel"):
        linhas.extend([
            "",
            f"Região atual: {central.get('regiao')}",
            (
                "Faixa: "
                f"{central.get('limite_inferior'):.3f} "
                "até "
                f"{central.get('limite_superior'):.3f}"
            )
        ])

        idade = central.get("idade_minutos")

        if idade is not None:
            linhas.append(
                f"Idade dos dados da Central: {idade:.1f} min"
            )

    linhas.extend([
        "",
        "Orientação calculada; execução manual."
    ])

    return "\n".join(linhas)
