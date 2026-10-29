import csv
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_MEMORIA = DATA / "memoria_agente.json"
ARQUIVO_VELAS = DATA / "historico_velas_m5.csv"
ARQUIVO_RESULTADOS = DATA / "resultados_operacoes.csv"

CAMPOS = [
    "id_sinal",
    "horario_sinal_utc",
    "ativo",
    "timeframe",
    "direcao",
    "entrada",
    "stop",
    "alvo",
    "status",
    "resultado_r",
    "horario_resultado_utc",
    "motivo"
]

STATUS_TERMINAIS = {"ALVO", "STOP", "AMBIGUO"}


def agora_utc():
    return datetime.now(timezone.utc).isoformat()


def numero(valor):
    if valor is None or valor == "":
        return None

    try:
        if isinstance(valor, str):
            valor = valor.strip().replace(",", ".")
        resultado = float(valor)
        return resultado if resultado == resultado else None
    except (ValueError, TypeError):
        return None


def ler_json(caminho):
    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except FileNotFoundError:
        print(f"Arquivo ainda nao existe: {caminho}")
    except (OSError, json.JSONDecodeError) as erro:
        print(f"Erro lendo {caminho}: {erro}")

    return {}


def interpretar_data(valor):
    if not valor:
        return None

    texto = str(valor).strip()

    try:
        data = datetime.fromisoformat(texto.replace("Z", "+00:00"))

        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)

        return data.astimezone(timezone.utc)

    except ValueError:
        pass

    formatos = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
    )

    for formato in formatos:
        try:
            return datetime.strptime(texto, formato).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

    return None


def obter_campo(registro, nomes):
    normalizados = {
        str(chave).strip().lower(): valor
        for chave, valor in registro.items()
        if chave is not None
    }

    for nome in nomes:
        valor = normalizados.get(nome.lower())
        if valor is not None and str(valor).strip() != "":
            return valor

    return None


def ler_velas():
    if not ARQUIVO_VELAS.exists():
        print(f"Historico de velas nao encontrado: {ARQUIVO_VELAS}")
        return []

    velas = []

    with ARQUIVO_VELAS.open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        amostra = arquivo.read(4096)
        arquivo.seek(0)

        try:
            delimitador = csv.Sniffer().sniff(
                amostra, delimiters=";,\t"
            ).delimiter
        except csv.Error:
            delimitador = ","

        leitor = csv.DictReader(arquivo, delimiter=delimitador)

        for linha in leitor:
            data_texto = obter_campo(
                linha,
                [
                    "timestamp_utc",
                    "candle_utc",
                    "timestamp",
                    "datetime",
                    "date",
                    "time",
                    "data",
                    "horario",
                ],
            )

            abertura = numero(obter_campo(
                linha, ["open", "abertura", "o"]
            ))
            maxima = numero(obter_campo(
                linha, ["high", "maxima", "máxima", "h"]
            ))
            minima = numero(obter_campo(
                linha, ["low", "minima", "mínima", "l"]
            ))
            fechamento = numero(obter_campo(
                linha, ["close", "fechamento", "c"]
            ))

            data = interpretar_data(data_texto)

            if (
                data is None
                or abertura is None
                or maxima is None
                or minima is None
                or fechamento is None
            ):
                continue

            velas.append({
                "data": data,
                "abertura": abertura,
                "maxima": maxima,
                "minima": minima,
                "fechamento": fechamento,
            })

    velas.sort(key=lambda vela: vela["data"])
    print(f"Velas validas carregadas: {len(velas)}")

    return velas


def ler_resultados_existentes():
    if not ARQUIVO_RESULTADOS.exists():
        return {}

    existentes = {}

    with ARQUIVO_RESULTADOS.open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")

        for linha in leitor:
            identificador = (linha.get("id_sinal") or "").strip()

            if identificador:
                existentes[identificador] = {
                    campo: linha.get(campo, "")
                    for campo in CAMPOS
                }

    return existentes


def extrair_sinais(memoria):
    if isinstance(memoria, list):
        return memoria

    if not isinstance(memoria, dict):
        return []

    historico = memoria.get("historico", [])

    return historico if isinstance(historico, list) else []


def criar_id(sinal):
    candle = str(sinal.get("candle_utc", "")).strip()
    direcao = str(
        sinal.get("decisao", sinal.get("sinal", ""))
    ).strip().upper()

    entrada = numero(sinal.get("entrada"))
    stop = numero(sinal.get("stop", sinal.get("stop_loss")))
    alvo = numero(sinal.get("gain", sinal.get("alvo")))

    if not candle or entrada is None or stop is None or alvo is None:
        return None

    return (
        f"{candle}|{direcao}|{entrada:.8f}|"
        f"{stop:.8f}|{alvo:.8f}"
    )


