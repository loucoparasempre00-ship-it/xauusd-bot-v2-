
import csv
import json
import math
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_MEMORIA = DATA / "memoria_agente.json"
ARQUIVO_VELAS = DATA / "historico_velas_m5.csv"
ARQUIVO_RESULTADOS = DATA / "resultados_operacoes.csv"

ATR_PERIODO = 14
STOP_ATR = 1.5
RR = 1.5

CAMPOS = [
    "id_sinal",
    "tipo_avaliacao",
    "origem",
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
    "motivo",
]


def texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def numero(valor):
    if valor is None or valor == "":
        return None

    try:
        if isinstance(valor, str):
            valor = valor.strip().replace(",", ".")
        resultado = float(valor)
        return resultado if math.isfinite(resultado) else None
    except (ValueError, TypeError):
        return None


def normalizar_chave(valor):
    valor = texto(valor).lower().strip()
    return (
        valor.replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )


def primeiro(registro, nomes, padrao=None):
    if not isinstance(registro, dict):
        return padrao

    mapa = {
        normalizar_chave(chave): valor
        for chave, valor in registro.items()
    }

    for nome in nomes:
        chave = normalizar_chave(nome)
        if chave in mapa and mapa[chave] not in (None, ""):
            return mapa[chave]

    return padrao


def data_utc(valor):
    valor = texto(valor)
    if not valor:
        return None

    try:
        valor = valor.replace("Z", "+00:00")
        resultado = datetime.fromisoformat(valor)

        if resultado.tzinfo is None:
            resultado = resultado.replace(tzinfo=timezone.utc)

        return resultado.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def data_iso(valor):
    resultado = data_utc(valor)
    if resultado is None:
        return ""
    return resultado.isoformat()


def ler_csv(caminho):
    if not caminho.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(8192)
        arquivo.seek(0)

        try:
            dialeto = csv.Sniffer().sniff(
                amostra,
                delimiters=";,\t|"
            )
        except csv.Error:
            dialeto = csv.excel
            dialeto.delimiter = ";"

        leitor = csv.DictReader(arquivo, dialect=dialeto)

        linhas = []
        for linha in leitor:
            if linha and any(texto(v) for v in linha.values()):
                linhas.append({
                    normalizar_chave(k): v
                    for k, v in linha.items()
                    if k is not None
                })

        return linhas


def carregar_memoria():
    if not ARQUIVO_MEMORIA.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {ARQUIVO_MEMORIA}"
        )

    with ARQUIVO_MEMORIA.open("r", encoding="utf-8") as arquivo:
        dados = json.load(arquivo)

    if isinstance(dados, list):
        registros = dados
    elif isinstance(dados, dict):
        registros = None

        for chave in (
            "historico",
            "registros",
            "memoria",
            "sinais",
            "resultados",
            "decisoes",
            "dados",
        ):
            valor = dados.get(chave)
            if isinstance(valor, list):
                registros = valor
                break

        if registros is None:
            registros = [dados]
    else:
        raise ValueError(
            "Formato inesperado em memoria_agente.json."
        )

    return [r for r in registros if isinstance(r, dict)]


def carregar_velas():
    linhas = ler_csv(ARQUIVO_VELAS)
    velas = []

    for linha in linhas:
        horario = primeiro(
            linha,
            [
                "timestamp_utc",
                "candle_utc",
                "datetime",
                "timestamp",
                "time",
                "date",
                "data_hora",
                "horario",
                "data",
            ],
        )

        dt = data_utc(horario)
        abertura = numero(primeiro(linha, ["open", "abertura", "o"]))
        maxima = numero(primeiro(linha, ["high", "maxima", "máxima", "h"]))
        minima = numero(primeiro(linha, ["low", "minima", "mínima", "l"]))
        fechamento = numero(primeiro(linha, ["close", "fechamento", "c"]))

        if (
            dt is None
            or abertura is None
            or maxima is None
            or minima is None
            or fechamento is None
        ):
            continue

        if maxima < minima:
            continue

        velas.append({
            "dt": dt,
            "open": abertura,
            "high": maxima,
            "low": minima,
            "close": fechamento,
        })

    velas.sort(key=lambda v: v["dt"])

    unicas = {}
    for vela in velas:
        unicas[vela["dt"]] = vela

    velas = sorted(unicas.values(), key=lambda v: v["dt"])

    if not velas:
        raise ValueError(
            "Nenhuma vela válida encontrada. "
            "Confira os cabeçalhos de data, open, high, low e close."
        )

    return velas


