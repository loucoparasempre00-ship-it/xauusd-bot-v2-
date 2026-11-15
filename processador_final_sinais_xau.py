
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PROCESSADOR FINAL DE SINAIS XAUUSD

CLASSIFICACAO_GEMINI:
    Mantem aprovados e reprovados.
    Controla os envios ao Telegram.

RANKING_MELHORES:
    Uma linha por codigo.
    Resultado em USDT fica PENDENTE ate existir
    acompanhamento real do resultado.

Telegram:
    Envia somente APPROVED.
    Nao executa ordens.
    Inclui botao nativo para copiar o comando ENTREI.
    Informa fechamento do candle, horario de envio
    e atraso de processamento.
"""

import os
import re
import json
import sqlite3
import urllib.parse
import urllib.request
import urllib.error

from datetime import datetime, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
)


CLASSIFICACAO = "CLASSIFICACAO_GEMINI"
RANKING = "RANKING_MELHORES"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

CAMPOS_MENSAGEM = (
    "mensagem_completa",
    "mensagem_telegram",
    "texto_sinal",
    "sinal_formatado",
    "mensagem",
    "telegram_text",
    "texto_completo",
)


def agora():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def ident(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def colunas(con, tabela):
    return {
        r["name"]
        for r in con.execute(
            f"PRAGMA table_info({ident(tabela)})"
        ).fetchall()
    }


def garantir_coluna(con, tabela, nome, tipo):
    if nome not in colunas(con, tabela):
        con.execute(
            f"ALTER TABLE {ident(tabela)} "
            f"ADD COLUMN {ident(nome)} {tipo}"
        )


def criar_estrutura(con):
    tabelas = {
        r["name"]
        for r in con.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
        """).fetchall()
    }

    if CLASSIFICACAO not in tabelas:
        raise RuntimeError(
            "CLASSIFICACAO_GEMINI nao encontrada. "
            "Execute primeiro o Python do Gemini."
        )

    obrigatorias = {"codigo", "classificacao"}
    faltando = obrigatorias - colunas(con, CLASSIFICACAO)

    if faltando:
        raise RuntimeError(
            "Faltam colunas em CLASSIFICACAO_GEMINI: "
            + ", ".join(sorted(faltando))
        )

    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_status",
        "TEXT NOT NULL DEFAULT 'NAO_ENVIADO'"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_message_id", "TEXT"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_enviado_utc", "TEXT"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_erro", "TEXT"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_candle_fechamento_utc", "TEXT"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_atraso_segundos", "REAL"
    )
    garantir_coluna(
        con, CLASSIFICACAO,
        "telegram_comando_copia", "TEXT"
    )

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {ident(RANKING)} (
            codigo TEXT PRIMARY KEY,
            classificacao_gemini TEXT NOT NULL,
            direcao TEXT,
            entrada REAL,
            stop_loss REAL,
            take_profit REAL,
            pontuacao REAL,
            resultado_usdt REAL,
            status_resultado TEXT NOT NULL DEFAULT 'PENDENTE',
            mensagem_original TEXT,
            classificado_utc TEXT,
            atualizado_utc TEXT NOT NULL
        )
    """)

    con.commit()


def primeiro(registro, nomes):
    for nome in nomes:
        valor = registro.get(nome)
        if valor is not None and str(valor).strip():
            return valor
    return None


def numero(valor):
    if valor is None:
        return None
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def mensagem_original(sinal):
    for campo in CAMPOS_MENSAGEM:
        valor = sinal.get(campo)
        if valor is not None and str(valor).strip():
            return str(valor).strip()

    for campo in (
        "indicadores_json",
        "dados_json",
        "sinal_json",
    ):
        bruto = sinal.get(campo)

        if not bruto:
            continue

        try:
            dados = (
                json.loads(bruto)
                if isinstance(bruto, str)
                else bruto
            )
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(dados, dict):
            for nome in CAMPOS_MENSAGEM:
                valor = dados.get(nome)
                if valor is not None and str(valor).strip():
                    return str(valor).strip()

    return None


# --------------------------------------------------
# HORARIO DE FECHAMENTO E ATRASO
# --------------------------------------------------

def converter_utc(valor):
    if not valor:
        return None

    try:
        texto = str(valor).strip().replace("Z", "+00:00")
        data = datetime.fromisoformat(texto)

        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)

        return data.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return None


def fechamento_candle(sinal):
    campos = (
        "candle_fechamento_utc",
        "fechamento_candle_utc",
        "timestamp_fechamento_candle",
        "timestamp_candle_utc",
        "timestamp_candle",
        "candle_utc",
        "data_candle_utc",
    )

    valor = primeiro(sinal, campos)
    data = converter_utc(valor)

    if data:
        return data

    original = mensagem_original(sinal) or ""

    padrao = (
        r"(?:Candle UTC|Fechamento do candle UTC)"
        r"\s*:\s*"
        r"(\d{4}-\d{2}-\d{2}T"
        r"\d{2}:\d{2}:\d{2}"
        r"(?:\.\d+)?(?:Z|\+00:00)?)"
    )

    encontrado = re.search(
        padrao, original, re.IGNORECASE
    )

    if encontrado:
        return converter_utc(encontrado.group(1))

    return None


def formatar_horario(data):
    if not data:
        return "NAO INFORMADO"

    return data.strftime("%Y-%m-%d %H:%M:%S UTC")


def calcular_atraso(fechamento, envio):
    if not fechamento:
        return None

    return (envio - fechamento).total_seconds()


def formatar_atraso(segundos):
    if segundos is None:
        return "NAO DISPONIVEL"

    minutos = segundos / 60

    if segundos < 0:
        return (
            f"RELOGIO ANTERIOR AO FECHAMENTO "
            f"({segundos:.1f} segundos)"
        )

    return (
        f"{segundos:.1f} segundos "
        f"({minutos:.2f} minutos)"
    )


# --------------------------------------------------
# COMANDO DE COPIA
# --------------------------------------------------

def montar_comando_copia(sinal):
    codigo = str(sinal["codigo"]).strip()

    entrada = primeiro(
        sinal,
        (
            "entrada",
            "preco_entrada",
            "entry",
            "entry_price",
        )
    )

    if entrada is None:
        original = mensagem_original(sinal) or ""

        encontrado = re.search(
            r"Entrada\s*:\s*([0-9]+(?:[.,][0-9]+)?)",
            original,
            re.IGNORECASE,
        )

        if encontrado:
            entrada = encontrado.group(1)

    if entrada is None:
        raise RuntimeError(
            f"{codigo}: preco de entrada ausente. "
            "Botao de acompanhamento nao pode ser criado."
        )

    return f"ENTREI {str(entrada).strip()} {codigo}"


# --------------------------------------------------
# MONTAGEM DA MENSAGEM
# --------------------------------------------------

def montar_mensagem(sinal, envio_utc):
    codigo = str(sinal["codigo"])
    original = mensagem_original(sinal)

    fechamento = fechamento_candle(sinal)

    atraso = calcular_atraso(
        fechamento,
        envio_utc,
    )

    if original:
        texto = original

        if codigo not in original:
            texto = f"🆔 Código: {codigo}\n\n{original}"

    else:
        direcao = primeiro(
            sinal, ("direcao", "decisao", "sinal", "lado")
        )
        entrada = primeiro(
            sinal, ("entrada", "preco_entrada", "entry", "entry_price")
        )
        stop = primeiro(
            sinal, ("stop_loss", "stop", "sl")
        )
        alvo = primeiro(
            sinal, ("take_profit", "alvo", "tp", "target")
        )
        pontuacao = primeiro(
            sinal, ("pontuacao", "score", "score_final")
        )

        if not any(
            v is not None
            for v in (direcao, entrada, stop, alvo, pontuacao)
        ):
            raise RuntimeError(
                f"{codigo}: mensagem completa e dados operacionais "
                "ainda nao estao disponiveis. Envio mantido pendente."
            )

        linhas = [
            "🥇 XAUUSD · OURO",
            "⏱ Timeframe: M5",
            "",
            f"🆔 Código: {codigo}",
            "",
            str(direcao or "DIRECAO NAO INFORMADA"),
        ]

        if entrada is not None:
            linhas.append(f"💰 Entrada: {entrada}")
        if stop is not None:
            linhas.append(f"🛑 Stop: {stop}")
        if alvo is not None:
            linhas.append(f"🎯 Alvo: {alvo}")
        if pontuacao is not None:
            linhas.append(f"📊 Pontuacao: {pontuacao}")

        linhas.append(
            f"🧠 Gemini: {sinal.get('classificacao', 'APPROVED')}"
        )

        if sinal.get("justificativa"):
            linhas.extend([
                "",
                "📝 " + str(sinal["justificativa"])
            ])

        texto = "\n".join(linhas)

    comando = montar_comando_copia(sinal)

    linhas_temporais = [
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"🕯️ Fechamento do candle UTC: "
        f"{formatar_horario(fechamento)}",
        f"📨 Envio UTC: {formatar_horario(envio_utc)}",
        f"⏳ Atraso: {formatar_atraso(atraso)}",
        "",
        "📲 Comando para acompanhamento:",
        comando,
        "",
        "⚠️ Sinal informativo. Nenhuma ordem foi executada.",
    ]

    texto += "\n" + "\n".join(linhas_temporais)

    return texto, fechamento, atraso, comando


# --------------------------------------------------
# RANKING
# --------------------------------------------------

def sincronizar_ranking(con, sinal):
    codigo = str(sinal["codigo"])

    direcao = primeiro(
        sinal, ("direcao", "decisao", "sinal", "lado")
    )
    entrada = numero(primeiro(
        sinal, ("entrada", "preco_entrada", "entry", "entry_price")
    ))
    stop = numero(primeiro(
        sinal, ("stop_loss", "stop", "sl")
    ))
    alvo = numero(primeiro(
        sinal, ("take_profit", "alvo", "tp", "target")
    ))
    pontuacao = numero(primeiro(
        sinal, ("pontuacao", "score", "score_final")
    ))

    original = mensagem_original(sinal)

    con.execute(f"""
        INSERT INTO {ident(RANKING)} (
            codigo,
            classificacao_gemini,
            direcao,
            entrada,
            stop_loss,
            take_profit,
            pontuacao,
            resultado_usdt,
            status_resultado,
            mensagem_original,
            classificado_utc,
            atualizado_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'PENDENTE', ?, ?, ?)

        ON CONFLICT(codigo) DO UPDATE SET
            classificacao_gemini=excluded.classificacao_gemini,
            direcao=COALESCE(excluded.direcao, direcao),
            entrada=COALESCE(excluded.entrada, entrada),
            stop_loss=COALESCE(excluded.stop_loss, stop_loss),
            take_profit=COALESCE(excluded.take_profit, take_profit),
            pontuacao=COALESCE(excluded.pontuacao, pontuacao),
            mensagem_original=COALESCE(
                excluded.mensagem_original,
                mensagem_original
            ),
            classificado_utc=COALESCE(
                excluded.classificado_utc,
                classificado_utc
            ),
            atualizado_utc=excluded.atualizado_utc
    """, (
        codigo,
        str(sinal["classificacao"]).upper(),
        str(direcao) if direcao is not None else None,
        entrada,
        stop,
        alvo,
        pontuacao,
        original,
        sinal.get("classificado_utc"),
        agora(),
    ))


# --------------------------------------------------
# ENVIO TELEGRAM
# --------------------------------------------------

def enviar_telegram(texto, comando):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Secrets TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID ausentes."
        )

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    teclado = {
        "inline_keyboard": [[
            {
                "text": "📋 Copiar comando",
                "copy_text": {
                    "text": comando
                }
            }
        ]]
    }

    dados = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": texto,
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps(
            teclado,
            ensure_ascii=False,
        ),
    }).encode("utf-8")

    requisicao = urllib.request.Request(
        url,
        data=dados,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=30,
        ) as resposta:
            retorno = json.loads(
                resposta.read().decode("utf-8")
            )

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8", "replace"
        )[:1000]

        raise RuntimeError(
            f"Telegram HTTP {erro.code}: {detalhe}"
        )

    if not retorno.get("ok"):
        raise RuntimeError(
            "Telegram recusou a mensagem: "
            + json.dumps(retorno, ensure_ascii=False)[:1000]
        )

    resultado = retorno.get("result", {})

    return resultado.get("message_id")


# --------------------------------------------------
# PROCESSAMENTO
# --------------------------------------------------

def processar(con):
    sinais = con.execute(f"""
        SELECT *
        FROM {ident(CLASSIFICACAO)}
        ORDER BY classificado_utc ASC, codigo ASC
    """).fetchall()

    enviados = 0
    reprovados = 0
    pendentes = 0
    ranking_atualizado = 0
    erros = []

    for linha in sinais:
        sinal = dict(linha)

        codigo = str(
            sinal.get("codigo", "")
        ).strip()

        classificacao = str(
            sinal.get("classificacao", "")
        ).upper().strip()

        if not codigo:
            erros.append("Registro sem codigo ignorado.")
            continue

        if classificacao not in ("APPROVED", "REJECTED"):
            erros.append(
                f"{codigo}: classificacao invalida: {classificacao}"
            )
            continue

        sincronizar_ranking(con, sinal)
        ranking_atualizado += 1

        if classificacao == "REJECTED":
            reprovados += 1
            continue

        status = str(
            sinal.get("telegram_status") or "NAO_ENVIADO"
        ).upper()

        if status == "ENVIADO":
            continue

        try:
            envio_utc = datetime.now(timezone.utc)

            texto, fechamento, atraso, comando = (
                montar_mensagem(sinal, envio_utc)
            )

            if len(texto) > 4090:
                texto = texto[:4080] + "\n…"

            message_id = enviar_telegram(
                texto,
                comando,
            )

            con.execute(f"""
                UPDATE {ident(CLASSIFICACAO)}
                SET telegram_status='ENVIADO',
                    telegram_message_id=?,
                    telegram_enviado_utc=?,
                    telegram_erro=NULL,
                    telegram_candle_fechamento_utc=?,
                    telegram_atraso_segundos=?,
                    telegram_comando_copia=?
                WHERE codigo=?
            """, (
                str(message_id) if message_id is not None else None,
                envio_utc.isoformat(timespec="milliseconds"),
                fechamento.isoformat(timespec="milliseconds")
                if fechamento else None,
                atraso,
                comando,
                codigo,
            ))

            enviados += 1

            print(
                f"{codigo}: enviado ao Telegram. "
                f"Atraso: {formatar_atraso(atraso)}"
            )

        except Exception as erro:
            detalhe = str(erro)[:2000]

            con.execute(f"""
                UPDATE {ident(CLASSIFICACAO)}
                SET telegram_status='ERRO',
                    telegram_erro=?
                WHERE codigo=?
            """, (detalhe, codigo))

            pendentes += 1
            erros.append(f"{codigo}: {detalhe}")

            print(
                f"{codigo}: envio pendente - {detalhe}"
            )

    con.commit()

    return {
        "classificados": len(sinais),
        "ranking_atualizado": ranking_atualizado,
        "reprovados_mantidos": reprovados,
        "aprovados_enviados": enviados,
        "envios_pendentes": pendentes,
        "erros": erros,
    }


# --------------------------------------------------
# EXECUCAO
# --------------------------------------------------

def executar():
    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        con.row_factory = sqlite3.Row

        criar_estrutura(con)
        resultado = processar(con)

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS,
        )

        print("\nPROCESSAMENTO FINAL CONCLUIDO")

        print(json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        ))

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
