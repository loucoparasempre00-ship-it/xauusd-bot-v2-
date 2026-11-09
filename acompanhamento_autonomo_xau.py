import os
import re
import csv
import json
import zipfile
import io
import copy
import uuid
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

from diretor_operacional_xau import calcular_decisao

from controle_sinais_xau import (
    ultimo_sinal,
    sinal_por_id,
    campo,
    preparar_sinal
)

from formatador_mensagens_xau import formatar_mensagem


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
    "operacao": None,
    "operacoes": []
}

historico = []


# =========================================================
# IDENTIFICAÇÃO DAS OPERAÇÕES
# =========================================================

def gerar_id():
    return uuid.uuid4().hex[:8].upper()


def obter_operacoes():
    operacoes = estado.get("operacoes")

    if isinstance(operacoes, list):
        return operacoes

    antiga = estado.get("operacao")

    if isinstance(antiga, dict):
        antiga = copy.deepcopy(antiga)
        antiga.setdefault("id", gerar_id())
        antiga.setdefault("status", "ATIVA")

        estado["operacoes"] = [antiga]
        estado["operacao"] = None

        return estado["operacoes"]

    estado["operacoes"] = []
    estado["operacao"] = None

    return estado["operacoes"]


def buscar_operacao(identificador=None):
    operacoes = obter_operacoes()

    if identificador:
        identificador = str(identificador).upper()

        for operacao in operacoes:
            if operacao.get("id", "").upper() == identificador:
                return operacao

        return None

    ativas = [
        op for op in operacoes
        if op.get("status") == "ATIVA"
    ]

    if len(ativas) == 1:
        return ativas[0]

    return None


def atualizar_estado_operacoes():
    operacoes = obter_operacoes()

    estado["operacoes"] = operacoes

    # Compatibilidade com leitores antigos do estado.
    ativas = [
        op for op in operacoes
        if op.get("status") == "ATIVA"
    ]

    estado["operacao"] = (
        copy.deepcopy(ativas[0])
        if len(ativas) == 1
        else None
    )


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

    if not isinstance(estado.get("operacoes"), list):
        estado["operacoes"] = []

    # Migração do formato antigo para múltiplas operações.
    if not estado["operacoes"] and isinstance(
        estado.get("operacao"), dict
    ):
        antiga = copy.deepcopy(estado["operacao"])
        antiga.setdefault("id", gerar_id())
        antiga.setdefault("status", "ATIVA")
        estado["operacoes"].append(antiga)

    for operacao in estado["operacoes"]:
        operacao.setdefault("id", gerar_id())
        operacao.setdefault("status", "ATIVA")

    atualizar_estado_operacoes()

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
    atualizar_estado_operacoes()

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


# =========================================================
# IDENTIFICAÇÃO DE COMANDOS
# =========================================================

def extrair_entrada(texto):
    resultado = re.fullmatch(
        r"^ENTREI\s+(\d+)\s+(\d+(?:[.,]\d+)?)$",
        texto.strip(),
        flags=re.IGNORECASE
    )

    if not resultado:
        return None

    sinal_id = resultado.group(1)

    preco = float(
        resultado.group(2).replace(",", ".")
    )

    return sinal_id, preco


def extrair_comando_identificado(texto, comando):
    resultado = re.fullmatch(
        rf"{re.escape(comando)}(?:\s+([A-Z0-9]+))?",
        texto.strip(),
        flags=re.IGNORECASE
    )

    if not resultado:
        return None

    return resultado.group(1)


# =========================================================
# ENCERRAMENTO E HISTÓRICO
# =========================================================

def fechar_operacao(status, mensagem, operacao=None):
    if operacao is None:
        operacao = buscar_operacao()

    if not operacao:
        enviar(
            "Não foi possível identificar uma operação.\n"
            "Informe o identificador da operação."
        )
        return

    identificador = operacao["id"]

    operacao["status"] = status
    operacao["encerrada_em"] = datetime.now(
        timezone.utc
    ).isoformat()

    historico.append(copy.deepcopy(operacao))

    estado["operacoes"] = [
        item for item in obter_operacoes()
        if item.get("id") != identificador
    ]

    salvar()

    enviar(
        f"OPERAÇÃO {identificador} ENCERRADA.\n\n"
        f"{mensagem}"
    )


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
# LOCALIZAR VELAS VALIDADAS
# =========================================================

