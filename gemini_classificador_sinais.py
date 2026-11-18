
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLASSIFICADOR E AUDITOR GEMINI XAUUSD

TRANSFERENCIA:
SINAIS_RECEBIDOS -> CLASSIFICACAO_GEMINI

BANCO OFICIAL:
data/sinais.db.gz

HISTORICO:
RANKING_MELHORES

- Consulta o historico real das operacoes.
- Nao utiliza JSON antigo de ranking.
- Nao confunde ranking de modelos com ranking de operacoes.
- Gemini propoe entrada, stop e alvo com base no fechamento M5 e no contexto.
- Preserva todos os campos originais do sinal.
- Registra nota, justificativa, relatorio e CSV.
- Remove da origem somente apos confirmar a gravacao.
- Nao envia Telegram.
- Nao executa ordens.
"""

import os
import sys
import json
import csv
import io
import urllib.request
import urllib.error
import sqlite3
import time

from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


ORIGEM = "SINAIS_RECEBIDOS"
DESTINO = "CLASSIFICACAO_GEMINI"
RANKING = "RANKING_MELHORES"

ARQUIVO_MODELO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "modelo_gemini.json",
)

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_BASE = (
    "https://generativelanguage.googleapis.com/v1beta"
)


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def ident(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def nomes_colunas(con, tabela):
    return {
        r["name"]
        for r in con.execute(
            f"PRAGMA table_info({ident(tabela)})"
        ).fetchall()
    }


def garantir_coluna(con, tabela, nome, tipo):
    if nome not in nomes_colunas(con, tabela):
        con.execute(
            f"ALTER TABLE {ident(tabela)} "
            f"ADD COLUMN {ident(nome)} {tipo}"
        )


def carregar_modelos(con):
    """Modelo escolhido seguido das reservas aprovadas pelo Testador."""
    if not os.path.isfile(ARQUIVO_MODELO):
        raise RuntimeError(
            "Arquivo data/modelo_gemini.json nao encontrado. "
            "Disponibilize o artefato do Testador Gemini."
        )
    with open(ARQUIVO_MODELO, "r", encoding="utf-8") as arquivo:
        dados = json.load(arquivo)

    status = str(dados.get("status", "")).lower()
    if status not in ("pronto", "pronto_com_aviso_api"):
        raise RuntimeError("Testador Gemini nao esta pronto. Status: " + status)

    escolhido = str(dados.get("modelo_escolhido") or "").strip()
    if not escolhido:
        raise RuntimeError("Modelo escolhido nao informado pelo Testador.")

    modelos = [escolhido]
    tabelas = {
        r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "SELECAO_MODELO_GEMINI" in tabelas:
        colunas = nomes_colunas(con, "SELECAO_MODELO_GEMINI")
        necessarias = {"atualizado_utc", "modelo", "sucessos", "posicao", "status_teste"}
        if necessarias.issubset(colunas):
            ultima = con.execute(
                "SELECT MAX(atualizado_utc) FROM SELECAO_MODELO_GEMINI"
            ).fetchone()[0]
            if ultima:
                reservas = con.execute("""
                    SELECT modelo, sucessos, status_teste
                    FROM SELECAO_MODELO_GEMINI
                    WHERE atualizado_utc = ?
                    ORDER BY posicao ASC
                """, (ultima,)).fetchall()
                for linha in reservas:
                    nome = str(linha["modelo"] or "").strip()
                    teste = str(linha["status_teste"] or "").lower()
                    sucessos = int(linha["sucessos"] or 0)
                    if nome.startswith("models/gemini-") and sucessos > 0 and teste in ("sucesso", "historico") and nome not in modelos:
                        modelos.append(nome)

    for item in dados.get("modelos_rankeados", []):
        nome = str(item.get("modelo") or "").strip()
        sucessos = int(item.get("sucessos") or 0)
        if nome.startswith("models/gemini-") and sucessos > 0 and nome not in modelos:
            modelos.append(nome)

    print("Modelos disponiveis em ordem:", modelos)
    return modelos


def criar_tabela(con):
    tabelas = {
        r["name"]
        for r in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if ORIGEM not in tabelas:
        raise RuntimeError(
            "Tabela SINAIS_RECEBIDOS nao encontrada."
        )

    origem = con.execute(
        f"PRAGMA table_info({ident(ORIGEM)})"
    ).fetchall()

    nomes_origem = {r["name"] for r in origem}

    obrigatorias = {"codigo", "origem_id"}

    if not obrigatorias.issubset(nomes_origem):
        raise RuntimeError(
            "SINAIS_RECEBIDOS precisa conter codigo e origem_id."
        )

    definicoes = [
        '"codigo" TEXT PRIMARY KEY',
        '"origem_id" INTEGER NOT NULL UNIQUE',
        '"classificacao" TEXT NOT NULL',
        '"justificativa" TEXT NOT NULL',
        '"modelo" TEXT NOT NULL',
        '"classificado_utc" TEXT NOT NULL',
        '"resposta_json" TEXT NOT NULL',
        '"nota_auditoria" REAL',
        '"relatorio_gemini" TEXT',
        '"csv_linha" TEXT',
        '"entrada" REAL',
        '"stop_loss" REAL',
        '"take_profit" REAL',
    ]

    for coluna in origem:
        nome = coluna["name"]

        if nome in (
            "codigo",
            "origem_id",
            "transferido_utc",
        ):
            continue

        tipo = coluna["type"] or "TEXT"

        definicoes.append(
            f"{ident(nome)} {tipo}"
        )

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ident(DESTINO)}
        ({", ".join(definicoes)})
        """
    )

    # Atualizacao aditiva: nao apaga registros existentes.
    garantir_coluna(
        con, DESTINO, "nota_auditoria", "REAL"
    )
    garantir_coluna(
        con, DESTINO, "relatorio_gemini", "TEXT"
    )
    garantir_coluna(
        con, DESTINO, "csv_linha", "TEXT"
    )
    garantir_coluna(con, DESTINO, "entrada", "REAL")
    garantir_coluna(con, DESTINO, "stop_loss", "REAL")
    garantir_coluna(con, DESTINO, "take_profit", "REAL")
    garantir_coluna(con, DESTINO, "preco_fechamento", "REAL")

    destino = nomes_colunas(con, DESTINO)

    necessarias = {
        "codigo",
        "origem_id",
        "classificacao",
        "justificativa",
        "modelo",
        "classificado_utc",
        "resposta_json",
    }

    faltando = necessarias - destino

    if faltando:
        raise RuntimeError(
            "Estrutura incompativel em CLASSIFICACAO_GEMINI: "
            + ", ".join(sorted(faltando))
        )

    con.commit()

    return origem


