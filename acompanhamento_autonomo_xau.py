import os
import re
import csv
import json
import zipfile
import io
import copy
import requests

from pathlib import Path
from datetime import datetime, timezone, timedelta

from memoria_autonoma_xau import (
    criar_memoria,
    registrar_observacao,
    obter_central_inicial
)

from leitor_central_autonomo_xau import (
    carregar_contexto,
    analisar_preco
)

from diretor_operacional_xau import (
    calcular_decisao,
    formatar_mensagem
)


# =========================================================
# CONFIGURAÇÃO
# =========================================================

TOKEN = os.environ["TELEGRAM_ACOMPANHAMENTO_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]

TELEGRAM = f"https://api.telegram.org/bot{TOKEN}"
GITHUB = "https://api.github.com"

PASTA = Path("data")
PASTA.mkdir(exist_ok=True)

ARQUIVO_ESTADO = PASTA / "acompanhamento_autonomo_estado.json"
ARQUIVO_HISTORICO = PASTA / "resultados_operacoes.json"

NOME_ARTEFATO_VELAS = "candles-xauusd-m5"
NOME_WORKFLOW_INTEGRACAO = "integracao-xauusd.yml"

estado = {
    "ultimo_update_id": 0,
    "operacao": None
}

historico = []


# =========================================================
# PERSISTÊNCIA
# =========================================================

def carregar():
    global estado, historico

    if ARQUIVO_ESTADO.exists():
        try:
            dados = json.loads(
                ARQUIVO_ESTADO.read_text(encoding="utf-8")
            )

            if isinstance(dados, dict):
                estado.update(dados)

        except Exception as erro:
            print(f"Erro ao carregar estado: {erro}")

    if ARQUIVO_HISTORICO.exists():
        try:
            historico = json.loads(
                ARQUIVO_HISTORICO.read_text(encoding="utf-8")
            )

            if not isinstance(historico, list):
                historico = []

        except Exception as erro:
            print(f"Erro ao carregar histórico: {erro}")
            historico = []


def salvar():
    ARQUIVO_ESTADO.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    ARQUIVO_HISTORICO.write_text(
        json.dumps(historico, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================================================
# TELEGRAM
# =========================================================

def enviar(texto):
    resposta = requests.post(
        f"{TELEGRAM}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": texto
        },
        timeout=20
    )

    resposta.raise_for_status()

    dados = resposta.json()

    if not dados.get("ok"):
        raise RuntimeError(f"Erro Telegram: {dados}")


def buscar_atualizacoes():
    resposta = requests.get(
        f"{TELEGRAM}/getUpdates",
        params={
            "offset": int(estado["ultimo_update_id"]) + 1,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"])
        },
        timeout=20
    )

    resposta.raise_for_status()

    dados = resposta.json()

    if not dados.get("ok"):
        raise RuntimeError(f"Erro Telegram: {dados}")

    return dados.get("result", [])


def atualizar_offset(atualizacao):
    estado["ultimo_update_id"] = max(
        int(estado.get("ultimo_update_id", 0)),
        int(atualizacao["update_id"])
    )


# =========================================================
# ENCERRAMENTO E HISTÓRICO
# =========================================================

def fechar_operacao(status, mensagem):
    operacao = estado.get("operacao")

    if not operacao:
        enviar("Não existe operação ativa.")
        return

    operacao["status"] = status
    operacao["encerrada_em"] = datetime.now(
        timezone.utc
    ).isoformat()

    historico.append(copy.deepcopy(operacao))

    estado["operacao"] = None

    salvar()
    enviar(mensagem)


# =========================================================
# GITHUB
# =========================================================

def github_get(url):
    resposta = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        },
        timeout=40
    )

    resposta.raise_for_status()
    return resposta


# =========================================================
# LOCALIZAR O ÚLTIMO SINAL REALMENTE ENVIADO
# =========================================================

