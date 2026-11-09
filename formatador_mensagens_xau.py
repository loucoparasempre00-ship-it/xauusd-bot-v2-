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