def calcular_atr(velas, horario_sinal, periodo=ATR_PERIODO):
    anteriores = [
        v for v in velas
        if v["dt"] <= horario_sinal
    ]

    if len(anteriores) < periodo + 1:
        return None

    ranges = []

    for i in range(1, len(anteriores)):
        atual = anteriores[i]
        anterior = anteriores[i - 1]

        tr = max(
            atual["high"] - atual["low"],
            abs(atual["high"] - anterior["close"]),
            abs(atual["low"] - anterior["close"]),
        )

        ranges.append(tr)

    if len(ranges) < periodo:
        return None

    # ATR simples das últimas 14 velas fechadas disponíveis
    ultimos = ranges[-periodo:]
    return sum(ultimos) / periodo


def obter_direcao(registro):
    valor = primeiro(
        registro,
        ["direcao", "direction", "decisao", "sinal", "signal", "acao", "ação"],
        "",
    )

    valor = texto(valor).upper()

    if valor in ("BUY", "LONG", "COMPRA", "COMPRAR"):
        return "COMPRA"

    if valor in ("SELL", "SHORT", "VENDA", "VENDER"):
        return "VENDA"

    if valor not in ("", "SEM_SINAL", "NO_SIGNAL", "NONE", "NEUTRO"):
        # Não converter valores desconhecidos em uma direção.
        pass

    contexto = " ".join(
        texto(v)
        for k, v in registro.items()
        if any(
            parte in normalizar_chave(k)
            for parte in (
                "justific",
                "context",
                "analise",
                "análise",
                "tendencia",
                "tendência",
                "motivo",
                "resumo",
                "leitura",
            )
        )
    ).lower()

    contexto = (
        contexto.replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )

    termos_alta = [
        "tendencia de alta",
        "impulso comprador",
        "pressao compradora",
        "compradores dominam",
        "ema9 acima da ema21",
        "ema 9 acima da ema 21",
        "sinal de compra",
        "direcao compradora",
        "movimento ascendente",
    ]

    termos_baixa = [
        "tendencia de baixa",
        "impulso vendedor",
        "pressao vendedora",
        "vendedores dominam",
        "ema9 abaixo da ema21",
        "ema 9 abaixo da ema 21",
        "sinal de venda",
        "direcao vendedora",
        "movimento descendente",
    ]

    alta = any(t in contexto for t in termos_alta)
    baixa = any(t in contexto for t in termos_baixa)

    if alta and not baixa:
        return "COMPRA"

    if baixa and not alta:
        return "VENDA"

    return ""


def obter_horario(registro):
    return data_utc(
        primeiro(
            registro,
            [
                "candle_utc",
                "timestamp_utc",
                "horario_sinal_utc",
                "timestamp",
                "datetime",
                "data_hora",
                "horario",
                "data",
            ],
        )
    )


def obter_entrada(registro):
    return numero(
        primeiro(
            registro,
            [
                "entrada",
                "preco_entrada",
                "preço_entrada",
                "entry",
                "preco",
                "preço",
                "close",
                "fechamento",
            ],
        )
    )


def criar_id(tipo, horario, direcao, entrada, stop, alvo):
    base = "|".join([
        tipo,
        horario.isoformat() if horario else "",
        direcao or "",
        str(entrada if entrada is not None else ""),
        str(stop if stop is not None else ""),
        str(alvo if alvo is not None else ""),
    ])

    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def normalizar_resultado(linha):
    saida = {campo: texto(linha.get(campo, "")) for campo in CAMPOS}

    if not saida["tipo_avaliacao"]:
        direcao = saida["direcao"].upper()
        saida["tipo_avaliacao"] = (
            "REAL" if direcao in ("COMPRA", "VENDA") else "HIPOTETICA"
        )

    if not saida["origem"]:
        saida["origem"] = "memoria_agente.json"

    return saida


def carregar_resultados_existentes():
    if not ARQUIVO_RESULTADOS.exists():
        return {}

    try:
        linhas = ler_csv(ARQUIVO_RESULTADOS)
    except Exception as erro:
        raise RuntimeError(
            f"Não foi possível ler o CSV de resultados: {erro}"
        ) from erro

    resultados = {}

    for linha in linhas:
        registro = normalizar_resultado(linha)
        identificador = registro["id_sinal"]

        if identificador:
            resultados[identificador] = registro

    return resultados


