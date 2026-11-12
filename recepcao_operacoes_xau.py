
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone


# ============================================================
# RECEPÇÃO DE OPERAÇÕES XAUUSD
# Fontes:
# 1. Telegram
# 2. Histórico de operações do Coordenador
#
# Não consulta o Gerador.
# Não executa ordens.
# Não altera os bancos oficiais do Coordenador.
# ============================================================

BASE_DIR = Path("data")

ARQUIVO_HISTORICO = BASE_DIR / "resultados_operacoes.json"
ARQUIVO_ESTADO = BASE_DIR / "recepcao_operacoes_estado.json"
ARQUIVO_SAIDA = BASE_DIR / "entrada_recepcao_operacoes.json"


def carregar_json(caminho, padrao=None):
    if padrao is None:
        padrao = {}

    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (json.JSONDecodeError, OSError):
        return padrao


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(caminho.suffix + ".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(caminho)


def obter_lista_operacoes(dados):
    """
    Aceita formatos comuns de histórico.
    Não cria operações nem inventa identificadores.
    """

    if isinstance(dados, list):
        return dados

    if not isinstance(dados, dict):
        return []

    for chave in (
        "operacoes",
        "historico",
        "resultados",
        "operacoes_registradas"
    ):
        valor = dados.get(chave)

        if isinstance(valor, list):
            return valor

    return []


def identificar_codigo(operacao):
    if not isinstance(operacao, dict):
        return None

    # Código oficial único do Coordenador.
    codigo = operacao.get("codigo")

    if not codigo:
        codigo = operacao.get("codigo_operacao")

    if not codigo:
        codigo = operacao.get("op_id")

    if not codigo:
        codigo = operacao.get("id")

    return str(codigo).strip() if codigo else None


def operacao_ativa(operacao):
    if not isinstance(operacao, dict):
        return False

    estado = str(
        operacao.get("status")
        or operacao.get("estado")
        or operacao.get("situacao")
        or ""
    ).strip().upper()

    encerrados = {
        "FECHADA",
        "FECHADO",
        "ENCERRADA",
        "ENCERRADO",
        "CANCELADA",
        "CANCELADO",
        "FINALIZADA",
        "FINALIZADO",
        "STOP",
        "TP"
    }

    if estado in encerrados:
        return False

    return True


def carregar_historico_coordenador():
    dados = carregar_json(ARQUIVO_HISTORICO, [])

    operacoes = obter_lista_operacoes(dados)

    resultado = {}

    for operacao in operacoes:
        if not isinstance(operacao, dict):
            continue

        codigo = identificar_codigo(operacao)

        if not codigo:
            continue

        # O código oficial é a chave de identificação.
        # Duplicatas com o mesmo código não geram novas operações.
        resultado[codigo] = operacao

    return resultado


def solicitar_telegram(url, parametros):
    dados = urllib.parse.urlencode(parametros).encode("utf-8")

    requisicao = urllib.request.Request(
        url,
        data=dados,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urllib.request.urlopen(requisicao, timeout=25) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def consultar_telegram():
    token = os.getenv("TELEGRAM_ACOMPANHAMENTO_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return {
            "disponivel": False,
            "eventos": [],
            "proximo_update_id": None,
            "motivo": "Credenciais do Telegram não configuradas."
        }

    estado = carregar_json(ARQUIVO_ESTADO, {})
    ultimo_update = int(estado.get("ultimo_update_id", 0))

    url = f"https://api.telegram.org/bot{token}/getUpdates"

    try:
        resposta = solicitar_telegram(
            url,
            {
                "offset": ultimo_update + 1,
                "timeout": 0,
                "allowed_updates": json.dumps(["message"])
            }
        )

        if not resposta.get("ok"):
            raise RuntimeError("Telegram retornou resposta inválida.")

        eventos = []
        maior_update = ultimo_update

        for item in resposta.get("result", []):
            update_id = int(item.get("update_id", 0))
            maior_update = max(maior_update, update_id)

            mensagem = item.get("message", {})
            remetente = mensagem.get("chat", {})
            texto = mensagem.get("text", "")

            # Só aceita mensagens do chat autorizado.
            if str(remetente.get("id")) != str(chat_id):
                continue

            if not texto:
                continue

            eventos.append(
                interpretar_comando(
                    texto,
                    update_id
                )
            )

        return {
            "disponivel": True,
            "eventos": eventos,
            "proximo_update_id": maior_update,
            "motivo": None
        }

    except Exception as erro:
        return {
            "disponivel": False,
            "eventos": [],
            "proximo_update_id": None,
            "motivo": str(erro)
        }


def interpretar_comando(texto, update_id=None):
    texto_original = texto.strip()
    texto = texto_original.upper()

    partes = texto.split()

    if not partes:
        comando = "DESCONHECIDO"
        parametros = {}

    elif partes[0] == "ENTREI":
        codigo = partes[1] if len(partes) > 1 else None
        preco = None

        if len(partes) > 2:
            try:
                preco = float(partes[2].replace(",", "."))
            except ValueError:
                preco = None

        comando = "ENTREI"
        parametros = {
            "codigo_operacao": codigo,
            "preco_entrada": preco
        }

    elif partes[0] in ("CANCELA", "CANCELAR"):
        comando = "CANCELA"
        parametros = {
            "codigo_operacao": partes[1] if len(partes) > 1 else None
        }

    elif partes[0] == "STOP" and len(partes) > 1:
        if partes[1] == "OK":
            comando = "STOP_OK"
        elif partes[1] in ("NAO", "NÃO"):
            comando = "STOP_NAO"
        else:
            comando = "DESCONHECIDO"

        parametros = {
            "codigo_operacao": partes[2] if len(partes) > 2 else None
        }

    elif partes[0] == "STATUS":
        comando = "STATUS"
        parametros = {}

    elif partes[0] == "DESLIGA":
        comando = "DESLIGA"
        parametros = {}

    else:
        comando = "DESCONHECIDO"
        parametros = {
            "texto_original": texto_original
        }

    return {
        "update_id": update_id,
        "comando": comando,
        "parametros": parametros,
        "recebido_em": datetime.now(
            timezone.utc
        ).isoformat()
    }


def validar_eventos(eventos, historico):
    validos = []

    for evento in eventos:
        comando = evento.get("comando")
        parametros = evento.get("parametros", {})

        codigo = parametros.get("codigo_operacao")

        if comando in (
            "ENTREI",
            "CANCELA",
            "STOP_OK",
            "STOP_NAO"
        ):
            if not codigo:
                evento["validacao"] = "SEM_CODIGO_OPERACAO"
                validos.append(evento)
                continue

            if codigo not in historico:
                evento["validacao"] = "OPERACAO_NAO_ENCONTRADA_NO_COORDENADOR"
                validos.append(evento)
                continue

            evento["validacao"] = "OPERACAO_IDENTIFICADA"

        else:
            evento["validacao"] = "COMANDO_RECEBIDO"

        validos.append(evento)

    return validos


def executar_recepcao():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    # Fonte 1: histórico oficial do Coordenador.
    historico = carregar_historico_coordenador()

    # Fonte 2: mensagens do Telegram.
    telegram = consultar_telegram()

    eventos = validar_eventos(
        telegram.get("eventos", []),
        historico
    )

    operacoes_ativas = {
        codigo: operacao
        for codigo, operacao in historico.items()
        if operacao_ativa(operacao)
    }

    resultado = {
        "modulo": "recepcao_operacoes_xau",
        "versao": 1,
        "executado_em": datetime.now(
            timezone.utc
        ).isoformat(),
        "fontes": {
            "telegram": telegram.get("disponivel", False),
            "historico_coordenador": True
        },
        "quantidade_operacoes_historico": len(historico),
        "quantidade_operacoes_ativas": len(operacoes_ativas),
        "operacoes_ativas": operacoes_ativas,
        "eventos_telegram": eventos,
        "telegram_erro": telegram.get("motivo")
    }

    salvar_json(ARQUIVO_SAIDA, resultado)

    # Atualiza o cursor somente depois de salvar a saída.
    if telegram.get("proximo_update_id") is not None:
        estado = carregar_json(ARQUIVO_ESTADO, {})

        estado["ultimo_update_id"] = telegram["proximo_update_id"]

        salvar_json(ARQUIVO_ESTADO, estado)

    print("RECEPCAO DE OPERACOES CONCLUIDA")
    print(f"Operações no histórico: {len(historico)}")
    print(f"Operações ativas: {len(operacoes_ativas)}")
    print(f"Comandos recebidos: {len(eventos)}")
    print(f"Arquivo de saída: {ARQUIVO_SAIDA}")


if __name__ == "__main__":
    executar_recepcao()
