from math import isfinite


def validar_candles(candles):
    if not isinstance(candles, list) or len(candles) < 10:
        raise ValueError("São necessários pelo menos 10 candles.")

    dados = []

    for i, candle in enumerate(candles):
        try:
            abertura = float(candle["open"])
            maxima = float(candle["high"])
            minima = float(candle["low"])
            fechamento = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"OHLC inválido no candle {i}.")

        valores = (abertura, maxima, minima, fechamento)

        if not all(isfinite(v) and v > 0 for v in valores):
            raise ValueError(f"Preço inválido no candle {i}.")

        if maxima < max(abertura, fechamento, minima):
            raise ValueError(f"Máxima inconsistente no candle {i}.")

        if minima > min(abertura, fechamento):
            raise ValueError(f"Mínima inconsistente no candle {i}.")

        dados.append({
            "open": abertura,
            "high": maxima,
            "low": minima,
            "close": fechamento,
        })

    return dados


def detectar_pivos(candles, lado=2):
    dados = validar_candles(candles)

    if not isinstance(lado, int) or lado < 1:
        raise ValueError("O lado do pivô deve ser um inteiro positivo.")

    topos = []
    fundos = []

    for i in range(lado, len(dados) - lado):
        janela = dados[i - lado:i + lado + 1]
        atual = dados[i]

        if all(
            atual["high"] >= c["high"]
            for j, c in enumerate(janela)
            if j != lado
        ):
            topos.append({"indice": i, "preco": atual["high"]})

        if all(
            atual["low"] <= c["low"]
            for j, c in enumerate(janela)
            if j != lado
        ):
            fundos.append({"indice": i, "preco": atual["low"]})

    return topos, fundos


def detectar_bos_choch(candles, lado=2):
    """
    Detecta rompimentos de pivôs confirmados.
    CHoCH é uma indicação estrutural preliminar, não uma previsão.
    """
    dados = validar_candles(candles)
    topos, fundos = detectar_pivos(dados, lado)

    eventos = []
    tendencia = "INDEFINIDA"
    ultimo_topo_rompido = None
    ultimo_fundo_rompido = None

    # Percorre apenas candles posteriores à confirmação dos pivôs.
    for i, candle in enumerate(dados):
        topos_anteriores = [
            p for p in topos if p["indice"] + lado < i
        ]
        fundos_anteriores = [
            p for p in fundos if p["indice"] + lado < i
        ]

        topo = next(
            (
                p for p in reversed(topos_anteriores)
                if p["indice"] != ultimo_topo_rompido
            ),
            None,
        )

        fundo = next(
            (
                p for p in reversed(fundos_anteriores)
                if p["indice"] != ultimo_fundo_rompido
            ),
            None,
        )

        rompeu_topo = (
            topo is not None and candle["close"] > topo["preco"]
        )
        rompeu_fundo = (
            fundo is not None and candle["close"] < fundo["preco"]
        )

        if rompeu_topo:
            tipo = "CHoCH_ALTA" if tendencia == "BAIXA" else "BOS_ALTA"
            eventos.append({
                "indice": i,
                "tipo": tipo,
                "nivel_rompido": topo["preco"],
            })
            tendencia = "ALTA"
            ultimo_topo_rompido = topo["indice"]

        elif rompeu_fundo:
            tipo = "CHoCH_BAIXA" if tendencia == "ALTA" else "BOS_BAIXA"
            eventos.append({
                "indice": i,
                "tipo": tipo,
                "nivel_rompido": fundo["preco"],
            })
            tendencia = "BAIXA"
            ultimo_fundo_rompido = fundo["indice"]

    return {
        "eventos": eventos,
        "tendencia_estrutural": tendencia,
    }


def detectar_triangulo(candles, quantidade=20, tolerancia=0.15):
    dados = validar_candles(candles)

    if quantidade < 10 or len(dados) < quantidade:
        return {"detectado": False, "tipo": None}

    if not isfinite(tolerancia) or tolerancia <= 0:
        raise ValueError("Tolerância deve ser positiva.")

    janela = dados[-quantidade:]
    largura = max(c["high"] for c in janela) - min(
        c["low"] for c in janela
    )

    if largura <= 0:
        return {"detectado": False, "tipo": None}

    metade = quantidade // 2
    primeira = janela[:metade]
    segunda = janela[metade:]

    topo_1 = max(c["high"] for c in primeira)
    topo_2 = max(c["high"] for c in segunda)
    fundo_1 = min(c["low"] for c in primeira)
    fundo_2 = min(c["low"] for c in segunda)

    reducao_topo = topo_1 - topo_2
    elevacao_fundo = fundo_2 - fundo_1

    limiar = largura * tolerancia

    if reducao_topo > limiar and elevacao_fundo > limiar:
        tipo = "SIMETRICO"
    elif abs(reducao_topo) <= limiar and elevacao_fundo > limiar:
        tipo = "ASCENDENTE"
    elif reducao_topo > limiar and abs(elevacao_fundo) <= limiar:
        tipo = "DESCENDENTE"
    else:
        return {"detectado": False, "tipo": None}

    return {
        "detectado": True,
        "tipo": tipo,
        "candles_analisados": quantidade,
    }


def detectar_ombro_cabeca_ombro(candles, tolerancia=0.02):
    dados = validar_candles(candles)
    topos, _ = detectar_pivos(dados, lado=2)

    if not isfinite(tolerancia) or tolerancia <= 0:
        raise ValueError("Tolerância deve ser positiva.")

    if len(topos) < 3:
        return {"detectado": False, "tipo": None}

    esquerda, cabeca, direita = topos[-3:]
    escala = cabeca["preco"]

    ombros_parecidos = (
        abs(esquerda["preco"] - direita["preco"])
        / escala <= tolerancia
    )

    cabeca_mais_alta = (
        cabeca["preco"] > esquerda["preco"]
        and cabeca["preco"] > direita["preco"]
    )

    detectado = ombros_parecidos and cabeca_mais_alta

    return {
        "detectado": detectado,
        "tipo": "OMBRO_CABECA_OMBRO" if detectado else None,
        "pivos": [esquerda, cabeca, direita] if detectado else [],
        "confirmado": False,
    }


def analisar_padroes(candles):
    dados = validar_candles(candles)

    return {
        "estrutura": detectar_bos_choch(dados),
        "triangulo": detectar_triangulo(dados),
        "ombro_cabeca_ombro": detectar_ombro_cabeca_ombro(dados),
        "observacao": (
            "Detectores preliminares; exigem validação antes de "
            "serem usados para autorizar sinais."
        ),
    }


if __name__ == "__main__":
    print("Modulo de padroes estruturais carregado.")
    print("Sem envio ao Telegram ou execução de ordens.")
