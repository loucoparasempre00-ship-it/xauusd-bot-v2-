from leitor_central_autonomo_xau import (
    identificar_pressao_contraria
)


def numero(valor):
    try:
        return None if valor is None else float(valor)
    except (ValueError, TypeError):
        return None


def movimento_favoravel(preco, entrada, direcao):
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
    direcao = str(operacao.get("direcao", "")).upper()

    if direcao not in ("COMPRA", "VENDA"):
        raise ValueError("Direção inválida.")

    entrada = numero(operacao.get("entrada_real"))
    stop_atual = numero(operacao.get("stop"))
    stop_original = numero(operacao.get("stop_original"))
    alvo = numero(operacao.get("alvo"))
    preco = numero(vela.get("close"))

    ema9 = numero(ema9)
    ema21 = numero(ema21)
    valor_rsi = numero(valor_rsi)
    atr = numero(atr)

    if None in (entrada, stop_atual, alvo, preco, atr):
        raise ValueError("Dados insuficientes.")

    if stop_original is None:
        stop_original = stop_atual

    risco_inicial = abs(entrada - stop_original)

    if risco_inicial <= 0 or atr <= 0:
        raise ValueError("Risco inicial ou ATR inválido.")

    movimento = movimento_favoravel(
        preco, entrada, direcao
    )

    resultado_r = movimento / risco_inicial

    novo_stop = stop_atual
    motivo_stop = None
    acao_stop = None

    # Ao atingir 1R, proteger o capital.
    if resultado_r >= 1.0:
        candidato = entrada

        if stop_mais_protetor(
            candidato, novo_stop, direcao
        ):
            novo_stop = candidato
            acao_stop = "MUDA_STOP"
            motivo_stop = (
                "O preço alcançou 1R. "
                "Proteger a entrada no zero a zero."
            )

    # A partir de 1.25R, acompanhar o preço com ATR.
    if resultado_r >= 1.25:
        distancia = max(atr * 0.80, 0.10)

        if direcao == "COMPRA":
            candidato = preco - distancia
        else:
            candidato = preco + distancia

        if stop_mais_protetor(
            candidato, novo_stop, direcao
        ):
            novo_stop = candidato
            acao_stop = "MUDA_STOP"
            motivo_stop = (
                "Movimento favorável acima de 1.25R. "
                "Stop móvel calculado por 0.80 ATR."
            )

    # Verificação de enfraquecimento estrutural.
    reversao = False

    if None not in (ema9, ema21, valor_rsi):
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
                "estrutura contrária à operação."
            ),
            "preco_referencia": preco,
            "novo_stop": round(novo_stop, 3),
            "resultado_r": round(resultado_r, 2),
            "sequencia_reversao": sequencia,
            "central": leitura_central
        }

    # Central só sugere proteção quando existe lucro
    # suficiente para tornar o stop mais protetor.
    pressao = identificar_pressao_contraria(
        leitura_central, direcao
    )

    if (
        pressao.get("pressao_contraria")
        and resultado_r >= 0.50
    ):
        distancia = max(atr * 0.50, 0.10)

        if direcao == "COMPRA":
            candidato = preco - distancia
        else:
            candidato = preco + distancia

        if stop_mais_protetor(
            candidato, novo_stop, direcao
        ):
            novo_stop = candidato

            return {
                "acao": "REDUZ_RISCO",
                "motivo": (
                    "A Central identificou pressão histórica "
                    "contrária na região atual. O stop pode "
                    "ser aproximado para proteger o resultado."
                ),
                "preco_referencia": preco,
                "novo_stop": round(novo_stop, 3),
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
            "A operação mantém condições para continuar. "
            "Não há confirmação suficiente para fechar "
            "nem novo nível de proteção nesta vela."
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

    preco = float(decisao["preco_referencia"])
    stop = float(decisao["novo_stop"])
    alvo = float(operacao["alvo"])

    linhas = [
        f"XAUUSD | {titulos.get(acao, acao)}",
        "",
        f"Operação: {operacao['direcao']}",
        f"Preço atual: {preco:.3f}",
        f"Stop de referência: {float(operacao['stop']):.3f}",
        f"Stop recomendado: {stop:.3f}",
        f"Alvo: {alvo:.3f}",
        f"Resultado: {decisao.get('resultado_r', 0):.2f}R",
        "",
        f"POR QUÊ: {decisao.get('motivo', '')}"
    ]

    central = decisao.get("central") or {}

    if central.get("disponivel"):
        linhas.extend([
            "",
            f"Região atual: {central.get('regiao')}",
            (
                f"Faixa: {central.get('limite_inferior'):.3f} "
                f"a {central.get('limite_superior'):.3f}"
            )
        ])

        idade = central.get("idade_minutos")

        if idade is not None:
            linhas.append(
                f"Idade da Central: {idade:.1f} minutos"
            )

    if acao in ("MUDA_STOP", "REDUZ_RISCO"):
        linhas.extend([
            "",
            "ATENÇÃO: stop apenas recomendado.",
            "Altere manualmente na corretora.",
            "Depois envie STOP OK para confirmar.",
            "O robô manterá o stop anterior até sua confirmação."
        ])

    elif acao == "FECHA":
        linhas.extend([
            "",
            "RECOMENDAÇÃO DE SAÍDA.",
            "Se fechar manualmente na corretora, envie CANCELA.",
            "O robô não executa ordens."
        ])

    else:
        linhas.append(
            "Acompanhamento por candle M5 fechado."
        )

    return "\n".join(linhas)
