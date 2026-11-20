#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini exclusivo para condução de uma operação XAUUSD já confirmada pelo usuário.

Entrada: gestor_operacao_contexto.json (preparado pelo workflow de condução).
Saídas: gestor_conducao_resultado.json e gestor_resposta.txt.

Este módulo não gera sinais, não altera bancos e não envia ordens à corretora.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta"
ARQUIVO_ENTRADA = Path(os.getenv(
    "GESTOR_OPERACAO_CONTEXTO", "gestor_operacao_contexto.json"
))
ARQUIVO_RESULTADO = Path("gestor_conducao_resultado.json")
ARQUIVO_RESPOSTA = Path("gestor_resposta.txt")
MAX_CONTEXTO = 30000


def requisicao_json(url, chave, metodo="GET", body=None):
    dados = None if body is None else json.dumps(
        body, ensure_ascii=False
    ).encode("utf-8")
    headers = {"x-goog-api-key": chave}
    if dados is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=dados, headers=headers, method=metodo
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detalhe = error.read().decode("utf-8", "replace")[:700]
        raise RuntimeError(
            "Gemini HTTP " + str(error.code) + ": " + detalhe
        )


def listar_modelos(chave):
    """Descobre modelos de texto disponíveis para a chave do Gestor."""
    modelos = []
    token = None
    for _ in range(5):
        url = API + "/models?pageSize=100"
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        dados = requisicao_json(url, chave)
        for item in dados.get("models", []):
            nome = item.get("name", "")
            metodos = item.get("supportedGenerationMethods", [])
            base = nome.rsplit("/", 1)[-1].lower()
            if (
                nome
                and "generateContent" in metodos
                and not any(x in base for x in (
                    "embedding", "image", "tts", "audio", "live", "robotics"
                ))
            ):
                modelos.append(nome)
        token = dados.get("nextPageToken")
        if not token:
            break

    preferidos = [
        os.getenv("GESTOR_GEMINI_MODEL_CONDUCAO", "").strip(),
        os.getenv("GESTOR_GEMINI_MODEL", "").strip(),
    ]

    def prioridade(nome):
        base = nome.rsplit("/", 1)[-1].lower()
        for pos, preferido in enumerate(preferidos):
            if preferido and base == preferido.rsplit("/", 1)[-1].lower():
                return (pos, base)
        if "flash-lite" in base:
            return (2, base)
        if "flash" in base:
            return (3, base)
        if "pro" in base:
            return (4, base)
        return (5, base)

    modelos = sorted(set(modelos), key=prioridade)
    if not modelos:
        raise RuntimeError(
            "Nenhum modelo Gemini compatível com generateContent foi encontrado."
        )
    return modelos


def carregar_contexto():
    if not ARQUIVO_ENTRADA.is_file():
        raise RuntimeError(
            "Contexto da operação não encontrado: "
            + str(ARQUIVO_ENTRADA)
            + ". O workflow precisa preparar esse arquivo antes da análise."
        )

    with ARQUIVO_ENTRADA.open("r", encoding="utf-8") as arquivo:
        contexto = json.load(arquivo)

    operacao = contexto.get("operacao")
    mercado = contexto.get("mercado")
    if not isinstance(operacao, dict) or not isinstance(mercado, dict):
        raise RuntimeError(
            "Contexto inválido: são obrigatórios os objetos 'operacao' e 'mercado'."
        )

    codigo = str(operacao.get("codigo", "")).strip()
    direcao = str(operacao.get("direcao", "")).strip().upper()
    status = str(operacao.get("status", "")).strip().upper()

    if not re.fullmatch(r"OP-\d{6,}", codigo):
        raise RuntimeError("Código da operação inválido ou ausente.")
    if direcao not in ("LONG", "SHORT", "COMPRA", "VENDA"):
        raise RuntimeError("Direção da operação inválida ou ausente.")
    if status not in ("ABERTA", "ATIVA", "EM_ANDAMENTO"):
        raise RuntimeError("A operação não está marcada como aberta.")
    if operacao.get("confirmacao_usuario") is not True:
        raise RuntimeError(
            "A operação precisa estar confirmada manualmente pelo usuário."
        )

    for campo in ("preco_entrada", "stop_atual", "alvo"):
        try:
            valor = float(operacao[campo])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Dado obrigatório ausente ou inválido na operação: " + campo
            )
        if valor <= 0:
            raise RuntimeError("Valor inválido na operação: " + campo)

    if not mercado:
        raise RuntimeError("O contexto de mercado está vazio.")

    bruto = json.dumps(contexto, ensure_ascii=False, default=str)
    if len(bruto) > MAX_CONTEXTO:
        raise RuntimeError(
            "Contexto acima do limite seguro de "
            + str(MAX_CONTEXTO)
            + " caracteres; reduza as amostras antes de chamar o Gemini."
        )
    return contexto, bruto