def localizar_execucao_integracao():
    url = (
        f"{GITHUB}/repos/{REPO}/actions/workflows/"
        f"{NOME_WORKFLOW_INTEGRACAO}/runs"
    )

    dados = github_get(
        url + "?status=success&per_page=20"
    ).json()

    for execucao in dados.get("workflow_runs", []):
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
            "Artefato de velas validadas não encontrado."
        )

    resposta = github_get(
        f"{GITHUB}/repos/{REPO}/actions/artifacts/"
        f"{artefato['id']}/zip"
    )

    return resposta.content


def interpretar_horario(valor):
    instante = datetime.fromisoformat(
        str(valor).strip().replace("Z", "+00:00")
    )

    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)

    return instante.astimezone(timezone.utc)


def buscar_velas():
    print("Buscando banco de velas validadas.")

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
                "Artefato sem xauusd_m5_fechados.csv."
            )

        with arquivo.open(caminhos[0]) as bruto:
            texto = io.TextIOWrapper(
                bruto,
                encoding="utf-8-sig",
                newline=""
            )

            leitor = csv.DictReader(texto)

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

    unicas = {
        vela["horario"]: vela
        for vela in velas
    }

    velas = sorted(
        unicas.values(),
        key=lambda item: item["horario"]
    )

    if not velas:
        raise RuntimeError(
            "Não existem candles M5 fechados válidos."
        )

    print(f"Velas válidas: {len(velas)}")
    print("Última vela:", velas[-1]["horario"].isoformat())

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

    return sum(ranges[-periodo:]) / periodo


# =========================================================
# ATIVAR NOVA OPERAÇÃO PELO ID EXATO DO SINAL
# =========================================================

def ativar_operacao(sinal_id, preco):
    diagnostico = sinal_por_id(
        github_get,
        GITHUB,
        REPO,
        sinal_id
    )

    sinal = preparar_sinal(diagnostico)

    run_id = diagnostico.get("_run_id")

    if str(run_id) != str(sinal_id):
        raise RuntimeError(
            "O ID recuperado não corresponde ao sinal informado."
        )

    # Impede cadastrar duas vezes o mesmo sinal.
    for existente in obter_operacoes():
        if str(existente.get("sinal_run_id")) == str(run_id):
            enviar(
                "Este sinal já está sendo acompanhado.\n"
                f"Operação: {existente['id']}"
            )
            return

    memoria = criar_memoria(
        diagnostico_original=diagnostico,
        contexto_central=diagnostico.get("contexto_central")
    )

    operacao = {
        "id": gerar_id(),
        **sinal,
        "entrada_real": float(preco),
        "stop_original": float(sinal["stop"]),
        "stop_confirmado": float(sinal["stop"]),
        "stop_pendente": None,
        "diagnostico_original": copy.deepcopy(diagnostico),
        "sinal_run_id": run_id,
        "memoria_autonoma": memoria,
        "ativada_em": datetime.now(timezone.utc).isoformat(),
        "ultima_vela": None,
        "alertas": [],
        "sequencia_reversao": 0,
        "status": "ATIVA"
    }

    obter_operacoes().append(operacao)

    salvar()

    enviar(
        "NOVA OPERAÇÃO REGISTRADA\n\n"
        f"ID da operação: {operacao['id']}\n"
        f"ID do sinal: {run_id}\n"
        f"Direção: {operacao['direcao']}\n"
        f"Sua entrada: {preco:.3f}\n"
        f"Entrada do sinal: {operacao['entrada_sinal']}\n"
        f"Stop inicial: {operacao['stop']:.3f}\n"
        f"Alvo: {operacao['alvo']:.3f}\n\n"
        f"Operações ativas: {len(obter_operacoes())}\n"
        "A operação anterior, se existente, foi preservada.\n"
        "Nenhuma ordem será executada pelo robô."
    )


# =========================================================
# COMANDOS TELEGRAM
# =========================================================

