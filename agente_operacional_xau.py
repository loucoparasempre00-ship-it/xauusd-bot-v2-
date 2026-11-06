
import json
import math
import os
import re
import sys
import urllib.request
import urllib.error

from pathlib import Path
from datetime import datetime, timezone


PASTA = Path("data")

ARQUIVO_ENTRADA = (
    PASTA / "pacote_agente_operacional.json"
)

ARQUIVO_SAIDA = (
    PASTA / "decisao_agente_operacional.json"
)

MODELO_PADRAO = "models/gemini-3.1-flash-lite"

API_BASE = (
    "https://generativelanguage.googleapis.com/v1beta"
)


def salvar_resultado(dados):
    PASTA.mkdir(exist_ok=True)

    dados["gerado_utc"] = datetime.now(
        timezone.utc
    ).isoformat()

    ARQUIVO_SAIDA.write_text(
        json.dumps(
            dados,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def carregar_entrada():
    if not ARQUIVO_ENTRADA.exists():
        raise FileNotFoundError(
            "Pacote de contexto operacional não encontrado: "
            + str(ARQUIVO_ENTRADA)
        )

    dados = json.loads(
        ARQUIVO_ENTRADA.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(dados, dict):
        raise ValueError(
            "O pacote operacional não é um objeto JSON."
        )

    operacao = dados.get("operacao")
    contexto = dados.get("contexto")

    if not isinstance(operacao, dict):
        raise ValueError(
            "Operação ativa ausente no pacote."
        )

    if not isinstance(contexto, dict):
        raise ValueError(
            "Contexto inteligente ausente no pacote."
        )

    if operacao.get("status") != "ATIVA":
        raise ValueError(
            "A operação não está marcada como ATIVA."
        )

    if operacao.get("direcao") not in (
        "COMPRA",
        "VENDA"
    ):
        raise ValueError(
            "Direção da operação inválida."
        )

    return dados


def carregar_modelo():
    caminho = PASTA / "modelo_gemini.json"

    if caminho.exists():
        try:
            selecao = json.loads(
                caminho.read_text(encoding="utf-8")
            )

            modelo = selecao.get("modelo_escolhido")

            if (
                selecao.get("status") in (
                    "pronto",
                    "pronto_com_aviso_api"
                )
                and isinstance(modelo, str)
                and modelo.startswith("models/gemini-")
            ):
                return modelo

        except Exception:
            pass

    modelo = os.getenv(
        "GEMINI_MODEL",
        MODELO_PADRAO
    ).strip()

    if not modelo.startswith("models/gemini-"):
        raise ValueError(
            "Modelo Gemini inválido."
        )

    return modelo


def montar_prompt(pacote):
    operacao = pacote["operacao"]
    contexto = pacote["contexto"]

    decisao_diretor = pacote.get(
        "decisao_diretor",
        {}
    )

    memoria = pacote.get(
        "memoria",
        {}
    )

    prompt = """
Você é o AGENTE OPERACIONAL INTELIGENTE do acompanhamento
de uma operação XAUUSD no timeframe M5.

Sua função é interpretar o cenário completo e produzir
uma segunda leitura independente para auxiliar o diretor.

Você recebe:
- Operação real informada pelo usuário.
- Histórico cronológico de candles M5.
- Gráfico reconstruído com OHLC e EMAs.
- Indicadores técnicos e estrutura de mercado.
- Central Inteligente e regiões históricas.
- Memória das observações anteriores.
- Decisão do diretor operacional.

OBJETIVO PRINCIPAL:

Compreender a evolução do mercado, não apenas o último preço.

Observe:
1. Sequência de topos e fundos.
2. Continuidade ou perda de tendência.
3. Impulso e desaceleração.
4. Rompimentos e possíveis falsos rompimentos.
5. Rejeições de preço e pavios.
6. Distância e comportamento em relação às médias.
7. MACD, RSI, ATR e demais indicadores disponíveis.
8. Região atual na Central Inteligente.
9. Histórico de visitas e transições entre regiões.
10. Mudanças de comportamento desde a entrada.
11. Coerência entre o cenário atual e a direção da operação.

REGRAS DE SEGURANÇA:

- Não invente preços, candles ou indicadores.
- Não trate dados ausentes como se fossem válidos.
- Não considere uma vela ainda aberta como fechada.
- Não afirme que o stop ou alvo foi executado sem evidência.
- Não altere a entrada original.
- Não altere o alvo original.
- Não envie ordens.
- Não execute operações na corretora.
- Não transforme estatísticas históricas em certezas.
- Não trate a Central como um segundo motor de sinais.
- Não ignore uma divergência importante entre as fontes.
- Se os dados estiverem desatualizados ou inconsistentes,
  recomende CONTINUA apenas no sentido de aguardar,
  sem emitir uma conclusão operacional confiável.

A decisão do diretor é uma referência de segurança.
Você pode concordar ou discordar, mas deve explicar
objetivamente a divergência.

AÇÕES PERMITIDAS:

CONTINUA:
O cenário ainda sustenta a operação.

FECHA:
Há evidências relevantes de deterioração ou reversão.
É somente uma recomendação para avaliação humana.

REDUZ_RISCO:
Há enfraquecimento ou risco crescente e existe justificativa
para avaliar proteção adicional.

MUDA_STOP:
Existe justificativa técnica para propor um novo stop.
O preço proposto será apenas uma sugestão.
A alteração depende de confirmação manual do usuário.

IMPORTANTE:
Nunca recomende um stop menos protetor que o atual.
Para COMPRA, um novo stop deve ser maior que o atual.
Para VENDA, um novo stop deve ser menor que o atual.
Se não houver preço válido, use null.

CONFIANÇA:
Use uma nota de 0 a 100 para expressar sua confiança
na interpretação, não uma probabilidade comprovada
de lucro ou acerto.

FORMATO OBRIGATÓRIO:
Responda somente com JSON válido, sem Markdown,
sem explicações fora do JSON.

{
  "acao": "CONTINUA",
  "confianca": 0,
  "justificativa": "Explicação objetiva do cenário.",
  "evidencias": [
    "Evidência observada nos dados."
  ],
  "risco_detectado": "Descrição do risco ou nenhum risco relevante.",
  "preco_stop_recomendado": null,
  "divergencia_diretor": false,
  "motivo_divergencia": "",
  "dados_suficientes": true
}

DADOS DA OPERAÇÃO:
""" + json.dumps(
        operacao,
        ensure_ascii=False
    )

    prompt += """

CONTEXTO COMPLETO DO MERCADO:
""" + json.dumps(
        contexto,
        ensure_ascii=False
    )

    prompt += """

DECISÃO DO DIRETOR OPERACIONAL:
""" + json.dumps(
        decisao_diretor,
        ensure_ascii=False
    )

    prompt += """

MEMÓRIA DA OPERAÇÃO:
""" + json.dumps(
        memoria,
        ensure_ascii=False
    )

    return prompt


def extrair_json(texto):
    texto = texto.strip()

    texto = re.sub(
        r"^```(?:json)?\s*",
        "",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"\s*```$",
        "",
        texto
    )

    inicio = texto.find("{")
    fim = texto.rfind("}")

    if inicio < 0 or fim <= inicio:
        raise ValueError(
            "Gemini não retornou um objeto JSON válido."
        )

    return json.loads(
        texto[inicio:fim + 1]
    )


def consultar_gemini(prompt, modelo, chave):
    url = (
        f"{API_BASE}/{modelo}:generateContent"
        f"?key={chave}"
    )

    corpo = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json"
        }
    }

    requisicao = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(
        requisicao,
        timeout=60
    ) as resposta:
        retorno = json.loads(
            resposta.read().decode("utf-8")
        )

    candidatos = retorno.get(
        "candidates",
        []
    )

    if not candidatos:
        raise RuntimeError(
            "Gemini não retornou candidatos."
        )

    partes = candidatos[0].get(
        "content", {}
    ).get("parts", [])

    textos = [
        parte.get("text", "")
        for parte in partes
        if parte.get("text")
    ]

    if not textos:
        raise RuntimeError(
            "Gemini retornou resposta sem texto."
        )

    return extrair_json(
        "\n".join(textos)
    )


def validar_decisao(decisao, operacao):
    if not isinstance(decisao, dict):
        raise ValueError(
            "Decisão retornada não é um objeto."
        )

    acoes = (
        "CONTINUA",
        "FECHA",
        "REDUZ_RISCO",
        "MUDA_STOP"
    )

    acao = decisao.get("acao")

    if acao not in acoes:
        raise ValueError(
            "Ação operacional não reconhecida."
        )

    try:
        confianca = float(
            decisao.get("confianca", 0)
        )
    except (TypeError, ValueError):
        confianca = 0

    if not math.isfinite(confianca):
        confianca = 0

    decisao["confianca"] = max(
        0,
        min(100, round(confianca, 1))
    )

    decisao["evidencias"] = (
        decisao.get("evidencias")
        if isinstance(
            decisao.get("evidencias"),
            list
        )
        else []
    )

    decisao["justificativa"] = str(
        decisao.get("justificativa", "")
    )

    decisao["risco_detectado"] = str(
        decisao.get("risco_detectado", "")
    )

    decisao["divergencia_diretor"] = bool(
        decisao.get("divergencia_diretor", False)
    )

    decisao["dados_suficientes"] = bool(
        decisao.get("dados_suficientes", False)
    )

    stop = decisao.get(
        "preco_stop_recomendado"
    )

    if acao in ("MUDA_STOP", "REDUZ_RISCO"):
        try:
            stop = float(stop)
        except (TypeError, ValueError):
            raise ValueError(
                "Ação de stop sem preço válido."
            )

        if not math.isfinite(stop) or stop <= 0:
            raise ValueError(
                "Preço de stop inválido."
            )

        atual = float(operacao["stop"])
        direcao = operacao["direcao"]

        if direcao == "COMPRA" and stop <= atual:
            raise ValueError(
                "Novo stop de COMPRA não protege mais que o atual."
            )

        if direcao == "VENDA" and stop >= atual:
            raise ValueError(
                "Novo stop de VENDA não protege mais que o atual."
            )

        decisao["preco_stop_recomendado"] = round(
            stop,
            3
        )

    else:
        decisao["preco_stop_recomendado"] = None

    return decisao


def main():
    print("Iniciando agente operacional Gemini XAUUSD.")

    chave = os.getenv(
        "GEMINI_API_KEY",
        ""
    ).strip()

    if not chave:
        raise RuntimeError(
            "Secret GEMINI_API_KEY ausente."
        )

    pacote = carregar_entrada()

    modelo = carregar_modelo()

    print("Modelo:", modelo)
    print("Operação:", pacote["operacao"]["direcao"])
    print(
        "Candle analisado:",
        pacote["contexto"].get("candle_utc")
    )

    prompt = montar_prompt(pacote)

    resposta = consultar_gemini(
        prompt,
        modelo,
        chave
    )

    decisao = validar_decisao(
        resposta,
        pacote["operacao"]
    )

    resultado = {
        "status": "SUCESSO",
        "ativo": "XAUUSD",
        "modelo_utilizado": modelo,
        "candle_analisado": pacote["contexto"].get(
            "candle_utc"
        ),
        "operacao_direcao": pacote["operacao"]["direcao"],
        "decisao": decisao,
        "aviso": (
            "Análise consultiva. Nenhuma ordem foi executada. "
            "Qualquer alteração de stop exige confirmação manual."
        )
    }

    salvar_resultado(resultado)

    print("Análise operacional concluída.")
    print("Ação sugerida:", decisao["acao"])
    print("Confiança:", decisao["confianca"])
    print("Arquivo:", ARQUIVO_SAIDA)


if __name__ == "__main__":
    try:
        main()

    except Exception as erro:
        salvar_resultado({
            "status": "ERRO",
            "decisao": None,
            "motivo": str(erro),
            "aviso": (
                "Análise da IA indisponível. "
                "Não interpretar ausência de resposta como aprovação."
            )
        })

        print("ERRO NO AGENTE OPERACIONAL:", erro)
        sys.exit(1)