def ultimo_sinal():
    url = (
        f"{GITHUB}/repos/{REPO}/actions/workflows/"
        "agente-gemini-xau.yml/runs"
    )

    dados = github_get(
        url + "?status=success&per_page=20"
    ).json()

    execucoes = dados.get("workflow_runs", [])

    if not execucoes:
        raise RuntimeError(
            "Não encontrei execução bem-sucedida do Agente Gemini."
        )

    for execucao in execucoes:
        run_id = execucao["id"]

        artefatos = github_get(
            f"{GITHUB}/repos/{REPO}/actions/runs/"
            f"{run_id}/artifacts"
        ).json().get("artifacts", [])

        artefato = next(
            (
                item for item in artefatos
                if item["name"] == "resultado-agente-gemini"
                and not item.get("expired", False)
            ),
            None
        )

        if not artefato:
            continue

        conteudo = github_get(
            f"{GITHUB}/repos/{REPO}/actions/artifacts/"
            f"{artefato['id']}/zip"
        ).content

        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
            caminho = next(
                (
                    nome for nome in arquivo.namelist()
                    if nome.endswith("decisao_gemini.json")
                ),
                None
            )

            if not caminho:
                continue

            diagnostico = json.loads(
                arquivo.read(caminho).decode("utf-8")
            )

        direcao = str(
            diagnostico.get("decisao_gemini", "")
        ).upper()

        enviado = diagnostico.get("envio_telegram") is True

        if direcao not in ("COMPRA", "VENDA"):
            continue

        if not enviado:
            continue

        return diagnostico

    raise RuntimeError(
        "Não encontrei sinal COMPRA ou VENDA realmente enviado "
        "pelo Agente Gemini nas últimas 20 execuções."
    )


def campo(dados, nomes):
    for nome in nomes:
        if nome in dados and dados[nome] is not None:
            return dados[nome]

    return None


def preparar_sinal(dados):
    direcao = campo(
        dados,
        ["decisao_gemini", "direcao", "decisao", "sinal"]
    )

    entrada = campo(
        dados,
        ["entrada", "preco_entrada", "entry"]
    )

    stop = campo(
        dados,
        ["stop_loss", "stop", "sl"]
    )

    alvo = campo(
        dados,
        ["take_profit", "alvo", "target", "tp"]
    )

    if not direcao or stop is None or alvo is None:
        raise RuntimeError(
            "Sinal sem direção, stop ou alvo."
        )

    direcao = str(direcao).upper()

    if "COMPRA" in direcao or "BUY" in direcao:
        direcao = "COMPRA"

    elif "VENDA" in direcao or "SELL" in direcao:
        direcao = "VENDA"

    else:
        raise RuntimeError(
            f"Direção não reconhecida: {direcao}"
        )

    return {
        "direcao": direcao,
        "entrada_sinal": (
            float(entrada) if entrada is not None else None
        ),
        "stop": float(stop),
        "alvo": float(alvo)
    }


# =========================================================
# LOCALIZAR VELAS VALIDADAS NO GITHUB ACTIONS
# =========================================================

def localizar_execucao_integracao():
    url = (
        f"{GITHUB}/repos/{REPO}/actions/workflows/"
        f"{NOME_WORKFLOW_INTEGRACAO}/runs"
    )

    dados = github_get(
        url + "?status=success&per_page=20"
    ).json()

    execucoes = dados.get("workflow_runs", [])

    for execucao in execucoes:
        if execucao.get("conclusion") == "success":
            return execucao["id"]

    raise RuntimeError(
        "Não encontrei execução bem-sucedida da integração XAUUSD."
    )


def baixar_artefato_velas(run_id):
    url = (
        f"{GITHUB}/repos/{REPO}/actions/runs/"
        f"{run_id}/artifacts"
    )

    artefatos = github_get(url).json().get("artifacts", [])

    artefato = next(
        (
            item for item in artefatos
            if item.get("name") == NOME_ARTEFATO_VELAS
            and not item.get("expired", False)
        ),
        None
    )

    if not artefato:
        raise RuntimeError(
            "O artefato de velas validadas não foi encontrado "
            "na última integração."
        )

    resposta = github_get(
        f"{GITHUB}/repos/{REPO}/actions/artifacts/"
        f"{artefato['id']}/zip"
    )

    return resposta.content


