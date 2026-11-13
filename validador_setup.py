
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VALIDADOR DO SETUP MATEMATICO

Leitura: graficos.db.gz
Leitura e gravacao: sinais.db.gz

Nao consulta bancos de operacoes.
"""

import json
import os

from otimizador_parametros_setup import (
    SINAIS,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
    carregar_amostras,
    avaliar
)

MIN_SINAIS_VALIDACAO = 30


def validar():
    amostras = carregar_amostras()

    corte = int(len(amostras) * 0.70)
    validacao = amostras[corte:]

    if len(validacao) < MIN_SINAIS_VALIDACAO:
        raise RuntimeError(
            f"Amostras de validacao insuficientes: "
            f"{len(validacao)}"
        )

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
                "Tabela setup_parametros nao existe. "
                "Execute primeiro o otimizador."
            )

        linha = con.execute("""
            SELECT *
            FROM setup_parametros
            WHERE status='PENDENTE_VALIDACAO'
            ORDER BY versao DESC
            LIMIT 1
        """).fetchone()

        if linha is None:
            raise RuntimeError(
                "Nao existe versao pendente de validacao."
            )

        pesos = json.loads(linha["pesos_json"])
        treino = json.loads(linha["treino_json"])

        limite = float(linha["limite_long"])
        piso = float(treino["piso_movimento"])

        resultado = avaliar(
            validacao,
            pesos,
            limite,
            piso
        )

        # Comparacao com todos os indicadores usando peso 1.
        baseline = avaliar(
            validacao,
            {k: 1 for k in pesos},
            limite,
            piso
        )

        aprovado = (
            resultado["sinais"] >= MIN_SINAIS_VALIDACAO
            and resultado["taxa_direcional"] >= 0.50
            and resultado["taxa_direcional"]
            >= baseline["taxa_direcional"]
        )

        status = (
            "APROVADO"
            if aprovado
            else "REPROVADO"
        )

        relatorio = {
            **resultado,
            "baseline_pesos_iguais_1": baseline,
            "minimo_sinais": MIN_SINAIS_VALIDACAO,
            "criterio": (
                "taxa >= 50%, pelo menos igual ao baseline "
                "e amostra minima"
            ),
            "status": status
        }

        con.execute("""
            UPDATE setup_parametros
            SET status=?,
                validacao_json=?
            WHERE versao=?
        """, (
            status,
            json.dumps(
                relatorio,
                ensure_ascii=False
            ),
            linha["versao"]
        ))

        con.commit()

        versao = linha["versao"]

        # Se a nova versao falhar, preserva a anterior aprovada.
        tem_aprovado = con.execute("""
            SELECT 1
            FROM setup_parametros
            WHERE status='APROVADO'
            LIMIT 1
        """).fetchone() is not None

        print(json.dumps({
            "versao": versao,
            "validacao": relatorio
        }, ensure_ascii=False, indent=2))

        print("STATUS DA NOVA VERSAO:", status)
        print("EXISTE VERSAO APROVADA:", tem_aprovado)

        github_output = os.getenv("GITHUB_OUTPUT")

        if github_output:
            with open(
                github_output,
                "a",
                encoding="utf-8"
            ) as saida:
                saida.write(f"status={status}\n")
                saida.write(
                    f"motor={'sim' if tem_aprovado else 'nao'}\n"
                )

        con.close()

        compactar_sqlite_gz(
            temporario,
            db_path,
            SINAIS
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
    validar()
