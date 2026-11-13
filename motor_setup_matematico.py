
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MOTOR MATEMATICO XAUUSD

Fonte: data/graficos.db.gz
Parametros: data/sinais.db.gz
Gravacao: data/sinais.db.gz

Decisoes: LONG, SHORT ou LATERAL.

A seta e tratada como evento matematico.
O motor nao envia ordens e nao chama o Gemini.
"""

import json
from datetime import datetime, timedelta, timezone

from otimizador_parametros_setup import (
    SINAIS,
    JANELA,
    TIMEFRAMES,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
    carregar_leituras,
    pontuar,
    parse_data,
    criar_tabelas_setup,
)


def obter_ultima_leitura_fechada():
    leituras = carregar_leituras()
    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    fechadas = []

    for leitura in leituras:
        inicio = parse_data(leitura["timestamp"])
        fim = inicio + timedelta(minutes=TIMEFRAMES["M5"])

        if fim <= agora:
            fechadas.append(leitura)

    if not fechadas:
        raise RuntimeError("Nao existe vela M5 fechada disponivel.")

    ultima = fechadas[-1]

    return ultima["timestamp"], ultima["features"]


def decidir():
    timestamp, features = obter_ultima_leitura_fechada()

    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        criar_tabelas_setup(con)

        parametros = con.execute("""
            SELECT *
            FROM setup_parametros
            WHERE status='APROVADO'
            ORDER BY versao DESC
            LIMIT 1
        """).fetchone()

        if parametros is None:
            raise RuntimeError(
                "Nao existem parametros aprovados. "
                "O motor nao emitira evento."
            )

        pesos = json.loads(parametros["pesos_json"])

        configuracao = json.loads(parametros["features_json"])
        features_aprovadas = set(configuracao.get("indicadores", []))

        disponiveis = {
            nome: valor
            for nome, valor in features.items()
            if nome in features_aprovadas
        }

        if len(disponiveis) < 3:
            raise RuntimeError(
                "Indicadores validos insuficientes para a decisao."
            )

        resultado = pontuar(disponiveis, pesos)

        nota_long = resultado["nota_long"]
        nota_short = resultado["nota_short"]
        score_final = resultado["score_final"]

        gatilho_long = float(parametros["limite_long"])
        gatilho_short = abs(float(parametros["limite_short"]))

        if score_final >= gatilho_long:
            decisao = "LONG"
        elif score_final <= -gatilho_short:
            decisao = "SHORT"
        else:
            decisao = "LATERAL"

        # Evita duplicar o mesmo evento se o workflow for executado
        # novamente para a mesma vela e versao de parametros.
        existente = con.execute("""
            SELECT id
            FROM setup_decisoes
            WHERE timestamp_candle=?
              AND parametro_versao=?
            LIMIT 1
        """, (timestamp, parametros["versao"])).fetchone()

        if existente:
            print(json.dumps({
                "status": "JA_PROCESSADO",
                "timestamp_candle": timestamp,
                "decisao": decisao,
                "id_existente": existente["id"],
            }, ensure_ascii=False, indent=2))

            con.close()
            temporario.cleanup()
            return

        indicadores_json = {
            "janela_m5": JANELA,
            "nota_long": nota_long,
            "nota_short": nota_short,
            "score_final": score_final,
            "gatilho_long": gatilho_long,
            "gatilho_short": -gatilho_short,
            "indicadores": resultado["indicadores"],
            "indicadores_utilizados": sorted(disponiveis),
            "parametro_versao": parametros["versao"],
            "descricao_evento": (
                "Evento matematico detectado pela soma das notas "
                "independentes de compra e venda."
            ),
        }

        con.execute("""
            INSERT INTO setup_decisoes (
                criado_utc,
                timestamp_candle,
                decisao,
                pontuacao,
                limite,
                parametro_versao,
                indicadores_json,
                nota_long,
                nota_short,
                gatilho_long,
                gatilho_short
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            timestamp,
            decisao,
            score_final,
            gatilho_long if decisao != "SHORT" else -gatilho_short,
            parametros["versao"],
            json.dumps(indicadores_json, ensure_ascii=False),
            nota_long,
            nota_short,
            gatilho_long,
            -gatilho_short,
        ))

        con.commit()

        saida = {
            "status": "EVENTO_REGISTRADO",
            "timestamp_candle": timestamp,
            "decisao": decisao,
            "nota_long": nota_long,
            "nota_short": nota_short,
            "score_final": score_final,
            "gatilho_long": gatilho_long,
            "gatilho_short": -gatilho_short,
            "janela_m5": JANELA,
            "versao_parametros": parametros["versao"],
            "indicadores": resultado["indicadores"],
        }

        print(json.dumps(saida, ensure_ascii=False, indent=2))

        con.close()
        compactar_sqlite_gz(temporario, db_path, SINAIS)

    except Exception:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass
        temporario.cleanup()
        raise


if __name__ == "__main__":
    decidir()
