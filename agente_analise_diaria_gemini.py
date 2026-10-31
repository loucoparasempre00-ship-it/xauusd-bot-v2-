
import csv
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://generativelanguage.googleapis.com/v1beta"
DATA = Path("data")
DATA.mkdir(exist_ok=True)

agora = datetime.now(timezone.utc)
dia_analisado = (agora.date() - timedelta(days=1)).isoformat()
chave = os.getenv("GEMINI_API_KEY", "").strip()


def ler_csv_diario(path):
    if not path.exists() or path.stat().st_size == 0:
        return None

    registros = []

    try:
        with path.open(
            encoding="utf-8-sig",
            newline=""
        ) as arquivo:
            leitor = csv.DictReader(arquivo)

            for linha in leitor:
                texto = " ".join(
                    str(valor or "")
                    for valor in linha.values()
                )

                if dia_analisado in texto:
                    registros.append(linha)

        return registros

    except Exception as erro:
        return {"erro_leitura": str(erro)}


def ler_json(path):
    if not path.exists() or path.stat().st_size == 0:
        return None

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception as erro:
        return {"erro_leitura": str(erro)}


arquivos_csv = [
    "historico_velas_m5.csv",
    "eventos_bordas_m5.csv",
    "resultados_eventos_m5.csv",
    "resultados_operacoes.csv",
    "memoria_agente.csv",
    "tabuleiro_m5.csv",
    "estatisticas_regioes_m5.csv",
    "transicoes_probabilidade_m5.csv",
    "sequencias_provaveis_m5.csv",
    "resumo_resultados_m5.csv",
    "relatorio_geral_tabuleiro_m5.csv"
]

arquivos_json = [
    "memoria_agente.json",
    "resultados_operacoes.json",
    "relatorio_tabuleiro_m5.json",
    "proximas_regioes_m5.json"
]

biblioteca = {}
total_registros = 0

for nome in arquivos_csv:
    conteudo = ler_csv_diario(DATA / nome)

    if conteudo is not None:
        biblioteca[nome] = conteudo

        if isinstance(conteudo, list):
            total_registros += len(conteudo)

for nome in arquivos_json:
    conteudo = ler_json(DATA / nome)

    if conteudo is not None:
        biblioteca[nome] = conteudo


# Primeiro ciclo ou dia sem registros:
# não gasta chamada Gemini.
if total_registros == 0:
    resultado = {
        "status": "sem_dados_do_dia",
        "dia_utc_analisado": dia_analisado,
        "gerado_utc": agora.isoformat(),
        "mensagem": (
            "Não foram encontrados registros CSV "
            "para o dia UTC encerrado."
        )
    }

    (DATA / "analise_diaria_gemini.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    sys.exit(0)


if not chave:
    sys.exit("Secret GEMINI_API_KEY ausente.")


# Usa o modelo escolhido pelo testador já existente.
arquivo_modelo = DATA / "modelo_gemini.json"

if not arquivo_modelo.exists():
    sys.exit("Arquivo modelo_gemini.json não encontrado.")

selecao = json.loads(
    arquivo_modelo.read_text(encoding="utf-8")
)

modelo = selecao.get("modelo_escolhido")

if selecao.get("status") != "pronto" or not modelo:
    sys.exit("Nenhum modelo Gemini validado disponível.")


prompt = f"""
Você é o agente de análise retrospectiva diária do projeto XAUUSD M5.

DATA UTC ANALISADA: {dia_analisado}

Sua função é ler a biblioteca do robô e tirar conclusões próprias
sobre o comportamento do mercado no dia encerrado.

Os arquivos CSV foram filtrados para conter registros que mencionam
a data UTC analisada.

Os arquivos JSON são relatórios e memórias persistentes da biblioteca.
Considere a possibilidade de conterem informações de outros dias.

ANALISE:

1. Comportamento do mercado durante o dia.
2. Movimentos de alta, baixa, lateralização e mudanças de direção.
3. Eventos, bordas, regiões e transições observadas.
4. Resultados de operações e sinais, distinguindo:
   - resultado confirmado;
   - operação sem resultado;
   - operação aberta;
   - ausência de sinal.
5. Padrões que parecem relevantes.
6. Padrões que ainda não possuem amostras suficientes.
7. Aprendizados que podem ajudar o agente de sinais nas próximas velas.

Não invente preços, horários, resultados ou estatísticas.
Não transforme correlação em causalidade.
Não trate ausência de dados como resultado negativo.
Não altere os parâmetros do motor de sinais.
Não envie ordens.
Não precisa concordar com conclusões anteriores da biblioteca.
Você pode discordar delas quando os dados justificarem.

Se não houver evidência suficiente, diga claramente.

Responda exclusivamente em JSON válido:

{{
  "dia_utc_analisado": "{dia_analisado}",
  "resumo_do_dia": "",
  "observacoes_relevantes": [],
  "resultados_evidenciados": [],
  "limitacoes_dos_dados": [],
  "aprendizados_para_o_agente_de_sinais": [],
  "pontos_a_observar_no_proximo_dia": [],
  "confianca_da_analise": "baixa"
}}

BIBLIOTECA:

{json.dumps(biblioteca, ensure_ascii=False, default=str)}
"""


corpo = {
    "contents": [
        {
            "parts": [
                {"text": prompt}
            ]
        }
    ],
    "generationConfig": {
        "temperature": 0.25,
        "maxOutputTokens": 1800,
        "responseMimeType": "application/json"
    }
}

requisicao = urllib.request.Request(
    f"{BASE}/{modelo}:generateContent",
    data=json.dumps(corpo).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "x-goog-api-key": chave
    },
    method="POST"
)

try:
    with urllib.request.urlopen(
        requisicao,
        timeout=90
    ) as resposta_http:

        resposta = json.loads(
            resposta_http.read().decode("utf-8")
        )

    texto = (
        resposta["candidates"][0]
        ["content"]["parts"][0]["text"]
    )

    analise = json.loads(texto)

except urllib.error.HTTPError as erro:
    detalhe = erro.read().decode(
        "utf-8",
        "replace"
    )

    sys.exit(
        f"Gemini HTTP {erro.code}: {detalhe[:1200]}"
    )

except Exception as erro:
    sys.exit(
        f"Falha na análise diária: {erro}"
    )


resultado = {
    "status": "concluido",
    "ativo": "XAUUSD",
    "timeframe": "M5",
    "gerado_utc": datetime.now(timezone.utc).isoformat(),
    "modelo_utilizado": modelo,
    "registros_diarios_encontrados": total_registros,
    "analise": analise
}

saida = DATA / "analise_diaria_gemini.json"

saida.write_text(
    json.dumps(resultado, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(json.dumps(resultado, ensure_ascii=False, indent=2))