INSTRUCAO_CONDUCAO = """
Você é o Gemini de CONDUÇÃO OPERACIONAL do Gestor XAUUSD.
Sua única tarefa é avaliar uma operação que o usuário já confirmou manualmente.
Você NÃO gera novos sinais e NÃO substitui o Gemini Sinal da Central.

A cada ciclo, examine a operação, o preço atual, candles fechados, indicadores,
contexto M5/M15/H1, tabuleiro, regiões, transições e histórico que estiverem
disponíveis. Use também "resumo_mercado" como orientação matemática auxiliar e
"ultimas_operacoes_produzidas" para comparar os sinais recentes, suas direções,
classificações e resultados disponíveis. O resumo não é uma decisão: confira as
evidências brutas e discorde dele quando os dados justificarem. Diferencie sinais
aprovados de negados e não trate resultado PENDENTE como lucro ou prejuízo.
Dê prioridade aos dados mais recentes e informe quando algum dado estiver ausente
ou desatualizado. Não invente valores nem presuma que uma vela ainda aberta já fechou.

Escolha exatamente UMA ação:
- MANTER: a estrutura ainda sustenta a operação e não há razão suficiente para
  alterar o stop ou recomendar encerramento.
- AJUSTAR_STOP: existe fundamento técnico para proteger mais o resultado.
  Informe um novo stop numérico, sem ampliar o risco original.
- ENCERRAR: a relação entre cenário, risco e operação deixou de justificar
  permanecer nela. Essa decisão FINALIZA o acompanhamento automático desta
  operação, mas NÃO envia ordem à corretora. O usuário continua responsável
  por executar manualmente o fechamento.

Não use ENCERRAR apenas por ruído de uma vela ou por pequena oscilação.
Considere a direção da operação, distância até stop e alvo, volatilidade,
estrutura, regiões e evidências contraditórias. Seja independente e adaptável;
não imponha alinhamento perfeito entre timeframes nem padrões fixos.
Não afrouxe o stop: em LONG, um novo stop deve ser maior que o stop atual;
em SHORT, deve ser menor que o stop atual. Não sugira stop que já tenha sido
ultrapassado pelo preço atual. Se não houver preço atual confiável, não escolha
AJUSTAR_STOP.

Responda SOMENTE com um objeto JSON válido, sem Markdown e sem texto externo,
com exatamente estes campos:
{
  "codigo_operacao": "OP-000266",
  "acao": "MANTER | AJUSTAR_STOP | ENCERRAR",
  "novo_stop": null,
  "status_conducao": "ATIVA | FINALIZADA",
  "confianca": 0,
  "risco": "BAIXO | MODERADO | ALTO",
  "justificativa": "explicação objetiva baseada nos dados recebidos",
  "mensagem_telegram": "resumo curto e claro para o usuário"
}

Regras do JSON:
- MANTER: novo_stop=null e status_conducao="ATIVA".
- AJUSTAR_STOP: novo_stop numérico e status_conducao="ATIVA".
- ENCERRAR: novo_stop=null e status_conducao="FINALIZADA".
- confiança é um número inteiro de 0 a 100.
- Não inclua campos adicionais.
"""


def extrair_json(texto):
    texto = texto.strip()
    texto = re.sub(r"^\s*```(?:json)?\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s*```\s*$", "", texto)
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise RuntimeError("Gemini não retornou um objeto JSON válido.")
    try:
        return json.loads(texto[inicio:fim + 1])
    except json.JSONDecodeError as erro:
        raise RuntimeError("JSON retornado pelo Gemini é inválido: " + str(erro))