# =========================================================
# LER CSV DE VELAS FECHADAS
# =========================================================

def interpretar_horario(valor):
    instante = datetime.fromisoformat(
        str(valor).strip().replace("Z", "+00:00")
    )

    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)

    return instante.astimezone(timezone.utc)


def buscar_velas():
    print("Buscando banco de velas validadas no GitHub Actions.")

    run_id = localizar_execucao_integracao()

    conteudo = baixar_artefato_velas(run_id)

    agora = datetime.now(timezone.utc)
    velas = []

    with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
        caminhos = [
            nome for nome in arquivo.namelist()
            if nome.endswith("xauusd_m5_fechados.csv")
        ]

        if not caminhos:
            raise RuntimeError(
                "O artefato não contém xauusd_m5_fechados.csv."
            )

        with arquivo.open(caminhos[0]) as bruto:
            texto = io.TextIOWrapper(
                bruto,
                encoding="utf-8-sig",
                newline=""
            )

            leitor = csv.DictReader(texto)

            if not leitor.fieldnames:
                raise RuntimeError(
                    "CSV de velas sem cabeçalho."
                )

            for item in leitor:
                horario_texto = (
                    item.get("timestamp_utc")
                    or item.get("timestamp")
                )

                if not horario_texto:
                    continue

                try:
                    horario = interpretar_horario(horario_texto)

                    abertura = float(item["open"])
                    maxima = float(item["high"])
                    minima = float(item["low"])
                    fechamento = float(item["close"])

                    precos = [
                        abertura,
                        maxima,
                        minima,
                        fechamento
                    ]

                    if not all(
                        valor > 0 and valor < float("inf")
                        for valor in precos
                    ):
                        continue

                    if maxima < max(abertura, fechamento, minima):
                        continue

                    if minima > min(abertura, fechamento, maxima):
                        continue

                    # Confirma que a vela M5 já fechou.
                    if horario + timedelta(minutes=5) > agora:
                        continue

                    velas.append({
                        "horario": horario,
                        "open": abertura,
                        "high": maxima,
                        "low": minima,
                        "close": fechamento
                    })

                except (ValueError, TypeError, KeyError):
                    continue

    velas.sort(key=lambda item: item["horario"])

    # Remove duplicidades de horário.
    unicas = {}

    for vela in velas:
        unicas[vela["horario"]] = vela

    velas = sorted(
        unicas.values(),
        key=lambda item: item["horario"]
    )

    if not velas:
        raise RuntimeError(
            "O banco de velas não possui candles M5 fechados válidos."
        )

    print(f"Velas validadas encontradas: {len(velas)}")
    print(
        "Última vela fechada:",
        velas[-1]["horario"].isoformat()
    )

    # Mantém até 5000 candles para os cálculos.
    return velas[-5000:]


# =========================================================
# INDICADORES
# =========================================================

def ema(valores, periodo):
    if len(valores) < periodo:
        return None

    fator = 2 / (periodo + 1)
    resultado = sum(valores[:periodo]) / periodo

    for valor in valores[periodo:]:
        resultado = (
            valor * fator
            + resultado * (1 - fator)
        )

    return resultado


def rsi(valores, periodo=14):
    if len(valores) <= periodo:
        return None

    ganhos = []
    perdas = []

    for i in range(1, len(valores)):
        variacao = valores[i] - valores[i - 1]
        ganhos.append(max(variacao, 0))
        perdas.append(max(-variacao, 0))

    media_ganho = sum(ganhos[:periodo]) / periodo
    media_perda = sum(perdas[:periodo]) / periodo

    for i in range(periodo, len(ganhos)):
        media_ganho = (
            media_ganho * (periodo - 1)
            + ganhos[i]
        ) / periodo

        media_perda = (
            media_perda * (periodo - 1)
            + perdas[i]
        ) / periodo

    if media_perda == 0:
        return 100.0

    if media_ganho == 0:
        return 0.0

    forca = media_ganho / media_perda

    return 100 - (100 / (1 + forca))


