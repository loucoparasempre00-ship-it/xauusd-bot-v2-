from math import isfinite


def validar_candles(candles):
    """Valida os campos OHLC usados pela leitura de mercado."""
    if not isinstance(candles, list) or len(candles) < 5:
        raise ValueError("São necessários pelo menos 5 candles.")

    resultado = []
    ultimo_timestamp = None

    for i, candle in enumerate(candles):
        try:
            o = float(candle["open"])
            h = float(candle["high"])
            low = float(candle["low"])
            c = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Candle {i}: OHLC inválido.")

        if not all(isfinite(v) and v > 0 for v in (o, h, low, c)):
            raise ValueError(f"Candle {i}: preço inválido.")

        if h < max(o, c, low) or low > min(o, c):
            raise ValueError(f"Candle {i}: máxima ou mínima inconsistente.")

        timestamp = candle.get("timestamp")
        if timestamp is not None:
            if not isinstance(timestamp, str):
                raise ValueError(f"Candle {i}: timestamp inválido.")
            if ultimo_timestamp is not None and timestamp <= ultimo_timestamp:
                raise ValueError("Candles duplicados ou fora de ordem.")
            ultimo_timestamp = timestamp

        resultado.append({
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "timestamp": timestamp,
        })

    return resultado


def detectar_pivos(candles, lado=2):
    """
    Identifica topos e fundos confirmados.
    Um pivô só é confirmado após existirem 'lado' candles à direita.
    """
    if lado < 1:
        raise ValueError("O lado deve ser pelo menos 1.")

    dados = validar_candles(candles)
    topos = []
    fundos = []

    for i in range(lado, len(dados) - lado):
        atual = dados[i]
        vizinhos = dados[i - lado:i + lado + 1]

        topo = all(
            atual["high"] >= c["high"] for j, c in enumerate(vizinhos)
            if j != lado
        )
        fundo = all(
            atual["low"] <= c["low"] for j, c in enumerate(vizinhos)
            if j != lado
        )

        registro = {
            "indice": i,
            "preco": atual["high"],
            "timestamp": atual["timestamp"],
        }
        if topo:
            topos.append(registro)

        registro_fundo = {
            "indice": i,
            "preco": atual["low"],
            "timestamp": atual["timestamp"],
        }
        if fundo:
            fundos.append(registro_fundo)

    return {"topos": topos, "fundos": fundos}


def classificar_tendencia(pivos):
    """Classifica a sequência recente de topos e fundos."""
    topos = pivos["topos"]
    fundos = pivos["fundos"]

    if len(topos) < 2 or len(fundos) < 2:
        return "INDEFINIDA"

    topos_altos = topos[-1]["preco"] > topos[-2]["preco"]
    fundos_altos = fundos[-1]["preco"] > fundos[-2]["preco"]
    topos_baixos = topos[-1]["preco"] < topos[-2]["preco"]
    fundos_baixos = fundos[-1]["preco"] < fundos[-2]["preco"]

    if topos_altos and fundos_altos:
        return "ALTA"
    if topos_baixos and fundos_baixos:
        return "BAIXA"
    return "LATERAL_OU_TRANSICAO"


def agrupar_zonas(niveis, tolerancia):
    """Agrupa preços próximos em zonas aproximadas."""
    if tolerancia <= 0 or not isfinite(tolerancia):
        raise ValueError("Tolerância inválida.")

    zonas = []
    for nivel in sorted(niveis):
        if not zonas or nivel - zonas[-1]["maximo"] > tolerancia:
            zonas.append({
                "minimo": nivel,
                "maximo": nivel,
                "quantidade": 1,
            })
        else:
            zona = zonas[-1]
            zona["maximo"] = nivel
            zona["quantidade"] += 1
            zona["minimo"] = min(zona["minimo"], nivel)

    for zona in zonas:
        zona["centro"] = round(
            (zona["minimo"] + zona["maximo"]) / 2, 5
        )

    return zonas


def ler_mercado(candles, lado_pivo=2, tolerancia_zona=1.0):
    """Produz uma estrutura unificada da leitura técnica básica."""
    dados = validar_candles(candles)
    pivos = detectar_pivos(dados, lado=lado_pivo)
    preco = dados[-1]["close"]

    resistencias = agrupar_zonas(
        [p["preco"] for p in pivos["topos"] if p["preco"] >= preco],
        tolerancia_zona,
    )
    suportes = agrupar_zonas(
        [p["preco"] for p in pivos["fundos"] if p["preco"] <= preco],
        tolerancia_zona,
    )

    ultimo_topo = pivos["topos"][-1] if pivos["topos"] else None
    ultimo_fundo = pivos["fundos"][-1] if pivos["fundos"] else None

    rompimentos = []
    if ultimo_topo and preco > ultimo_topo["preco"]:
        rompimentos.append("ROMPIMENTO_DE_TOPO")
    if ultimo_fundo and preco < ultimo_fundo["preco"]:
        rompimentos.append("ROMPIMENTO_DE_FUNDO")

    return {
        "timeframe": "M5",
        "preco_fechamento": preco,
        "tendencia": classificar_tendencia(pivos),
        "topos": pivos["topos"],
        "fundos": pivos["fundos"],
        "suportes": suportes,
        "resistencias": resistencias,
        "rompimentos": rompimentos,
        "figuras": [],
        "observacao": (
            "Figuras gráficas ainda não implementadas. "
            "Usar somente candles fechados."
        ),
    }


if __name__ == "__main__":
    print("Modulo de leitura de mercado carregado.")
    print("Sem conexão com corretora ou envio de sinais.")