def numero(valor):
    if valor is None:
        return None

    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def consultar_historico(con):
    """
    Consulta somente operacoes com resultado conhecido.
    Operacoes PENDENTES nao entram nas estatisticas.
    """

    tabelas = {
        r["name"]
        for r in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if RANKING not in tabelas:
        return {
            "disponibilidade": "TABELA_AINDA_NAO_EXISTE",
            "total_encerradas": 0,
            "total_pendentes": 0,
            "estatisticas": {},
            "melhores_resultados": [],
            "piores_resultados": [],
        }

    colunas = nomes_colunas(con, RANKING)

    obrigatorias = {
        "codigo",
        "classificacao_gemini",
        "resultado_usdt",
        "status_resultado",
    }

    if not obrigatorias.issubset(colunas):
        return {
            "disponibilidade": "ESTRUTURA_INCOMPLETA",
            "colunas_encontradas": sorted(colunas),
            "total_encerradas": 0,
            "total_pendentes": 0,
            "estatisticas": {},
            "melhores_resultados": [],
            "piores_resultados": [],
        }

    registros = con.execute(
        f"SELECT * FROM {ident(RANKING)}"
    ).fetchall()

    encerradas = []
    pendentes = 0

    for linha in registros:
        r = dict(linha)
        resultado = numero(r.get("resultado_usdt"))
        status = str(
            r.get("status_resultado") or ""
        ).upper()

        if resultado is None or status == "PENDENTE":
            pendentes += 1
            continue

        encerradas.append(r)

    estatisticas = {}

    for classificacao in ("APPROVED", "REJECTED"):
        grupo = [
            r for r in encerradas
            if str(
                r.get("classificacao_gemini", "")
            ).upper() == classificacao
        ]

        valores = [
            numero(r.get("resultado_usdt"))
            for r in grupo
        ]

        valores = [v for v in valores if v is not None]

        positivos = sum(1 for v in valores if v > 0)
        negativos = sum(1 for v in valores if v < 0)

        estatisticas[classificacao] = {
            "total_encerradas": len(valores),
            "positivas": positivos,
            "negativas": negativos,
            "resultado_medio_usdt": (
                round(sum(valores) / len(valores), 4)
                if valores else None
            ),
            "resultado_total_usdt": round(
                sum(valores), 4
            ),
            "taxa_positiva_percentual": (
                round(positivos / len(valores) * 100, 2)
                if valores else None
            ),
        }

    ordenadas = sorted(
        encerradas,
        key=lambda r: (
            numero(r.get("resultado_usdt"))
            if numero(r.get("resultado_usdt")) is not None
            else 0
        ),
        reverse=True,
    )

    def resumir(r):
        campos = (
            "codigo",
            "classificacao_gemini",
            "direcao",
            "entrada",
            "stop_loss",
            "take_profit",
            "resultado_usdt",
            "status_resultado",
        )

        return {
            campo: r.get(campo)
            for campo in campos
            if campo in r
        }

    return {
        "disponibilidade": "DADOS_REAIS_CONSULTADOS",
        "total_encerradas": len(encerradas),
        "total_pendentes": pendentes,
        "estatisticas": estatisticas,
        "melhores_resultados": [
            resumir(r) for r in ordenadas[:10]
        ],
        "piores_resultados": [
            resumir(r) for r in ordenadas[-10:]
        ],
    }


def montar_prompt(sinal, historico):
    dados_sinal = dict(sinal)
    dados_sinal.pop("transferido_utc", None)

    return f"""
Voce e o AUDITOR GEMINI de sinais XAUUSD.

Analise o sinal com independencia de raciocinio. Use os dados
recebidos, interprete o contexto e forme sua propria conclusao.
O motor matematico e uma fonte de informacao, nao uma ordem.

LIBERDADE ANALITICA:
- Nao siga lista fixa de padroes nem categorias obrigatorias.
- Nao exija alinhamento perfeito entre M5, M15 e H1.
- Divergencia entre tempos graficos nao gera penalidade automatica.
- Nao use pesos fixos de indicadores nem soma obrigatoria.
- Identifique estruturas, comportamentos e padroes novos,
  desde que sustentados pelos dados recebidos.
- Pode concordar ou discordar do motor matematico, explicando por que.
- Avalie contexto, oportunidade, incerteza e risco de forma integrada.
- Nao aprove ou rejeite apenas para atingir uma nota ou faixa.
- A nota e um registro da avaliacao, nao uma formula que determina
  automaticamente a classificacao.

REGRAS OBRIGATORIAS DE SEGURANCA, INTEGRIDADE E REGISTRO:
- Use somente dados presentes no sinal e no historico.
- Nunca invente cotacoes, indicadores, probabilidades ou resultados.
- Proponha entrada, stop_loss e take_profit para o sinal, usando o fechamento M5 recebido como referencia de mercado. Esses tres precos sao sua responsabilidade analitica.
- Nao execute ordens nem solicite execucao automatica.
- Justifique claramente decisao, evidencias, incertezas e limitacoes.
- Registre classificacao, nota, modelo e relatorio.
- Considere somente resultados historicos conhecidos; pendentes
  nao sao vitorias nem derrotas.
- Compare aprovados e rejeitados quando houver amostra, sem
  inventar padroes nem contar o mesmo resultado duas vezes.

PRECOS DO SETUP (CAMPOS OBRIGATORIOS):
Voce DEVE calcular e preencher entrada, stop_loss e take_profit.
Os tres campos devem conter numeros JSON positivos (ex.: 4021.50), nunca null, nunca texto e nunca ser omitidos, inclusive quando a classificacao for REJECTED.
Use preco_fechamento como referencia observada. Nao invente cotacao atual.
Escolha os precos de acordo com o contexto real recebido e com a direcao do sinal. Nao use margem, ATR, RR ou outro parametro fixo obrigatorio. Se os dados forem insuficientes para um setup confiavel, ainda assim informe uma proposta numerica conservadora coerente com os dados disponiveis e explique a incerteza no relatorio. Nao aprove o sinal apenas por ter preenchido os precos.

Responda exclusivamente em JSON valido. Os exemplos numericos abaixo indicam o TIPO de dado esperado; substitua-os pelos precos calculados:
{{
  "classificacao": "APPROVED ou REJECTED",
  "nota_auditoria": 0,
  "entrada": 4021.50,
  "stop_loss": 4028.00,
  "take_profit": 4011.75,
  "justificativa": "Motivo objetivo e contextualizado",
  "condicao_lote": "NORMAL, REDUZIDO ou VETADO",
  "pontuacao": {{}},
  "risco": {{
    "entrada_original": null,
    "stop_original": null,
    "alvo_original": null,
    "rr_tp1": null,
    "rr_tp2": null,
    "dados_suficientes": false
  }},
  "relatorio": "Analise completa, contexto, evidencias, riscos e incertezas"
}}

A nota deve ser numerica, entre 0 e 100, coerente com sua
avaliacao. Nao existe soma obrigatoria nem limite de nota que
force a classificacao. A pontuacao pode usar criterios livres.

DADOS DO SINAL:
{json.dumps(dados_sinal, ensure_ascii=False, default=str)}

HISTORICO REAL DE OPERACOES:
{json.dumps(historico, ensure_ascii=False, default=str)}
"""

def requisicao_gemini(modelo, sinal, historico):
    if not GEMINI_KEY:
        raise RuntimeError("Secret GEMINI_API_KEY ausente.")

    prompt = montar_prompt(sinal, historico)

    corpo = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 1800,
        },
    }

    url = f"{GEMINI_BASE}/{modelo}:generateContent"

    requisicao = urllib.request.Request(
        url,
        data=json.dumps(
            corpo,
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=60,
        ) as resposta:
            retorno = json.loads(
                resposta.read().decode("utf-8")
            )

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8", "replace"
        )[:1000]

        raise RuntimeError(
            f"Gemini HTTP {erro.code}: {detalhe}"
        )

    partes = (
        retorno.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )

    texto = "".join(
        parte.get("text", "")
        for parte in partes
    ).strip()

    if not texto:
        raise RuntimeError("Gemini retornou resposta vazia.")

    if texto.startswith("```"):
        texto = texto.replace("```json", "", 1)
        texto = texto.replace("```", "").strip()

    try:
        resultado = json.loads(texto)
    except json.JSONDecodeError:
        raise RuntimeError(
            "Gemini nao retornou JSON valido: "
            + texto[:1000]
        )

    classificacao = str(
        resultado.get("classificacao", "")
    ).upper().strip()

    if classificacao not in ("APPROVED", "REJECTED"):
        raise RuntimeError(
            "Classificacao invalida retornada pelo Gemini."
        )

    justificativa = str(
        resultado.get("justificativa", "")
    ).strip()

    if not justificativa:
        raise RuntimeError(
            "Gemini nao informou a justificativa."
        )

    nota = numero(resultado.get("nota_auditoria"))

    if nota is None or not 0 <= nota <= 100:
        raise RuntimeError(
            "Nota de auditoria invalida."
        )

    pontuacao = resultado.get("pontuacao", {})
    if pontuacao is None:
        pontuacao = {}
    if not isinstance(pontuacao, dict):
        raise RuntimeError("Pontuacao de auditoria invalida.")

    # Pontuacao livre e explicativa: sem categorias, pesos,
    # soma obrigatoria ou faixas que forcem a classificacao.

    entrada = numero(resultado.get("entrada"))
    stop_loss = numero(resultado.get("stop_loss"))
    take_profit = numero(resultado.get("take_profit"))
    direcao = str(sinal.get("direcao") or sinal.get("decisao") or "").upper().strip()
    if any(v is None or v <= 0 for v in (entrada, stop_loss, take_profit)):
        raise RuntimeError("Gemini nao forneceu entrada, stop_loss e take_profit validos.")
    if direcao == "LONG" and not (stop_loss < entrada < take_profit):
        raise RuntimeError("Precos invalidos para LONG: esperado stop_loss < entrada < take_profit.")
    if direcao == "SHORT" and not (take_profit < entrada < stop_loss):
        raise RuntimeError("Precos invalidos para SHORT: esperado take_profit < entrada < stop_loss.")
    if direcao not in ("LONG", "SHORT"):
        raise RuntimeError("Direcao do sinal nao permite montar precos: " + direcao)

    risco = resultado.get("risco", {})
    relatorio = str(
        resultado.get("relatorio", "")
    ).strip()

    if not relatorio:
        raise RuntimeError(
            "Relatorio da avaliacao nao informado."
        )

    return {
        "classificacao": classificacao,
        "justificativa": justificativa[:3000],
        "nota_auditoria": nota,
        "entrada": entrada,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "relatorio": relatorio[:12000],
        "pontuacao": pontuacao,
        "risco": risco,
        "resposta_json": json.dumps(
            retorno,
            ensure_ascii=False,
        ),
    }


