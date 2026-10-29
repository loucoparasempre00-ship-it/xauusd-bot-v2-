import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_RESULTADOS = DATA / "resultados_operacoes.json"

FONTES_SINAIS = [
    DATA / "sinais_pendentes.json",
    DATA / "memoria_agente.json",
]

FONTES_VELAS = [
    DATA / "xauusd_m5_fechados.csv",
    DATA / "historico_velas_m5.csv",
]


def agora_utc():
    return datetime.now(timezone.utc).isoformat()


def normalizar_texto(valor):
    return str(valor or "").strip().lower()


def numero(valor):
    if valor is None or valor == "":
        return None

    try:
        if isinstance(valor, str):
            valor = valor.strip().replace(",", ".")
        return float(valor)
    except (ValueError, TypeError):
        return None


def data_utc(valor):
    if not valor:
        return None

    try:
        texto = str(valor).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(texto)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def ler_json(caminho, padrao):
    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (OSError, json.JSONDecodeError) as erro:
        print(f"AVISO: nao foi possivel ler {caminho}: {erro}")
        return padrao


def salvar_json(caminho, conteudo):
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            conteudo,
            arquivo,
            ensure_ascii=False,
            indent=2,
        )

    temporario.replace(caminho)


def extrair_registros(conteudo):
    if isinstance(conteudo, list):
        return conteudo

    if not isinstance(conteudo, dict):
        return []

    for chave in ("sinais", "historico", "operacoes", "registros"):
        valor = conteudo.get(chave)

        if isinstance(valor, list):
            return valor

    # Tambem aceita um unico sinal em formato de objeto.
    if any(
        chave in conteudo
        for chave in ("sinal", "direcao", "entrada", "stop", "gain")
    ):
        return [conteudo]

    return []


def obter_direcao(registro):
    texto = normalizar_texto(
        registro.get("direcao")
        or registro.get("sinal")
        or registro.get("decisao")
        or registro.get("tipo")
    )

    if texto in ("compra", "buy", "long", "alta"):
        return "COMPRA"

    if texto in ("venda", "sell", "short", "baixa"):
        return "VENDA"

    return None


def obter_timestamp_sinal(registro):
    for chave in (
        "candle_utc",
        "timestamp_utc",
        "timestamp",
        "data_hora",
        "registrado_utc",
        "created_at",
    ):
        instante = data_utc(registro.get(chave))

        if instante:
            return instante

    return None


def identificar_sinal(registro, direcao, instante, entrada, stop, alvo):
    identificador_existente = (
        registro.get("id_sinal")
        or registro.get("signal_id")
        or registro.get("id")
    )

    if identificador_existente:
        return str(identificador_existente)

    base_id = "|".join(
        [
            direcao,
            instante.isoformat(),
            f"{entrada:.5f}",
            f"{stop:.5f}",
            f"{alvo:.5f}",
        ]
    )

    return hashlib.sha256(base_id.encode("utf-8")).hexdigest()[:20]


def carregar_sinais():
    encontrados = {}

    for caminho in FONTES_SINAIS:
        conteudo = ler_json(caminho, {})
        registros = extrair_registros(conteudo)

        for registro in registros:
            if not isinstance(registro, dict):
                continue

            direcao = obter_direcao(registro)

            # SEM_SINAL e registros sem direcao nao sao operacoes.
            if not direcao:
                continue

            entrada = numero(
                registro.get("entrada")
                or registro.get("preco_entrada")
            )
            stop = numero(
                registro.get("stop")
                or registro.get("stop_loss")
                or registro.get("sl")
            )
            alvo = numero(
                registro.get("gain")
                or registro.get("alvo")
                or registro.get("take_profit")
                or registro.get("tp")
            )
            instante = obter_timestamp_sinal(registro)

            # Sem entrada, stop, alvo ou horario nao ha como
            # acompanhar a operacao com seguranca.
            if (
                entrada is None
                or stop is None
                or alvo is None
                or instante is None
            ):
                continue

            # Impede niveis incoerentes com a direcao.
            if direcao == "COMPRA" and not (stop < entrada < alvo):
                print(
                    "Sinal ignorado: niveis incoerentes para COMPRA."
                )
                continue

            if direcao == "VENDA" and not (alvo < entrada < stop):
                print(
                    "Sinal ignorado: niveis incoerentes para VENDA."
                )
                continue

            identificador = identificar_sinal(
                registro,
                direcao,
                instante,
                entrada,
                stop,
                alvo,
            )

            encontrados[identificador] = {
                "id_sinal": identificador,
                "ativo": "XAUUSD",
                "timeframe": "M5",
                "direcao": direcao,
                "horario_sinal_utc": instante.isoformat(),
                "entrada": entrada,
                "stop": stop,
                "alvo": alvo,
                "status": "PENDENTE",
                "resultado_r": None,
                "horario_resultado_utc": None,
                "motivo": None,
                "origem": str(caminho.relative_to(BASE)),
            }

    return encontrados


def localizar_arquivo_velas():
    for caminho in FONTES_VELAS:
        if caminho.exists() and caminho.stat().st_size > 0:
            return caminho

    return None


def obter_campo(registro, alternativas):
    mapa = {
        normalizar_texto(chave): valor
        for chave, valor in registro.items()
        if chave is not None
    }

    for alternativa in alternativas:
        valor = mapa.get(normalizar_texto(alternativa))

        if valor is not None and str(valor).strip() != "":
            return valor

    return None