def processar_mensagem(mensagem):
    if str(
        mensagem.get("chat", {}).get("id")
    ) != CHAT_ID:
        return

    texto = (
        mensagem.get("text") or ""
    ).strip()

    upper = texto.upper()

    # DESLIGA tem prioridade absoluta.
    if upper == "DESLIGA":
        ultimo_id = int(
            estado.get("ultimo_update_id", 0)
        )

        estado.clear()

        estado.update({
            "ultimo_update_id": ultimo_id,
            "operacao": None,
            "operacoes": []
        })

        salvar()

        enviar(
            "ACOMPANHAMENTO DESLIGADO.\n\n"
            "Todas as operações foram retiradas do acompanhamento.\n"
            "Histórico anterior preservado.\n"
            "O sistema voltou ao estado inicial."
        )

        return

    # CANCELA ou CANCELA ID
    identificador = extrair_comando_identificado(
        texto, "CANCELA"
    )

    if identificador is None:
        identificador = extrair_comando_identificado(
            texto, "CANCELAR"
        )

    if texto.upper() in ("CANCELA", "CANCELAR") or identificador:
        operacao = buscar_operacao(identificador)

        if not operacao:
            enviar(
                "Não foi possível identificar a operação.\n"
                "Use CANCELA ID quando houver mais de uma.\n"
                "Exemplo: CANCELA A1B2C3D4"
            )
            return

        fechar_operacao(
            "FECHADA MANUALMENTE NA CORRETORA",
            "Você informou que fechou a posição na corretora.\n"
            "Registro salvo como fechamento manual.",
            operacao
        )
        return

    # STOP OK ID
    identificador = extrair_comando_identificado(
        texto, "STOP OK"
    )

    if texto.upper() == "STOP OK" or identificador:
        if texto.upper().startswith("STOP OK"):
            operacao = buscar_operacao(identificador)

            if not operacao:
                enviar(
                    "Informe o ID do stop quando houver "
                    "mais de uma operação."
                )
                return

            pendente = operacao.get("stop_pendente")

            if pendente is None:
                enviar(
                    f"Operação {operacao['id']}: "
                    "não existe stop aguardando confirmação."
                )
                return

            operacao["stop"] = float(pendente)
            operacao["stop_confirmado"] = float(pendente)
            operacao["stop_pendente"] = None

            salvar()

            enviar(
                f"STOP CONFIRMADO — {operacao['id']}\n"
                f"Novo stop: {operacao['stop']:.3f}"
            )
            return

    # STOP NÃO ID
    if upper.startswith("STOP NÃO") or upper.startswith("STOP NAO"):
        partes = texto.split()
        identificador = partes[2] if len(partes) > 2 else None

        operacao = buscar_operacao(identificador)

        if not operacao:
            enviar(
                "Informe o ID da operação para recusar o stop."
            )
            return

        operacao["stop_pendente"] = None
        salvar()

        enviar(
            f"STOP RECUSADO — {operacao['id']}\n"
            f"Stop mantido: {operacao['stop']:.3f}"
        )
        return

    # STATUS
    if upper == "STATUS":
        operacoes = [
            op for op in obter_operacoes()
            if op.get("status") == "ATIVA"
        ]

        if not operacoes:
            enviar("Nenhuma operação ativa.")
            return

        linhas = [
            f"OPERAÇÕES ATIVAS: {len(operacoes)}"
        ]

        for op in operacoes:
            linhas.extend([
                "",
                f"ID: {op['id']}",
                f"Direção: {op['direcao']}",
                f"Entrada: {op['entrada_real']:.3f}",
                f"Stop: {op['stop']:.3f}",
                f"Alvo: {op['alvo']:.3f}",
                f"Estado: {op['status']}"
            ])

        enviar("\n".join(linhas))
        return

    # ENTREI ID_DO_SINAL PREÇO
    entrada = extrair_entrada(texto)

    if entrada is not None:
        sinal_id, preco = entrada

        try:
            ativar_operacao(sinal_id, preco)

        except Exception as erro:
            enviar(
                "NÃO FOI POSSÍVEL REGISTRAR A OPERAÇÃO.\n\n"
                f"ID do sinal: {sinal_id}\n"
                f"Motivo: {erro}\n"
                "Nenhuma ordem foi executada."
            )

        return

    if upper.startswith("ENTREI"):
        enviar(
            "FORMATO NÃO RECONHECIDO.\n\n"
            "Use: ENTREI ID_DO_SINAL PREÇO\n"
            "Exemplo: ENTREI 123456789 4095.212"
        )


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
# ACOMPANHAMENTO DE UMA OPERAÇÃO
# =========================================================