def gerar_csv_linha(sinal, resultado, modelo):
    """
    O CSV e produzido pelo sistema, nao pelo Gemini.
    """

    campos = [
        "codigo",
        "classificacao",
        "nota_auditoria",
        "modelo",
        "justificativa",
        "mtf_medias",
        "bordas",
        "rsi_macd",
        "probabilidade",
        "ranking_historico",
        "condicao_lote",
        "classificado_utc",
    ]

    dados = {
        "codigo": sinal.get("codigo"),
        "classificacao": resultado["classificacao"],
        "nota_auditoria": resultado["nota_auditoria"],
        "modelo": modelo,
        "justificativa": resultado["justificativa"],
        "mtf_medias": resultado["pontuacao"].get("mtf_medias"),
        "bordas": resultado["pontuacao"].get("bordas"),
        "rsi_macd": resultado["pontuacao"].get("rsi_macd"),
        "probabilidade": resultado["pontuacao"].get("probabilidade"),
        "ranking_historico": resultado["pontuacao"].get("ranking_historico"),
        "condicao_lote": resultado.get("risco", {}).get("condicao_lote"),
        "classificado_utc": agora(),
    }

    saida = io.StringIO()
    escritor = csv.DictWriter(
        saida,
        fieldnames=campos,
        extrasaction="ignore",
    )

    escritor.writeheader()
    escritor.writerow(dados)

    return saida.getvalue().strip()