def validar_decisao(decisao, contexto):
    campos = {
        "codigo_operacao", "acao", "novo_stop", "status_conducao",
        "confianca", "risco", "justificativa", "mensagem_telegram"
    }
    if not isinstance(decisao, dict) or set(decisao) != campos:
        raise RuntimeError("Gemini retornou campos diferentes do contrato de condução.")

    operacao = contexto["operacao"]
    codigo = str(operacao["codigo"])
    if decisao["codigo_operacao"] != codigo:
        raise RuntimeError("Gemini retornou código de operação diferente do ativo.")

    acao = decisao["acao"]
    status = decisao["status_conducao"]
    if acao not in ("MANTER", "AJUSTAR_STOP", "ENCERRAR"):
        raise RuntimeError("Ação operacional desconhecida.")
    if decisao["risco"] not in ("BAIXO", "MODERADO", "ALTO"):
        raise RuntimeError("Classificação de risco inválida.")
    try:
        confianca = int(decisao["confianca"])
    except (TypeError, ValueError):
        raise RuntimeError("Confiança inválida.")
    if not 0 <= confianca <= 100:
        raise RuntimeError("Confiança deve ficar entre 0 e 100.")
    for campo in ("justificativa", "mensagem_telegram"):
        if not isinstance(decisao[campo], str) or not decisao[campo].strip():
            raise RuntimeError("Campo textual obrigatório vazio: " + campo)

    if acao == "MANTER":
        if status != "ATIVA" or decisao["novo_stop"] is not None:
            raise RuntimeError("Resposta MANTER inconsistente.")
    elif acao == "ENCERRAR":
        if status != "FINALIZADA" or decisao["novo_stop"] is not None:
            raise RuntimeError("Resposta ENCERRAR inconsistente.")
        decisao["mensagem_telegram"] = (
            "RECOMENDAÇÃO DE ENCERRAMENTO — "
            + decisao["mensagem_telegram"]
            + " Fechamento manual; nenhuma ordem foi executada."
        )
    else:
        if status != "ATIVA":
            raise RuntimeError("Resposta AJUSTAR_STOP inconsistente.")
        try:
            novo = float(decisao["novo_stop"])
            atual = float(operacao["stop_atual"])
            preco = float(contexto["mercado"]["preco_atual"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Não há preço atual e stop atual suficientes para validar o novo stop."
            )
        direcao = str(operacao["direcao"]).upper()
        if direcao in ("LONG", "COMPRA"):
            valido = atual < novo < preco
        else:
            valido = preco < novo < atual
        if not valido:
            raise RuntimeError(
                "Novo stop recusado: não pode ampliar o risco nem ficar além do preço atual."
            )
        decisao["novo_stop"] = round(novo, 5)

    decisao["confianca"] = confianca
    decisao["tipo_resultado"] = "DECISAO_GEMINI_CONDUCAO"
    decisao["ordem_executada"] = False
    decisao["confirmacao_manual_necessaria"] = True
    return decisao


def gerar_decisao(contexto_texto, contexto):
    chave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("Secret GEMINI_API_KEY não configurado.")

    prompt = (
        INSTRUCAO_CONDUCAO
        + "\n\nCONTEXTO ATUAL DA OPERAÇÃO E DO MERCADO:\n"
        + contexto_texto
    )
    body = {
        "systemInstruction": {"parts": [{"text": INSTRUCAO_CONDUCAO}]},
        "contents": [{"parts": [{"text": (
            "Avalie este ciclo da operação e devolva somente o JSON exigido.\n"
            + contexto_texto
        )}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json"
        }
    }

    falhas = []
    for modelo in listar_modelos(chave):
        try:
            dados = requisicao_json(
                API + "/" + modelo + ":generateContent",
                chave, metodo="POST", body=body
            )
            partes = (
                dados.get("candidates", [{}])[0]
                .get("content", {}).get("parts", [])
            )
            texto = "\n".join(
                p.get("text", "") for p in partes if p.get("text")
            ).strip()
            if not texto:
                raise RuntimeError("Resposta vazia.")
            decisao = validar_decisao(extrair_json(texto), contexto)
            decisao["modelo_utilizado"] = modelo
            return decisao
        except Exception as erro:
            falhas.append(modelo + ": " + str(erro)[:180])

    raise RuntimeError(
        "Nenhum modelo Gemini produziu decisão válida. "
        + " | ".join(falhas[:4])
    )


def main():
    contexto, bruto = carregar_contexto()
    decisao = gerar_decisao(bruto, contexto)

    ARQUIVO_RESULTADO.write_text(
        json.dumps(decisao, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    ARQUIVO_RESPOSTA.write_text(
        decisao["mensagem_telegram"],
        encoding="utf-8"
    )

    print(
        "Condução concluída: "
        + decisao["acao"]
        + " | "
        + decisao["codigo_operacao"]
        + " | acompanhamento "
        + decisao["status_conducao"]
    )


if __name__ == "__main__":
    main()