def calcular_atr(velas, periodo=14):
    if len(velas) < periodo + 1:
        return None

    ranges = []

    for i in range(1, len(velas)):
        atual = velas[i]
        anterior = velas[i - 1]

        tr = max(
            atual["high"] - atual["low"],
            abs(atual["high"] - anterior["close"]),
            abs(atual["low"] - anterior["close"])
        )

        ranges.append(tr)

    janela = ranges[-periodo:]

    if not janela:
        return None

    return sum(janela) / len(janela)


# =========================================================
# ATIVAR OPERAÇÃO
# =========================================================

def ativar_operacao(preco):
    diagnostico = ultimo_sinal()
    sinal = preparar_sinal(diagnostico)

    central_inicial = diagnostico.get("contexto_central")

    memoria = criar_memoria(
        diagnostico_original=diagnostico,
        contexto_central=central_inicial
    )

    operacao = {
        **sinal,
        "entrada_real": float(preco),
        "stop_original": float(sinal["stop"]),
        "stop_confirmado": float(sinal["stop"]),
        "stop_pendente": None,
        "diagnostico_original": copy.deepcopy(diagnostico),
        "memoria_autonoma": memoria,
        "ativada_em": datetime.now(timezone.utc).isoformat(),
        "ultima_vela": None,
        "alertas": [],
        "sequencia_reversao": 0,
        "status": "ATIVA"
    }

    estado["operacao"] = operacao

    salvar()

    enviar(
        "ROBÔ DIRETOR ATIVADO\n\n"
        f"Direção: {operacao['direcao']}\n"
        f"Sua entrada: {preco:.3f}\n"
        f"Entrada do sinal: {operacao['entrada_sinal']}\n"
        f"Stop inicial: {operacao['stop']:.3f}\n"
        f"Alvo: {operacao['alvo']:.3f}\n\n"
        "Último sinal válido do Gemini localizado.\n"
        "Diagnóstico original armazenado.\n"
        "Contexto inicial da Central armazenado.\n\n"
        "O acompanhamento começa na próxima execução M5.\n"
        "As velas serão consultadas no banco já validado.\n"
        "Nenhuma ordem será executada pelo robô."
    )


# =========================================================
# COMANDOS TELEGRAM
# =========================================================

def extrair_preco_entrada(texto):
    padrao = (
        r"^ENTREI\s*(?:\+|:|\s)\s*"
        r"(\d+(?:[.,]\d+)?)$"
    )

    resultado = re.fullmatch(
        padrao,
        texto.strip(),
        flags=re.IGNORECASE
    )

    if not resultado:
        return None

    return float(
        resultado.group(1).replace(",", ".")
    )