def montar_candidato(registro, velas):
    horario = obter_horario(registro)

    if horario is None:
        return None

    sinal_original = texto(
        primeiro(
            registro,
            ["sinal", "signal", "decisao", "direcao", "direction"],
            "",
        )
    ).upper()

    direcao = obter_direcao(registro)

    entrada = obter_entrada(registro)
    stop = numero(primeiro(registro, ["stop", "stop_loss", "sl"]))
    alvo = numero(
        primeiro(
            registro,
            ["alvo", "gain", "take_profit", "target", "tp"],
        )
    )

    hipotetica = sinal_original in (
        "SEM_SINAL",
        "NO_SIGNAL",
        "NONE",
        "",
        "NEUTRO",
    )

    # Se o registro já traz uma direção válida, é um sinal real,
    # mesmo que outro campo auxiliar tenha valor diferente.
    if direcao and not (
        sinal_original in ("SEM_SINAL", "NO_SIGNAL", "NONE", "NEUTRO")
    ):
        hipotetica = False

    tipo = "HIPOTETICA" if hipotetica else "REAL"
    origem = "SEM_SINAL" if hipotetica else "SINAL_REAL"

    if entrada is None:
        velas_ate_sinal = [
            v for v in velas
            if v["dt"] <= horario
        ]

        if velas_ate_sinal:
            entrada = velas_ate_sinal[-1]["close"]

    motivo = texto(
        primeiro(
            registro,
            ["justificativa", "motivo", "contexto", "analise", "análise"],
            "",
        )
    )

    status_inicial = "EM_ABERTO"

    if entrada is None:
        status_inicial = "SEM_PRECO"
        motivo = (
            motivo + " | Não foi possível determinar o preço de entrada."
        ).strip(" |")

    elif hipotetica and not direcao:
        status_inicial = "SEM_DIRECAO"
        motivo = (
            motivo
            + " | O contexto disponível no momento do registro "
              "não determina uma direção com segurança."
        ).strip(" |")

    elif hipotetica:
        atr = calcular_atr(velas, horario)

        if atr is None or atr <= 0:
            status_inicial = "SEM_ATR"
            motivo = (
                motivo
                + " | ATR indisponível no horário do registro; "
                  "simulação não criada."
            ).strip(" |")
        else:
            risco = STOP_ATR * atr
            distancia_alvo = risco * RR

            if direcao == "COMPRA":
                stop = entrada - risco
                alvo = entrada + distancia_alvo
            else:
                stop = entrada + risco
                alvo = entrada - distancia_alvo

            motivo = (
                motivo
                + f" | Simulação hipotética: ATR14={atr:.5f}, "
                  f"stop={STOP_ATR} ATR, RR=1:{RR}. "
                  "Direção baseada apenas no contexto do registro."
            ).strip(" |")

    elif stop is None or alvo is None:
        status_inicial = "SEM_NIVEIS"
        motivo = (
            motivo
            + " | Sinal real sem stop ou alvo válido; "
              "não foi inventado um nível."
        ).strip(" |")

    identificador = criar_id(
        tipo, horario, direcao, entrada, stop, alvo
    )

    return {
        "id_sinal": identificador,
        "tipo_avaliacao": tipo,
        "origem": origem,
        "horario_sinal_utc": horario.isoformat(),
        "ativo": texto(primeiro(registro, ["ativo", "symbol", "ticker"], "XAUUSD")),
        "timeframe": texto(primeiro(registro, ["timeframe", "intervalo"], "M5")),
        "direcao": direcao,
        "entrada": f"{entrada:.5f}" if entrada is not None else "",
        "stop": f"{stop:.5f}" if stop is not None else "",
        "alvo": f"{alvo:.5f}" if alvo is not None else "",
        "status": status_inicial,
        "resultado_r": "",
        "horario_resultado_utc": "",
        "motivo": motivo,
    }