def carregar_velas():
    caminho = localizar_arquivo_velas()

    if caminho is None:
        print("AVISO: nenhum CSV de velas encontrado.")
        print("Esperado: data/xauusd_m5_fechados.csv")
        print("Ou: data/historico_velas_m5.csv")
        return []

    velas = []

    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo)

        for registro in leitor:
            instante = data_utc(
                obter_campo(
                    registro,
                    [
                        "timestamp_utc",
                        "candle_utc",
                        "timestamp",
                        "datetime",
                        "date",
                        "time",
                        "data",
                    ],
                )
            )

            abertura = numero(
                obter_campo(registro, ["open", "abertura", "o"])
            )
            maxima = numero(
                obter_campo(registro, ["high", "maxima", "máxima", "h"])
            )
            minima = numero(
                obter_campo(registro, ["low", "minima", "mínima", "l"])
            )
            fechamento = numero(
                obter_campo(registro, ["close", "fechamento", "c"])
            )

            if (
                instante is None
                or abertura is None
                or maxima is None
                or minima is None
                or fechamento is None
            ):
                continue

            velas.append(
                {
                    "timestamp": instante,
                    "open": abertura,
                    "high": maxima,
                    "low": minima,
                    "close": fechamento,
                }
            )

    velas.sort(key=lambda vela: vela["timestamp"])

    print(f"Velas carregadas: {len(velas)}")
    print(f"Arquivo de velas: {caminho}")

    return velas


def verificar_resultado(operacao, velas):
    if operacao["status"] != "PENDENTE":
        return False

    instante_sinal = data_utc(operacao["horario_sinal_utc"])

    if instante_sinal is None:
        return False

    entrada = operacao["entrada"]
    stop = operacao["stop"]
    alvo = operacao["alvo"]
    direcao = operacao["direcao"]

    for vela in velas:
        # Ignora a vela do sinal e todas as anteriores.
        if vela["timestamp"] <= instante_sinal:
            continue

        if direcao == "COMPRA":
            stop_atingido = vela["low"] <= stop
            alvo_atingido = vela["high"] >= alvo
        else:
            stop_atingido = vela["high"] >= stop
            alvo_atingido = vela["low"] <= alvo

        if not stop_atingido and not alvo_atingido:
            continue

        # OHLC M5 nao revela qual nivel foi atingido primeiro
        # quando stop e alvo aparecem na mesma vela.
        if stop_atingido and alvo_atingido:
            operacao["status"] = "AMBIGUO"
            operacao["motivo"] = (
                "Stop e alvo aparecem na mesma vela M5; "
                "a sequencia nao pode ser confirmada pelo OHLC."
            )
            operacao["horario_resultado_utc"] = (
                vela["timestamp"].isoformat()
            )
            operacao["resultado_r"] = None

        elif stop_atingido:
            operacao["status"] = "STOP"
            operacao["motivo"] = "Stop confirmado pela vela M5."
            operacao["horario_resultado_utc"] = (
                vela["timestamp"].isoformat()
            )
            operacao["resultado_r"] = -1.0

        else:
            operacao["status"] = "ALVO"
            operacao["motivo"] = "Alvo confirmado pela vela M5."
            operacao["horario_resultado_utc"] = (
                vela["timestamp"].isoformat()
            )
            operacao["resultado_r"] = round(
                abs(alvo - entrada) / abs(entrada - stop),
                4,
            )

        operacao["atualizado_utc"] = agora_utc()
        return True

    return False


def main():
    DATA.mkdir(parents=True, exist_ok=True)

    estado = ler_json(
        ARQUIVO_RESULTADOS,
        {
            "atualizado_utc": None,
            "total_operacoes": 0,
            "total_pendentes": 0,
            "total_alvos": 0,
            "total_stops": 0,
            "total_ambiguos": 0,
            "operacoes": [],
        },
    )

    if not isinstance(estado, dict):
        estado = {"operacoes": []}

    operacoes_anteriores = estado.get("operacoes", [])

    if not isinstance(operacoes_anteriores, list):
        operacoes_anteriores = []

    por_id = {}

    for operacao in operacoes_anteriores:
        if isinstance(operacao, dict) and operacao.get("id_sinal"):
            por_id[str(operacao["id_sinal"])] = operacao

    sinais_novos = carregar_sinais()
    adicionadas = 0

    for identificador, sinal in sinais_novos.items():
        if identificador not in por_id:
            por_id[identificador] = sinal
            adicionadas += 1

    velas = carregar_velas()
    alteradas = 0

    if velas:
        for operacao in por_id.values():
            if verificar_resultado(operacao, velas):
                alteradas += 1

    lista = sorted(
        por_id.values(),
        key=lambda item: item.get("horario_sinal_utc", ""),
    )

    estado_final = {
        "atualizado_utc": agora_utc(),
        "total_operacoes": len(lista),
        "total_pendentes": sum(
            item.get("status") == "PENDENTE" for item in lista
        ),
        "total_alvos": sum(
            item.get("status") == "ALVO" for item in lista
        ),
        "total_stops": sum(
            item.get("status") == "STOP" for item in lista
        ),
        "total_ambiguos": sum(
            item.get("status") == "AMBIGUO" for item in lista
        ),
        "novas_operacoes": adicionadas,
        "resultados_confirmados_nesta_execucao": alteradas,
        "operacoes": lista,
    }

    salvar_json(ARQUIVO_RESULTADOS, estado_final)

    print("Acompanhamento concluido.")
    print(f"Novas operacoes: {adicionadas}")
    print(f"Resultados atualizados: {alteradas}")
    print(f"Pendentes: {estado_final['total_pendentes']}")
    print(f"Alvos: {estado_final['total_alvos']}")
    print(f"Stops: {estado_final['total_stops']}")
    print(f"Ambiguos: {estado_final['total_ambiguos']}")
    print(f"Arquivo salvo: {ARQUIVO_RESULTADOS}")


if __name__ == "__main__":
    main()