def atualizar_operacao(operacao, velas):
    if operacao.get("status") in STATUS_TERMINAIS:
        return

    direcao = operacao["direcao"]
    entrada = float(operacao["entrada"])
    stop = float(operacao["stop"])
    alvo = float(operacao["alvo"])

    data_sinal = interpretar_data(operacao["horario_sinal_utc"])

    if data_sinal is None:
        operacao["motivo"] = "Data do sinal invalida"
        return

    risco = abs(entrada - stop)

    if risco == 0:
        operacao["motivo"] = "Distancia do stop igual a zero"
        return

    for vela in velas:
        # Só avaliar candles posteriores ao candle do sinal.
        if vela["data"] <= data_sinal:
            continue

        if direcao == "COMPRA":
            stop_atingido = vela["minima"] <= stop
            alvo_atingido = vela["maxima"] >= alvo
        else:
            stop_atingido = vela["maxima"] >= stop
            alvo_atingido = vela["minima"] <= alvo

        if stop_atingido and alvo_atingido:
            operacao["status"] = "AMBIGUO"
            operacao["resultado_r"] = ""
            operacao["horario_resultado_utc"] = (
                vela["data"].isoformat()
            )
            operacao["motivo"] = (
                "Stop e alvo tocados no mesmo candle; "
                "a ordem intr candle nao pode ser confirmada"
            )
            return

        if stop_atingido:
            operacao["status"] = "STOP"
            operacao["resultado_r"] = "-1.0"
            operacao["horario_resultado_utc"] = (
                vela["data"].isoformat()
            )
            operacao["motivo"] = "Stop-loss atingido"
            return

        if alvo_atingido:
            recompensa = abs(alvo - entrada)
            resultado_r = recompensa / risco

            operacao["status"] = "ALVO"
            operacao["resultado_r"] = str(round(resultado_r, 4))
            operacao["horario_resultado_utc"] = (
                vela["data"].isoformat()
            )
            operacao["motivo"] = "Alvo atingido"
            return

    operacao["status"] = "EM_ABERTO"
    operacao["motivo"] = "Stop e alvo ainda nao identificados"


def salvar_csv(operacoes):
    DATA.mkdir(parents=True, exist_ok=True)

    with ARQUIVO_RESULTADOS.open(
        "w", encoding="utf-8-sig", newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=CAMPOS,
            delimiter=";",
            extrasaction="ignore",
        )

        escritor.writeheader()

        for operacao in sorted(
            operacoes.values(),
            key=lambda item: item.get("horario_sinal_utc", "")
        ):
            escritor.writerow({
                campo: operacao.get(campo, "")
                for campo in CAMPOS
            })

    print(f"CSV atualizado: {ARQUIVO_RESULTADOS}")


def main():
    print("=== ACOMPANHAMENTO DE OPERACOES XAUUSD ===")

    memoria = ler_json(ARQUIVO_MEMORIA)
    velas = ler_velas()
    operacoes = ler_resultados_existentes()

    sinais = extrair_sinais(memoria)
    novos = 0

    for sinal in sinais:
        if not isinstance(sinal, dict):
            continue

        direcao = str(
            sinal.get("decisao", sinal.get("sinal", ""))
        ).strip().upper()

        if direcao not in ("COMPRA", "VENDA"):
            continue

        identificador = criar_id(sinal)

        if identificador is None:
            print("Sinal ignorado: faltam data, entrada, stop ou alvo.")
            continue

        if identificador in operacoes:
            continue

        entrada = numero(sinal.get("entrada"))
        stop = numero(sinal.get("stop", sinal.get("stop_loss")))
        alvo = numero(sinal.get("gain", sinal.get("alvo")))

        operacoes[identificador] = {
            "id_sinal": identificador,
            "horario_sinal_utc": str(sinal["candle_utc"]),
            "ativo": str(sinal.get("ativo", "XAUUSD")),
            "timeframe": str(sinal.get("timeframe", "M5")),
            "direcao": direcao,
            "entrada": entrada,
            "stop": stop,
            "alvo": alvo,
            "status": "EM_ABERTO",
            "resultado_r": "",
            "horario_resultado_utc": "",
            "motivo": "",
        }

        novos += 1
        print(f"Nova operacao registrada: {direcao} | {identificador}")

    for operacao in operacoes.values():
        atualizar_operacao(operacao, velas)

    salvar_csv(operacoes)

    total = len(operacoes)
    abertas = sum(
        op.get("status") == "EM_ABERTO"
        for op in operacoes.values()
    )
    alvos = sum(
        op.get("status") == "ALVO"
        for op in operacoes.values()
    )
    stops = sum(
        op.get("status") == "STOP"
        for op in operacoes.values()
    )
    ambiguas = sum(
        op.get("status") == "AMBIGUO"
        for op in operacoes.values()
    )

    print(f"Operacoes registradas: {total}")
    print(f"Novas nesta execucao: {novos}")
    print(f"Em aberto: {abertas}")
    print(f"Alvos atingidos: {alvos}")
    print(f"Stops atingidos: {stops}")
    print(f"Resultados ambiguos: {ambiguas}")


if __name__ == "__main__":
    main()