def transferir_classificacao(
    con,
    sinal,
    resultado,
    modelo,
    colunas_origem,
):
    codigo = str(sinal["codigo"])

    con.execute("BEGIN IMMEDIATE")

    existente = con.execute("""
        SELECT codigo, origem_id
        FROM CLASSIFICACAO_GEMINI
        WHERE codigo = ?
    """, (codigo,)).fetchone()

    if existente:
        if existente["origem_id"] != sinal["origem_id"]:
            raise RuntimeError(
                f"Codigo {codigo} pertence a outro sinal."
            )
    else:
        nomes = [
            "codigo",
            "origem_id",
            "classificacao",
            "justificativa",
            "modelo",
            "classificado_utc",
            "resposta_json",
            "nota_auditoria",
            "relatorio_gemini",
            "csv_linha",
            "entrada",
            "stop_loss",
            "take_profit",
        ]

        data_classificacao = agora()

        valores = [
            codigo,
            sinal["origem_id"],
            resultado["classificacao"],
            resultado["justificativa"],
            modelo,
            data_classificacao,
            resultado["resposta_json"],
            resultado["nota_auditoria"],
            resultado["relatorio"],
            gerar_csv_linha(sinal, resultado, modelo),
            resultado["entrada"],
            resultado["stop_loss"],
            resultado["take_profit"],
        ]

        for coluna in colunas_origem:
            nome = coluna["name"]

            if nome in (
                "codigo",
                "origem_id",
                "transferido_utc",
                "entrada",
                "stop_loss",
                "take_profit",
            ):
                continue

            nomes.append(nome)
            valores.append(sinal[nome])

        con.execute(
            f"""
            INSERT INTO {ident(DESTINO)}
            ({", ".join(ident(n) for n in nomes)})
            VALUES ({", ".join("?" for _ in valores)})
            """,
            valores,
        )

    confirmado = con.execute("""
        SELECT codigo, origem_id, classificacao
        FROM CLASSIFICACAO_GEMINI
        WHERE codigo = ?
    """, (codigo,)).fetchone()

    if (
        confirmado is None
        or confirmado["origem_id"] != sinal["origem_id"]
    ):
        raise RuntimeError(
            f"Falha ao confirmar gravacao de {codigo}."
        )

    removido = con.execute("""
        DELETE FROM SINAIS_RECEBIDOS
        WHERE codigo = ?
          AND origem_id = ?
    """, (
        codigo,
        sinal["origem_id"],
    ))

    if removido.rowcount != 1:
        raise RuntimeError(
            f"Falha ao retirar {codigo} da origem."
        )

    con.commit()


