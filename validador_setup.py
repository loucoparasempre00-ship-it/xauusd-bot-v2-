
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VALIDADOR DO SETUP MATEMATICO

Leitura: graficos.db.gz
Leitura e gravacao: sinais.db.gz

Aprovacao independente para LONG e SHORT.
Nao consulta bancos de operacoes nem Gemini.
"""

import json
import os

from otimizador_parametros_setup import (
    SINAIS,
    MIN_SINAIS_TREINO,
    abrir_sqlite_gz,
    compactar_sqlite_gz,
    carregar_amostras,
    avaliar_direcao,
    criar_tabelas_setup,
)


MIN_SINAIS_VALIDACAO = 20
TAXA_MINIMA = 0.50


def validar():
    amostras = carregar_amostras()
    corte = int(len(amostras) * 0.70)

    validacao = amostras[corte:]

    if len(validacao) < MIN_SINAIS_VALIDACAO * 2:
        raise RuntimeError(
            f"Periodo de validacao insuficiente: {len(validacao)} amostras."
        )

    temporario, db_path, con = abrir_sqlite_gz(SINAIS)

    try:
        criar_tabelas_setup(con)

        linha = con.execute("""
            SELECT *
            FROM setup_parametros
            WHERE status='PENDENTE_VALIDACAO'
            ORDER BY versao DESC
            LIMIT 1
        """).fetchone()

        if linha is None:
            raise RuntimeError("Nao existe versao pendente de validacao.")

        limite_long = float(linha["limite_long"])
        limite_short = abs(float(linha["limite_short"]))

        resultado_long = avaliar_direcao(
            validacao, limite_long, "LONG"
        )

        resultado_short = avaliar_direcao(
            validacao, limite_short, "SHORT"
        )

        aprovado_long = (
            resultado_long["sinais"] >= MIN_SINAIS_VALIDACAO
            and resultado_long["taxa_acerto"] >= TAXA_MINIMA
        )

        aprovado_short = (
            resultado_short["sinais"] >= MIN_SINAIS_VALIDACAO
            and resultado_short["taxa_acerto"] >= TAXA_MINIMA
        )

        aprovado = aprovado_long and aprovado_short

        status = "APROVADO" if aprovado else "REPROVADO"

        relatorio = {
            "status": status,
            "janela_m5": 48,
            "gatilho_long": limite_long,
            "gatilho_short": -limite_short,
            "validacao_long": resultado_long,
            "validacao_short": resultado_short,
            "criterios": {
                "minimo_sinais_por_direcao": MIN_SINAIS_VALIDACAO,
                "taxa_minima_por_direcao": TAXA_MINIMA,
                "exige_aprovacao_long": True,
                "exige_aprovacao_short": True,
            },
        }

        con.execute("""
            UPDATE setup_parametros
            SET status=?, validacao_json=?
            WHERE versao=?
        """, (
            status,
            json.dumps(relatorio, ensure_ascii=False),
            linha["versao"],
        ))

        # Uma versao reprovada nunca substitui uma aprovada anterior.
        tem_aprovado = con.execute("""
            SELECT 1
            FROM setup_parametros
            WHERE status='APROVADO'
            LIMIT 1
        """).fetchone() is not None

        con.commit()

        print(json.dumps({
            "versao": linha["versao"],
            "relatorio": relatorio,
            "existe_versao_aprovada": tem_aprovado,
        }, ensure_ascii=False, indent=2))

        github_output = os.getenv("GITHUB_OUTPUT")

        if github_output:
            with open(github_output, "a", encoding="utf-8") as saida:
                saida.write(f"status={status}\n")
                saida.write(
                    f"motor={'sim' if tem_aprovado else 'nao'}\n"
                )

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
    validar()