def conduzir_operacao(velas, operacao):
    if operacao.get("status") != "ATIVA":
        return

    if len(velas) < 30:
        print("Histórico insuficiente para análise M5.")
        return

    vela = velas[-1]
    horario = vela["horario"].isoformat()

    if operacao.get("ultima_vela") == horario:
        print(
            f"Operação {operacao['id']}: vela já analisada."
        )
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
        fechar_operacao(
            "AMBIGUO",
            "A mesma vela M5 tocou stop e alvo. "
            "Não é possível determinar a ordem dos eventos.",
            operacao
        )
        return

    if tocou_stop:
        fechar_operacao(
            "STOP",
            f"Stop de referência atingido: {stop:.3f}",
            operacao
        )
        return

    if tocou_alvo:
        fechar_operacao(
            "ALVO",
            f"Alvo atingido: {alvo:.3f}",
            operacao
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
        print("ATR indisponível.")
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
    # DIRETOR OPERACIONAL
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

    else:
        enviar(
            formatar_mensagem(
                decisao,
                operacao
            )
        )

    salvar()


# =========================================================
# EXECUÇÃO PRINCIPAL
# =========================================================

def main():
    carregar()

    try:
        # 1. UMA ÚNICA CONSULTA AO TELEGRAM
        atualizacoes = buscar_atualizacoes()

        print(
            f"Mensagens recebidas: {len(atualizacoes)}"
        )

        # 2. IDENTIFICAR MENSAGENS DO CHAT AUTORIZADO
        mensagens = []

        for atualizacao in atualizacoes:
            mensagem = atualizacao.get("message")

            if not mensagem:
                continue

            if str(
                mensagem.get("chat", {}).get("id")
            ) != CHAT_ID:
                continue

            mensagens.append(mensagem)

        # 3. ATUALIZAR OFFSET UMA ÚNICA VEZ
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

        # 4. DESLIGA TEM PRIORIDADE
        desligar = [
            mensagem for mensagem in mensagens
            if (mensagem.get("text") or "").strip().upper()
            == "DESLIGA"
        ]

        if desligar:
            processar_mensagem(desligar[-1])
            return

        # 5. PROCESSAR COMANDOS RECEBIDOS
        # Não interrompe a execução depois de um comando.
        for mensagem in mensagens:
            processar_mensagem(mensagem)

        salvar()

        # 6. IDENTIFICAR TODAS AS OPERAÇÕES ATIVAS
        operacoes_ativas = [
            op for op in obter_operacoes()
            if op.get("status") == "ATIVA"
        ]

        if not operacoes_ativas:
            print(
                "Nenhuma operação ativa. "
                "Central seguirá com as demais etapas."
            )
            return

        # 7. BUSCAR VELAS UMA ÚNICA VEZ
        velas = buscar_velas()

        # 8. ACOMPANHAR CADA OPERAÇÃO INDEPENDENTEMENTE
        for operacao in list(operacoes_ativas):
            identificador = operacao["id"]

            print(
                f"Acompanhando operação {identificador}."
            )

            try:
                conduzir_operacao(
                    velas,
                    operacao
                )

            except Exception as erro:
                print(
                    f"Erro na operação {identificador}: {erro}"
                )

                # Uma operação com erro não impede
                # o acompanhamento das demais.
                continue

        salvar()

        print(
            "Acompanhamento concluído. "
            f"Operações ainda ativas: "
            f"{len([op for op in obter_operacoes() if op.get('status') == 'ATIVA'])}"
        )

    finally:
        salvar()


if __name__ == "__main__":
    main()
