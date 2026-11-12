
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
INTERACAO TELEGRAM XAUUSD

Responsabilidades:
- Enviar decisoes importantes do Decisor.
- Confirmar recebimento de comandos da Recepcao.
- Evitar mensagens repetidas a cada vela M5.

A Recepcao e a unica responsavel por consultar getUpdates.
Este modulo somente envia mensagens.

Nao executa ordens.
Nao altera bancos oficiais.
"""

import json
import os
import urllib.parse
import urllib.request

from pathlib import Path
from datetime import datetime, timezone


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_DECISOES = DATA / "decisoes_operacoes_xau.json"
ARQUIVO_RECEPCAO = DATA / "entrada_recepcao_operacoes.json"
ARQUIVO_ESTADO = DATA / "interacao_telegram_estado.json"
ARQUIVO_SAIDA = DATA / "resultado_interacao_telegram.json"

LIMITE_HISTORICO_ENVIO = 3000

# Somente estas decisoes geram alerta operacional.
ACOES_IMPORTANTES = {
    "MUDA_STOP",
    "REDUZ_RISCO",
    "FECHA"
}


# ============================================================
# UTILIDADES
# ============================================================

def agora():
    return datetime.now(timezone.utc).isoformat()


def carregar_json(caminho, padrao=None):
    if padrao is None:
        padrao = {}

    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return padrao


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(caminho)


def formatar_valor(valor):
    if valor is None:
        return "N/D"

    if isinstance(valor, float):
        return f"{valor:.3f}"

    return str(valor)


# ============================================================
# ENVIO TELEGRAM
# ============================================================

def enviar_mensagem(token, chat_id, texto):

    url = (
        f"https://api.telegram.org/bot{token}/sendMessage"
    )

    dados = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto
    }).encode("utf-8")

    requisicao = urllib.request.Request(
        url,
        data=dados,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urllib.request.urlopen(
        requisicao,
        timeout=25
    ) as resposta:

        resultado = json.loads(
            resposta.read().decode("utf-8")
        )

    if not resultado.get("ok"):
        raise RuntimeError(
            "Telegram nao confirmou o envio."
        )

    return resultado


# ============================================================
# FORMATAR DECISAO OPERACIONAL
# ============================================================

def formatar_decisao(decisao, probabilidades):

    codigo = decisao.get(
        "codigo_operacao",
        "SEM_CODIGO"
    )

    acao = decisao.get(
        "decisao",
        "SEM_DECISAO"
    )

    linhas = [
        "XAUUSD | ALERTA OPERACIONAL",
        "",
        f"Operacao: {codigo}",
        f"Decisao: {acao}",
        (
            "Direcao: "
            f"{decisao.get('direcao_operacao', 'N/D')}"
        ),
        "",
        (
            "Probabilidade geral de subir: "
            f"{probabilidades.get('subir', 'N/D')}%"
        ),
        (
            "Probabilidade geral de cair: "
            f"{probabilidades.get('cair', 'N/D')}%"
        ),
        "",
        (
            "Preco de referencia: "
            f"{formatar_valor(decisao.get('preco_referencia'))}"
        ),
        (
            "Stop recomendado: "
            f"{formatar_valor(decisao.get('stop_recomendado'))}"
        ),
        (
            "Resultado: "
            f"{formatar_valor(decisao.get('resultado_r'))}R"
        ),
        "",
        f"Motivo: {decisao.get('motivo', '')}"
    ]

    leitura = decisao.get("leitura_central", {})

    if isinstance(leitura, dict):
        if leitura.get("disponivel"):
            linhas.extend([
                "",
                "Central M5: leitura disponivel."
            ])

    linhas.extend([
        "",
        "O sistema nao executa ordens."
    ])

    if decisao.get("confirmacao_manual"):

        linhas.extend([
            "",
            "CONFIRMACAO MANUAL NECESSARIA."
        ])

        if acao in ("MUDA_STOP", "REDUZ_RISCO"):
            linhas.append(
                f"Para confirmar: STOP OK {codigo}"
            )

        elif acao == "FECHA":
            linhas.append(
                "Depois de fechar manualmente, envie:"
            )
            linhas.append(
                f"CANCELA {codigo}"
            )

    return "\n".join(linhas)


# ============================================================
# CHAVE DE DUPLICIDADE DAS DECISOES
# ============================================================

def chave_decisao(decisao):

    codigo = decisao.get(
        "codigo_operacao",
        ""
    )

    acao = str(
        decisao.get("decisao", "")
    ).upper()

    stop = decisao.get("stop_recomendado")

    # A mesma recomendacao de stop nao sera repetida
    # em todas as velas seguintes.
    if acao in ("MUDA_STOP", "REDUZ_RISCO"):
        return (
            f"DECISAO|{codigo}|{acao}|{stop}"
        )

    # Fechamento e comunicado somente uma vez.
    if acao == "FECHA":
        return (
            f"DECISAO|{codigo}|FECHA"
        )

    return None


# ============================================================
# FILTRO DE DECISOES IMPORTANTES
# ============================================================

def decisao_importante(decisao):

    acao = str(
        decisao.get("decisao", "")
    ).upper()

    if acao not in ACOES_IMPORTANTES:
        return False

    if decisao.get("confirmacao_manual") is not True:
        return False

    if not decisao.get("codigo_operacao"):
        return False

    return True


# ============================================================
# FORMATAR EVENTOS DA RECEPCAO
# ============================================================

def formatar_evento(evento):

    comando = evento.get("comando")

    parametros = evento.get(
        "parametros",
        {}
    )

    codigo = parametros.get(
        "codigo_operacao"
    )

    validacao = evento.get(
        "validacao"
    )

    if validacao in (
        "SEM_CODIGO_OPERACAO",
        "OPERACAO_NAO_ENCONTRADA_NO_BANCO",
        "PRECO_ENTRADA_INVALIDO",
        "FORMATO_INVALIDO"
    ):

        return (
            "XAUUSD | COMANDO NAO ACEITO\n\n"
            f"Comando: {comando}\n"
            f"Operacao: {codigo or 'Nao informada'}\n"
            f"Motivo: {validacao}\n\n"
            "Confira o codigo e os parametros."
        )

    mensagens = {

        "ENTREI": (
            "XAUUSD | ENTRADA RECEBIDA\n\n"
            f"Operacao: {codigo}\n"
            "Preco informado registrado pela Recepcao.\n"
            "O acompanhamento podera considerar "
            "a entrada manual."
        ),

        "CANCELA": (
            "XAUUSD | ENCERRAMENTO SOLICITADO\n\n"
            f"Operacao: {codigo}\n"
            "Pedido encaminhado para processamento "
            "pelo Coordenador."
        ),

        "STOP_OK": (
            "XAUUSD | STOP CONFIRMADO\n\n"
            f"Operacao: {codigo}\n"
            "Confirmacao manual recebida e encaminhada "
            "para o Coordenador."
        ),

        "STOP_NAO": (
            "XAUUSD | STOP RECUSADO\n\n"
            f"Operacao: {codigo}\n"
            "O stop anterior deve ser preservado."
        ),

        "STATUS": (
            "XAUUSD | STATUS\n\n"
            "Comando recebido. Consulte a saida da "
            "Recepcao para verificar as operacoes ativas."
        ),

        "DESLIGA": (
            "XAUUSD | DESLIGA RECEBIDO\n\n"
            "Comando encaminhado para tratamento "
            "pelo Coordenador."
        )
    }

    return mensagens.get(comando)


# ============================================================
# EXECUCAO
# ============================================================

def executar():

    print("=== INTERACAO TELEGRAM XAUUSD ===")

    token = os.getenv(
        "TELEGRAM_ACOMPANHAMENTO_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        raise RuntimeError(
            "Credenciais do Telegram ausentes."
        )

    decisoes = carregar_json(
        ARQUIVO_DECISOES,
        {}
    )

    recepcao = carregar_json(
        ARQUIVO_RECEPCAO,
        {}
    )

    estado = carregar_json(
        ARQUIVO_ESTADO,
        {
            "mensagens_enviadas": []
        }
    )

    ja_enviadas = set(
        estado.get(
            "mensagens_enviadas",
            []
        )
    )

    probabilidades = decisoes.get(
        "probabilidade_geral",
        {}
    )

    enviadas = []
    ignoradas = 0
    erros = []

    # ========================================================
    # 1. DECISOES DO DECISOR
    # ========================================================

    for decisao in decisoes.get("decisoes", []):

        if not decisao_importante(decisao):
            ignoradas += 1
            continue

        chave = chave_decisao(decisao)

        if not chave:
            ignoradas += 1
            continue

        if chave in ja_enviadas:
            ignoradas += 1
            continue

        mensagem = formatar_decisao(
            decisao,
            probabilidades
        )

        try:

            enviar_mensagem(
                token,
                chat_id,
                mensagem
            )

            ja_enviadas.add(chave)
            enviadas.append(chave)

            print(
                "Alerta enviado:",
                decisao.get("codigo_operacao"),
                decisao.get("decisao")
            )

        except Exception as erro:

            erros.append({
                "tipo": "DECISAO",
                "codigo_operacao": (
                    decisao.get("codigo_operacao")
                ),
                "erro": str(erro)
            })

    # ========================================================
    # 2. CONFIRMACOES DOS COMANDOS DA RECEPCAO
    # ========================================================

    for evento in recepcao.get(
        "eventos_telegram",
        []
    ):

        update_id = evento.get("update_id")

        if update_id is None:
            continue

        chave = f"EVENTO|{update_id}"

        if chave in ja_enviadas:
            continue

        mensagem = formatar_evento(evento)

        if not mensagem:
            continue

        try:

            enviar_mensagem(
                token,
                chat_id,
                mensagem
            )

            ja_enviadas.add(chave)
            enviadas.append(chave)

            print(
                "Confirmacao enviada:",
                evento.get("comando")
            )

        except Exception as erro:

            erros.append({
                "tipo": "EVENTO",
                "update_id": update_id,
                "erro": str(erro)
            })

    # ========================================================
    # 3. PERSISTIR CONTROLE DE DUPLICIDADE
    # ========================================================

    historico = sorted(ja_enviadas)

    estado["mensagens_enviadas"] = (
        historico[-LIMITE_HISTORICO_ENVIO:]
    )

    estado["atualizado_utc"] = agora()

    salvar_json(
        ARQUIVO_ESTADO,
        estado
    )

    # ========================================================
    # 4. RELATORIO DA EXECUCAO
    # ========================================================

    resultado = {
        "modulo": "interacao_telegram_xau",

        "executado_utc": agora(),

        "mensagens_enviadas": len(enviadas),

        "decisoes_ignoradas": ignoradas,

        "erros": erros,

        "regras": {
            "envia_continua": False,
            "envia_decisoes_sem_acao": False,
            "envia_apenas_acoes_importantes": True,
            "consulta_telegram": False,
            "execucao_ordens": False
        }
    }

    salvar_json(
        ARQUIVO_SAIDA,
        resultado
    )

    print("--------------------------------------")
    print("Mensagens enviadas:", len(enviadas))
    print("Decisoes ignoradas:", ignoradas)
    print("Erros:", len(erros))
    print("INTERACAO CONCLUIDA")


if __name__ == "__main__":
    executar()