def executar():
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        con.row_factory = sqlite3.Row

        colunas_origem = criar_tabela(con)
        modelos = carregar_modelos(con)

        sinais = con.execute("""
            SELECT *
            FROM SINAIS_RECEBIDOS
            ORDER BY codigo ASC
        """).fetchall()

        print("Modelo principal:", modelos[0])
        print("Sinais aguardando Gemini:", len(sinais))

        transferidos = 0
        erro_processamento = None

        for linha in sinais:
            sinal = dict(linha)
            codigo = sinal["codigo"]

            try:
                print("\nConsultando historico para:", codigo)
                historico = consultar_historico(con)
                resultado = None
                modelo_usado = None
                falhas = []

                # Tres tentativas totais. Prioriza o modelo escolhido
                # e avanca para os proximos aprovados no ranking.
                sequencia = modelos[:3]
                while len(sequencia) < 3:
                    sequencia.append(modelos[len(sequencia) % len(modelos)])

                for tentativa, modelo_tentativa in enumerate(sequencia, start=1):
                    try:
                        print("Tentativa", tentativa, "de 3:", modelo_tentativa)
                        resultado = requisicao_gemini(
                            modelo_tentativa, sinal, historico
                        )
                        modelo_usado = modelo_tentativa
                        break
                    except Exception as erro_modelo:
                        detalhe = str(erro_modelo)
                        falhas.append(f"{modelo_tentativa}: {detalhe}")
                        print("Falha:", detalhe)
                        if tentativa < 3:
                            print("Aguardando 4 segundos para a proxima tentativa...")
                            time.sleep(4)

                if resultado is None:
                    raise RuntimeError(
                        "As tres tentativas falharam. " + " | ".join(falhas)
                    )

                transferir_classificacao(
                    con, sinal, resultado, modelo_usado, colunas_origem
                )
                transferidos += 1
                print(
                    codigo, "=>", resultado["classificacao"],
                    "| Nota:", resultado["nota_auditoria"],
                    "| Modelo:", modelo_usado, "| TRANSFERIDO",
                )

            except Exception as erro:
                con.rollback()
                erro_processamento = f"{codigo}: {str(erro)}"
                print("SINAL PRESERVADO NA ORIGEM:", erro_processamento)
                break

        restantes = con.execute("""
            SELECT COUNT(*)
            FROM SINAIS_RECEBIDOS
        """).fetchone()[0]

        total_destino = con.execute("""
            SELECT COUNT(*)
            FROM CLASSIFICACAO_GEMINI
        """).fetchone()[0]

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS,
        )

        print("\nCLASSIFICACAO GEMINI CONCLUIDA")
        print("Novos classificados:", transferidos)
        print("Total em CLASSIFICACAO_GEMINI:", total_destino)
        print("Sinais restantes na origem:", restantes)

        if erro_processamento:
            raise RuntimeError(
                "Processamento interrompido. "
                "O sinal com erro permaneceu na origem. "
                + erro_processamento
            )

    except Exception:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass

        temporario.cleanup()
        raise


if __name__ == "__main__":
    executar()