def processar_mensagem(mensagem):
    if str(
        mensagem.get("chat", {}).get("id")
    ) != CHAT_ID:
        return False

    texto = (
        mensagem.get("text") or ""
    ).strip()

    upper = texto.upper()

    operacao = estado.get("operacao")

    # -----------------------------------------------------
    # DESLIGA — RESET IMEDIATO DO ACOMPANHANTE
    # -----------------------------------------------------

    if upper == "DESLIGA":
        ultimo_id = int(
            estado.get("ultimo_update_id", 0)
        )

        # Restaura o estado operacional inicial.
        # Preserva o controle da fila do Telegram.
        estado.clear()

        estado.update({
            "ultimo_update_id": ultimo_id,
            "operacao": None
        })

        # Salva o estado inicial antes da confirmação.
        salvar()

        enviar(
            "ACOMPANHAMENTO DESLIGADO.\n\n"
            "Estado operacional restaurado ao inicial.\n"
            "Nenhuma operação será acompanhada.\n"
            "Histórico anterior preservado.\n\n"
            "O XP voltou a aguardar novas instruções."
        )

        return False

    # -----------------------------------------------------
    # CANCELA
    # -----------------------------------------------------

    if upper in ("CANCELA", "CANCELAR"):
        if operacao:
            fechar_operacao(
                "FECHADA MANUALMENTE NA CORRETORA",
                "ACOMPANHAMENTO ENCERRADO.\n\n"
                "Você informou que fechou a posição na corretora.\n"
                "Registro salvo como FECHADA MANUALMENTE.\n"
                "O robô voltou a aguardar um novo ENTREI + preço."
            )

        else:
            enviar(
                "Não existe operação ativa para cancelar."
            )

        return False

    # -----------------------------------------------------
    # CONFIRMAÇÃO DE STOP
    # -----------------------------------------------------

    if upper == "STOP OK":
        if not operacao:
            enviar("Não existe operação ativa.")
            return False

        pendente = operacao.get("stop_pendente")

        if pendente is None:
            enviar(
                "Não existe novo stop aguardando confirmação."
            )
            return False

        operacao["stop"] = float(pendente)
        operacao["stop_confirmado"] = float(pendente)
        operacao["stop_pendente"] = None

        salvar()

        enviar(
            "NOVO STOP CONFIRMADO.\n\n"
            f"Stop atualizado: {operacao['stop']:.3f}\n"
            "O robô passará a monitorar esse nível."
        )

        return False

    if upper == "STOP NÃO":
        if not operacao:
            enviar("Não existe operação ativa.")
            return False

        operacao["stop_pendente"] = None

        salvar()

        enviar(
            "ALTERAÇÃO DE STOP RECUSADA.\n"
            f"Continuarei monitorando: {operacao['stop']:.3f}"
        )

        return False

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if upper == "STATUS":
        if not operacao:
            enviar(
                "ROBÔ DIRETOR: AGUARDANDO ENTREI + PREÇO."
            )
            return False

        enviar(
            "STATUS DA OPERAÇÃO\n\n"
            f"Direção: {operacao['direcao']}\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Stop monitorado: {operacao['stop']:.3f}\n"
            f"Alvo: {operacao['alvo']:.3f}\n"
            f"Estado: {operacao['status']}\n"
            f"Stop pendente: {operacao.get('stop_pendente')}"
        )

        return False

    # -----------------------------------------------------
    # NOVA ENTRADA
    # -----------------------------------------------------

    preco = extrair_preco_entrada(texto)

    if preco is not None:
        if operacao:
            enviar(
                "Já existe uma operação em acompanhamento.\n"
                "Envie CANCELA somente depois de fechar "
                "a posição na corretora."
            )
            return False

        try:
            ativar_operacao(preco)
            return True

        except Exception as erro:
            enviar(
                "NÃO FOI POSSÍVEL ATIVAR O ACOMPANHAMENTO.\n\n"
                f"Motivo: {erro}\n\n"
                "Nenhuma operação foi iniciada."
            )

            return False

    if upper.startswith("ENTREI"):
        enviar(
            "FORMATO DE ENTRADA NÃO RECONHECIDO.\n\n"
            "Envie tudo em uma mensagem:\n"
            "ENTREI 4095.212"
        )

    return False


# =========================================================
# CONTEXTO CENTRAL
# =========================================================

