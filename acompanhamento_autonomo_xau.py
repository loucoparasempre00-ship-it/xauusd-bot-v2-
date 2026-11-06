
import os
import re
import json
import zipfile
import io
import time
import requests
import copy

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
API_KEY = os.environ["TWELVE_DATA_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]

TELEGRAM = f"https://api.telegram.org/bot{TOKEN}"
GITHUB = "https://api.github.com"

PASTA = Path("data")
PASTA.mkdir(exist_ok=True)

ARQUIVO_ESTADO = PASTA / "acompanhamento_autonomo_estado.json"
ARQUIVO_HISTORICO = PASTA / "resultados_operacoes.json"

estado = {
    "ultimo_update_id": 0,
    "aguardando_entrada": False,
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
            estado.update(
                json.loads(
                    ARQUIVO_ESTADO.read_text(
                        encoding="utf-8"
                    )
                )
            )
        except Exception as erro:
            print(f"Erro ao carregar estado: {erro}")

    if ARQUIVO_HISTORICO.exists():
        try:
            historico = json.loads(
                ARQUIVO_HISTORICO.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as erro:
            print(f"Erro ao carregar histórico: {erro}")
            historico = []


def salvar():
    ARQUIVO_ESTADO.write_text(
        json.dumps(
            estado,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    ARQUIVO_HISTORICO.write_text(
        json.dumps(
            historico,
            ensure_ascii=False,
            indent=2
        ),
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
        raise RuntimeError(
            f"Erro Telegram: {dados}"
        )


def buscar_atualizacoes(timeout_segundos=0):
    resposta = requests.get(
        f"{TELEGRAM}/getUpdates",
        params={
            "offset": int(estado["ultimo_update_id"]) + 1,
            "timeout": timeout_segundos,
            "allowed_updates": json.dumps(["message"])
        },
        timeout=max(20, timeout_segundos + 10)
    )

    resposta.raise_for_status()

    dados = resposta.json()

    if not dados.get("ok"):
        raise RuntimeError(
            f"Erro Telegram: {dados}"
        )

    return dados.get("result", [])


def atualizar_offset(atualizacao):
    estado["ultimo_update_id"] = max(
        int(estado.get("ultimo_update_id", 0)),
        int(atualizacao["update_id"])
    )


# =========================================================
# ENCERRAR OPERAÇÃO
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
# GITHUB E SINAL GEMINI
# =========================================================

def github_get(url):
    resposta = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        },
        timeout=30
    )

    resposta.raise_for_status()
    return resposta


def ultimo_sinal():
    url = (
        f"{GITHUB}/repos/{REPO}/actions/workflows/"
        "agente-gemini-xau.yml/runs"
    )

    dados = github_get(
        url + "?status=success&per_page=10"
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

        with zipfile.ZipFile(
            io.BytesIO(conteudo)
        ) as arquivo:
            caminho = next(
                (
                    nome for nome in arquivo.namelist()
                    if nome.endswith("decisao_gemini.json")
                ),
                None
            )

            if not caminho:
                continue

            return json.loads(
                arquivo.read(caminho).decode("utf-8")
            )

    raise RuntimeError(
        "Não encontrei decisao_gemini.json no artefato."
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
            float(entrada)
            if entrada is not None
            else None
        ),
        "stop": float(stop),
        "alvo": float(alvo)
    }


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
# BUSCAR VELAS M5 FECHADAS
# =========================================================

def buscar_velas():
    resposta = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD",
            "interval": "5min",
            "outputsize": 100,
            "apikey": API_KEY,
            "timezone": "UTC"
        },
        timeout=30
    )

    resposta.raise_for_status()
    dados = resposta.json()

    if "values" not in dados:
        raise RuntimeError(
            f"Erro Twelve Data: {dados}"
        )

    agora = datetime.now(timezone.utc)
    velas = []

    for item in dados["values"]:
        horario = datetime.strptime(
            item["datetime"],
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        if horario + timedelta(minutes=5) <= agora:
            velas.append({
                "horario": horario,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

    velas.sort(key=lambda x: x["horario"])
    return velas


# =========================================================
# ATIVAR OPERAÇÃO E MEMÓRIA ORIGINAL
# =========================================================

def ativar_operacao(preco):
    diagnostico = ultimo_sinal()
    sinal = preparar_sinal(diagnostico)

    central_inicial = diagnostico.get(
        "contexto_central"
    )

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
        "ativada_em": datetime.now(
            timezone.utc
        ).isoformat(),
        "ultima_vela": None,
        "alertas": [],
        "sequencia_reversao": 0,
        "status": "ATIVA"
    }

    estado["operacao"] = operacao
    estado["aguardando_entrada"] = False

    salvar()

    enviar(
        "ROBÔ DIRETOR ATIVADO\n\n"
        f"Direção: {operacao['direcao']}\n"
        f"Sua entrada: {preco:.3f}\n"
        f"Entrada do sinal: {operacao['entrada_sinal']}\n"
        f"Stop inicial: {operacao['stop']:.3f}\n"
        f"Alvo: {operacao['alvo']:.3f}\n\n"
        "Diagnóstico original do Gemini armazenado.\n"
        "Contexto inicial da Central armazenado.\n"
        "A partir de agora, a condução será autônoma "
        "por ciclos M5.\n\n"
        "Não envio nem executo ordens."
    )


# =========================================================
# AGUARDAR PREÇO DE ENTRADA
# =========================================================

def aguardar_preco_por_um_minuto():
    prazo = time.monotonic() + 60

    while time.monotonic() < prazo:
        restantes = max(
            1,
            int(prazo - time.monotonic())
        )

        atualizacoes = buscar_atualizacoes(
            timeout_segundos=min(5, restantes)
        )

        for atualizacao in atualizacoes:
            atualizar_offset(atualizacao)

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

            if upper == "CANCELAR":
                estado["aguardando_entrada"] = False
                salvar()

                enviar(
                    "Entrada cancelada. "
                    "Nenhuma operação foi ativada."
                )
                return False

            if upper == "ENTREI":
                continue

            if re.fullmatch(r"\d+(?:[.,]\d+)?", texto):
                preco = float(
                    texto.replace(",", ".")
                )

                estado["aguardando_entrada"] = False
                salvar()

                try:
                    ativar_operacao(preco)
                except Exception as erro:
                    enviar(
                        "Não consegui ativar o acompanhamento.\n"
                        f"Motivo: {erro}\n"
                        "Envie ENTREI para tentar novamente."
                    )

                return estado.get("operacao") is not None

            if texto:
                enviar(
                    "Ainda aguardo seu preço. "
                    "Exemplo: 4095.212"
                )

        salvar()

    estado["aguardando_entrada"] = False
    salvar()

    enviar(
        "TEMPO ESGOTADO.\n"
        "Não recebi seu preço em 1 minuto.\n"
        "Envie ENTREI para tentar novamente."
    )

    return False


# =========================================================
# COMANDOS DO USUÁRIO
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

    operacao = estado.get("operacao")

    if upper == "STOP OK":
        if not operacao:
            enviar("Não existe operação ativa.")
            return

        pendente = operacao.get("stop_pendente")

        if pendente is None:
            enviar("Não existe novo stop aguardando confirmação.")
            return

        operacao["stop"] = float(pendente)
        operacao["stop_confirmado"] = float(pendente)
        operacao["stop_pendente"] = None

        salvar()

        enviar(
            "NOVO STOP CONFIRMADO.\n\n"
            f"Stop de referência atualizado: "
            f"{operacao['stop']:.3f}\n"
            "O robô passará a monitorar esse nível."
        )
        return

    if upper == "STOP NÃO":
        if not operacao:
            enviar("Não existe operação ativa.")
            return

        operacao["stop_pendente"] = None
        salvar()

        enviar(
            "Alteração de stop recusada.\n"
            f"Continuarei monitorando o stop anterior: "
            f"{operacao['stop']:.3f}"
        )
        return

    if upper == "STATUS":
        if not operacao:
            enviar("Não existe operação ativa.")
            return

        enviar(
            "STATUS DA OPERAÇÃO\n\n"
            f"Direção: {operacao['direcao']}\n"
            f"Entrada: {operacao['entrada_real']:.3f}\n"
            f"Stop monitorado: {operacao['stop']:.3f}\n"
            f"Alvo: {operacao['alvo']:.3f}\n"
            f"Estado: {operacao['status']}\n"
            f"Stop pendente: {operacao.get('stop_pendente')}"
        )
        return

    if upper == "CANCELAR":
        if estado.get("aguardando_entrada"):
            estado["aguardando_entrada"] = False
            salvar()
            enviar("Solicitação de entrada cancelada.")

        elif operacao:
            fechar_operacao(
                "CANCELADA MANUALMENTE",
                "Acompanhamento cancelado manualmente."
            )

        else:
            enviar("Não há entrada pendente nem operação ativa.")

        return

    if upper in ("ENCERRAR", "FECHAR"):
        if operacao:
            fechar_operacao(
                "ENCERRADA MANUALMENTE",
                "Operação encerrada manualmente."
            )
        else:
            enviar("Não existe operação ativa.")

        return

    if upper == "ENTREI":
        if operacao:
            enviar(
                "Já existe uma operação em acompanhamento."
            )
            return

        if estado.get("aguardando_entrada"):
            enviar("Já estou aguardando seu preço.")
            return

        estado["aguardando_entrada"] = True
        salvar()

        enviar(
            "ENTRADA RECEBIDA.\n\n"
            "Qual foi seu preço real de entrada?\n"
            "Exemplo: 4095.212\n\n"
            "Você tem 1 minuto para responder.\n"
            "Envie CANCELAR se desistir."
        )

        aguardar_preco_por_um_minuto()


# =========================================================
# CONTEXTO CENTRAL: ATUAL OU REFERÊNCIA INICIAL
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
            round(idade, 1)
            if idade is not None
            else None
        )
    }


# =========================================================
# MEMÓRIA E DECISÃO A CADA VELA M5
# =========================================================

def conduzir(velas):
    operacao = estado.get("operacao")

    if (
        not operacao
        or operacao.get("status") != "ATIVA"
        or len(velas) < 30
    ):
        return

    vela = velas[-1]
    horario = vela["horario"].isoformat()

    if operacao.get("ultima_vela") == horario:
        return

    direcao = operacao["direcao"]

    stop = float(operacao["stop"])
    alvo = float(operacao["alvo"])

    # Primeiro, verificar os níveis efetivamente confirmados.
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
            "ATENÇÃO: A mesma vela M5 tocou stop e alvo. "
            "Não é possível determinar qual ocorreu primeiro."
        )
        return

    if tocou_stop:
        operacao["ultima_vela"] = horario
        fechar_operacao(
            "STOP",
            "STOP DE REFERÊNCIA ATINGIDO.\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Stop monitorado: {stop:.3f}"
        )
        return

    if tocou_alvo:
        operacao["ultima_vela"] = horario
        fechar_operacao(
            "ALVO",
            "ALVO ATINGIDO.\n"
            f"Entrada real: {operacao['entrada_real']:.3f}\n"
            f"Alvo: {alvo:.3f}"
        )
        return

    operacao["ultima_vela"] = horario

    fechamentos = [
        item["close"] for item in velas
    ]

    ema9 = ema(fechamentos, 9)
    ema21 = ema(fechamentos, 21)
    valor_rsi = rsi(fechamentos, 14)
    atr = calcular_atr(velas, 14)

    if atr is None:
        enviar(
            "Acompanhamento pausado nesta vela: "
            "não há histórico suficiente para calcular ATR."
        )
        return

    contexto = obter_contexto_central(operacao)

    leitura = analisar_preco(
        vela["close"],
        contexto
    )

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

    acao = decisao["acao"]
    novo_stop = float(decisao["novo_stop"])

    if acao in ("MUDA_STOP", "REDUZ_RISCO"):
        stop_atual = float(operacao["stop"])
        pendente = operacao.get("stop_pendente")

        melhora = (
            novo_stop > stop_atual
            if direcao == "COMPRA"
            else novo_stop < stop_atual
        )

        if melhora:
            if pendente is None or round(
                novo_stop, 3
            ) != round(float(pendente), 3):

                operacao["stop_pendente"] = round(
                    novo_stop, 3
                )

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
# EXECUÇÃO
# =========================================================

def main():
    carregar()

    try:
        atualizacoes = buscar_atualizacoes(
            timeout_segundos=20
        )

        for atualizacao in atualizacoes:
            atualizar_offset(atualizacao)

            mensagem = atualizacao.get("message")

            if mensagem:
                processar_mensagem(mensagem)

        if estado.get("operacao"):
            velas = buscar_velas()
            conduzir(velas)

    finally:
        salvar()


if __name__ == "__main__":
    main()