def avaliar_operacao(registro, velas):
    if registro["status"] not in ("EM_ABERTO", ""):
        return registro

    if registro["status"] in (
        "SEM_DIRECAO",
        "SEM_ATR",
        "SEM_PRECO",
        "SEM_NIVEIS",
    ):
        return registro

    horario = data_utc(registro["horario_sinal_utc"])
    entrada = numero(registro["entrada"])
    stop = numero(registro["stop"])
    alvo = numero(registro["alvo"])
    direcao = registro["direcao"]

    if (
        horario is None
        or entrada is None
        or stop is None
        or alvo is None
        or direcao not in ("COMPRA", "VENDA")
    ):
        registro["status"] = "SEM_NIVEIS"
        registro["motivo"] = (
            registro["motivo"]
            + " | Dados insuficientes para avaliar a operação."
        ).strip(" |")
        return registro

    risco = abs(entrada - stop)

    if risco <= 0:
        registro["status"] = "SEM_NIVEIS"
        registro["motivo"] = (
            registro["motivo"] + " | Risco inválido."
        ).strip(" |")
        return registro

    posteriores = [
        v for v in velas
        if v["dt"] > horario
    ]

    for vela in posteriores:
        if direcao == "COMPRA":
            atingiu_stop = vela["low"] <= stop
            atingiu_alvo = vela["high"] >= alvo
        else:
            atingiu_stop = vela["high"] >= stop
            atingiu_alvo = vela["low"] <= alvo

        if atingiu_stop and atingiu_alvo:
            registro["status"] = "AMBIGUO"
            registro["resultado_r"] = ""
            registro["horario_resultado_utc"] = vela["dt"].isoformat()
            registro["motivo"] = (
                registro["motivo"]
                + " | Stop e alvo apareceram na mesma vela; "
                  "a sequência intravela não pode ser confirmada."
            ).strip(" |")
            return registro

        if atingiu_stop:
            registro["status"] = "STOP"
            registro["resultado_r"] = "-1.00"
            registro["horario_resultado_utc"] = vela["dt"].isoformat()
            return registro

        if atingiu_alvo:
            registro["status"] = "ALVO"
            registro["resultado_r"] = f"{abs(alvo - entrada) / risco:.2f}"
            registro["horario_resultado_utc"] = vela["dt"].isoformat()
            return registro

    registro["status"] = "EM_ABERTO"
    return registro


def salvar_resultados(resultados):
    ARQUIVO_RESULTADOS.parent.mkdir(parents=True, exist_ok=True)

    temporario = ARQUIVO_RESULTADOS.with_suffix(".csv.tmp")

    with temporario.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=CAMPOS,
            delimiter=";",
            extrasaction="ignore",
        )
        escritor.writeheader()

        for identificador in sorted(resultados):
            escritor.writerow(resultados[identificador])

    temporario.replace(ARQUIVO_RESULTADOS)


def main():
    print("=== ACOMPANHAMENTO XAUUSD ===")
    print(f"Memória: {ARQUIVO_MEMORIA}")
    print(f"Velas: {ARQUIVO_VELAS}")
    print(f"Resultados: {ARQUIVO_RESULTADOS}")

    memoria = carregar_memoria()
    velas = carregar_velas()
    resultados = carregar_resultados_existentes()

    print(f"Registros de memória: {len(memoria)}")
    print(f"Velas válidas: {len(velas)}")
    print(f"Resultados anteriores preservados: {len(resultados)}")

    novos = 0

    for item in memoria:
        candidato = montar_candidato(item, velas)

        if candidato is None:
            continue

        identificador = candidato["id_sinal"]

        if identificador not in resultados:
            resultados[identificador] = candidato
            novos += 1

    # Reavalia apenas registros ainda abertos.
    # Resultados terminais existentes não são sobrescritos.
    for identificador, registro in list(resultados.items()):
        if registro["status"] == "EM_ABERTO":
            resultados[identificador] = avaliar_operacao(
                registro, velas
            )

    salvar_resultados(resultados)

    contagem = {}
    por_tipo = {}

    for registro in resultados.values():
        status = registro["status"] or "SEM_STATUS"
        contagem[status] = contagem.get(status, 0) + 1

        tipo = registro["tipo_avaliacao"] or "NAO_CLASSIFICADA"
        por_tipo.setdefault(tipo, {})
        por_tipo[tipo][status] = (
            por_tipo[tipo].get(status, 0) + 1
        )

    print(f"Novos registros adicionados: {novos}")
    print(f"Total de registros: {len(resultados)}")
    print("Resumo por status:")

    for status, quantidade in sorted(contagem.items()):
        print(f"  {status}: {quantidade}")

    print("Resumo separado por tipo de avaliação:")

    for tipo, estados in sorted(por_tipo.items()):
        print(f"  {tipo}:")
        for status, quantidade in sorted(estados.items()):
            print(f"    {status}: {quantidade}")

    print(f"CSV atualizado: {ARQUIVO_RESULTADOS}")
    print("Concluído.")


if __name__ == "__main__":
    main()