def obter_contexto_central(operacao):
    contexto = carregar_contexto()

    if contexto.get("disponivel"):
        return contexto

    memoria = operacao.get("memoria_autonoma") or {}
    inicial = obter_central_inicial(memoria)

    if not isinstance(inicial, dict):
        return contexto

    mercado = inicial.get("mercado_atual")

    if not isinstance(mercado, dict):
        return contexto

    atualizado = inicial.get("atualizado_em")
    idade = None

    if atualizado:
        try:
            data = datetime.fromisoformat(
                str(atualizado).replace("Z", "+00:00")
            )

            if data.tzinfo is None:
                data = data.replace(tzinfo=timezone.utc)

            idade = max(
                0.0,
                (
                    datetime.now(timezone.utc)
                    - data.astimezone(timezone.utc)
                ).total_seconds() / 60
            )

        except (ValueError, TypeError):
            idade = None

    return {
        "disponivel": True,
        "dados": inicial,
        "mercado_atual": mercado,
        "atualizado_em": atualizado,
        "idade_minutos": (
            round(idade, 1) if idade is not None else None
        )
    }


# =========================================================
# ACOMPANHAMENTO AUTÔNOMO POR VELA M5
# =========================================================

def conduzir(velas):
    operacao = estado.get("operacao")

    if (
        not operacao
        or operacao.get("status") != "ATIVA"
    ):
        return

    if len(velas) < 30:
        print("Histórico insuficiente para análise M5.")
        return

    vela = velas[-1]
    horario = vela["horario"].isoformat()

    if operacao.get("ultima_vela") == horario:
        print("Esta vela M5 já foi analisada.")
        return

    direcao = operacao["direcao"]

    stop = float(operacao["stop"])
    alvo = float(operacao["alvo"])

    # -----------------------------------------------------
    # VERIFICAÇÃO DE STOP E ALVO
    # -----------------------------------------------------

    if direcao == "COMPRA":
        tocou_stop = vela["low"] <= stop
        tocou_alvo = vela["high"] >= alvo

    else:
        tocou_stop = vela["high"] >= stop
        tocou_alvo = vela["low"] <= alvo

    if tocou_stop and tocou_alvo:
        operacao["ultima_vela"] = horario

        fechar_operacao(
            "AMBIGUO",
            "ATENÇÃO: A mesma vela M5 tocou stop e alvo.\n"
            "Não é possível determinar qual nível ocorreu primeiro."
        )

        return

    if tocou_stop:
        operacao["ultima_vela"] = horario

        fechar_operacao(
            "STOP",
            "STOP DE REFERÊNCIA ATINGIDO.\n\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Stop monitorado: {stop:.3f}"
        )

        return

    if tocou_alvo:
        operacao["ultima_vela"] = horario

        fechar_operacao(
            "ALVO",
            "ALVO ATINGIDO.\n\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Alvo: {alvo:.3f}"
        )

        return

    # -----------------------------------------------------
    # INDICADORES
    # -----------------------------------------------------

    fechamentos = [
        item["close"] for item in velas
    ]

    ema9 = ema(fechamentos, 9)
    ema21 = ema(fechamentos, 21)
    valor_rsi = rsi(fechamentos, 14)
    atr = calcular_atr(velas, 14)

    if atr is None:
        print("ATR indisponível nesta vela.")
        return

    # -----------------------------------------------------
    # CENTRAL INTELIGENTE
    # -----------------------------------------------------

    contexto = obter_contexto_central(operacao)

    leitura = analisar_preco(
        vela["close"],
        contexto
    )

    # -----------------------------------------------------
    # DECISÃO DO DIRETOR
    # -----------------------------------------------------

    decisao = calcular_decisao(
        operacao=operacao,
        vela={
            "close": vela["close"],
            "high": vela["high"],
            "low": vela["low"]
        },
        ema9=ema9,
        ema21=ema21,
        valor_rsi=valor_rsi,
        atr=atr,
        leitura_central=leitura
    )

    observacao = {
        "timestamp": horario,
        "preco": vela["close"],
        "maxima": vela["high"],
        "minima": vela["low"],
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": valor_rsi,
        "atr14": atr,
        "leitura_central": leitura,
        "decisao": decisao
    }

    memoria = operacao.get("memoria_autonoma")

    if memoria:
        memoria, registrada = registrar_observacao(
            memoria,
            observacao
        )

        operacao["memoria_autonoma"] = memoria

    else:
        registrada = False

    if not registrada:
        salvar()
        return

    operacao["ultima_vela"] = horario

    # -----------------------------------------------------
    # PROTEÇÃO DO STOP
    # -----------------------------------------------------

    acao = decisao["acao"]
    novo_stop = round(float(decisao["novo_stop"]), 3)

    if acao in ("MUDA_STOP", "REDUZ_RISCO"):
        stop_atual = float(operacao["stop"])
        pendente = operacao.get("stop_pendente")

        referencia = stop_atual

        if pendente is not None:
            if direcao == "COMPRA":
                referencia = max(
                    stop_atual,
                    float(pendente)
                )
            else:
                referencia = min(
                    stop_atual,
                    float(pendente)
                )

        if direcao == "COMPRA":
            melhora = novo_stop > referencia
        else:
            melhora = novo_stop < referencia

        if melhora:
            operacao["stop_pendente"] = novo_stop

            enviar(
                formatar_mensagem(
                    decisao,
                    operacao
                )
            )

    elif acao == "FECHA":
        enviar(
            formatar_mensagem(
                decisao,
                operacao
            )
        )

    else:
        enviar(
            formatar_mensagem(
                decisao,
                operacao
            )
        )

    salvar()


