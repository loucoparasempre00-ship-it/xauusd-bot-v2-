
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MOTOR MATEMATICO DE SETUP

Leitura: graficos.db.gz e sinais.db.gz
Gravacao: sinais.db.gz

Decisoes:
LONG
SHORT
LATERAL

Nao utiliza Gemini nem bancos de operacoes.
"""

import json
from datetime import datetime, timedelta, timezone

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
    carregar_banco_graficos,
    construir_indices_indicadores,
    obter_indicador_fechado,
    extrair_features,
    pontuar,
    criar_tabelas_setup
)


def ultima_leitura():
    candles, indicadores = carregar_banco_graficos()

    candle = candles[-1]
    fechamento = float(candle["fechamento"])

    inicio = datetime.strptime(
        candle["timestamp"],
        "%Y-%m-%d %H:%M:%S"
    )

    instante_fechamento = inicio + timedelta(minutes=5)

    indices = construir_indices_indicadores(
        indicadores
    )

    linhas = {
        tf: obter_indicador_fechado(
            indices,
            tf,
            instante_fechamento
        )
        for tf in ("M5", "M15", "H1")
    }

    features = extrair_features(
        linhas,
        fechamento
    )

    if not features:
        raise RuntimeError(
            "Nenhum indicador valido para a ultima vela M5."
        )

    return candle["timestamp"], features


def decidir():
    timestamp, features = ultima_leitura()

    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        existe = con.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type='table'
            AND name='setup_parametros'
        """).fetchone()

        if not existe:
            raise RuntimeError(
                "Parametros nao encontrados. "
                "Execute otimizador e validador."
            )

        p = con.execute("""
            SELECT *
            FROM setup_parametros
            WHERE status='APROVADO'
            ORDER BY versao DESC
            LIMIT 1
        """).fetchone()

        if p is None:
            raise RuntimeError(
                "Nao existem parametros aprovados. "
                "Motor nao emitira decisao."
            )

        pesos = json.loads(p["pesos_json"])

        features_aprovadas = set(
            json.loads(p["features_json"])
        )

        disponiveis = {
            k: v
            for k, v in features.items()
            if k in features_aprovadas
        }

        score, usados = pontuar(
            disponiveis,
            pesos
        )

        if score is None:
            raise RuntimeError(
                "Nao ha indicadores validos para calcular."
            )

        limite = float(p["limite_long"])

        if score >= limite:
            decisao = "LONG"
        elif score <= -limite:
            decisao = "SHORT"
        else:
            decisao = "LATERAL"

        criar_tabelas_setup(con)

        con.execute("""
            INSERT INTO setup_decisoes (
                criado_utc,
                timestamp_candle,
                decisao,
                pontuacao,
                limite,
                parametro_versao,
                indicadores_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            timestamp,
            decisao,
            round(score, 6),
            limite,
            p["versao"],
            json.dumps({
                "utilizados": usados,
                "sem_leitura": sorted(
                    features_aprovadas - set(disponiveis)
                ),
                "indicadores_lidos": sorted(disponiveis)
            }, ensure_ascii=False)
        ))

        con.commit()

        resultado = {
            "timestamp_candle": timestamp,
            "decisao": decisao,
            "pontuacao": round(score, 6),
            "limite_long": limite,
            "limite_short": -limite,
            "versao_parametros": p["versao"],
            "indicadores": usados
        }

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS
        )

        print(json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2
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
    decidir()