# =========================================================
# EXECUÇÃO PRINCIPAL — FILA TELEGRAM + DESLIGA
# =========================================================

def main():
    carregar()

    try:
        # 1. CONSULTAR TELEGRAM
        atualizacoes = buscar_atualizacoes()

        if not atualizacoes:
            print("Nenhuma mensagem nova no Telegram.")

        # 2. LOCALIZAR COMANDOS
        comandos_validos = []
        comandos_desliga = []

        for atualizacao in atualizacoes:
            mensagem = atualizacao.get("message")

            if not mensagem:
                continue

            if str(
                mensagem.get("chat", {}).get("id")
            ) != CHAT_ID:
                continue

            texto = (
                mensagem.get("text") or ""
            ).strip()

            upper = texto.upper()

            comando_reconhecido = (
                upper in (
                    "DESLIGA",
                    "CANCELA",
                    "CANCELAR",
                    "STOP OK",
                    "STOP NÃO",
                    "STATUS"
                )
                or extrair_preco_entrada(texto) is not None
            )

            if comando_reconhecido:
                comandos_validos.append(mensagem)

                if upper == "DESLIGA":
                    comandos_desliga.append(mensagem)

        # 3. CONSUMIR TODA A FILA RECEBIDA
        if atualizacoes:
            maior_id = max(
                int(item["update_id"])
                for item in atualizacoes
            )

            estado["ultimo_update_id"] = max(
                int(estado.get("ultimo_update_id", 0)),
                maior_id
            )

            salvar()

        # 4. DESLIGA TEM PRIORIDADE ABSOLUTA
        if comandos_desliga:
            print("Comando DESLIGA identificado.")

            processar_mensagem(
                comandos_desliga[-1]
            )

            salvar()
            return

        # 5. PROCESSAR SOMENTE O COMANDO VÁLIDO MAIS RECENTE
        if comandos_validos:
            mensagem = comandos_validos[-1]

            print(
                "Processando somente o comando válido "
                "mais recente do Telegram."
            )

            processar_mensagem(mensagem)

            salvar()
            return

        # 6. SEM OPERAÇÃO ATIVA: AGUARDAR
        if not estado.get("operacao"):
            print(
                "Nenhuma operação ativa. "
                "Execução encerrada sem consultar velas."
            )
            return

        # 7. OPERAÇÃO ATIVA: ACOMPANHAR
        print(
            "Operação ativa. Consultando banco "
            "de velas já validadas."
        )

        velas = buscar_velas()

        conduzir(velas)

    finally:
        salvar()


if __name__ == "__main__":
    main()
