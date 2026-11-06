import copy
from datetime import datetime, timezone


VERSAO_MEMORIA = "1.0"
MAX_OBSERVACOES = 500


def agora_utc():
    return datetime.now(timezone.utc).isoformat()


def criar_memoria(diagnostico_original, contexto_central=None):
    """
    Guarda o diagnóstico integral recebido do Gemini.
    Não reduz o JSON aos campos de entrada, stop e alvo.
    """

    diagnostico = copy.deepcopy(diagnostico_original or {})

    if contexto_central is None:
        contexto_central = diagnostico.get("contexto_central")

    return {
        "versao": VERSAO_MEMORIA,
        "criada_em": agora_utc(),
        "diagnostico_original": diagnostico,
        "central_inicial": copy.deepcopy(contexto_central),
        "total_ciclos": 0,
        "ultima_observacao": None,
        "observacoes": []
    }


def validar_memoria(memoria):
    if not isinstance(memoria, dict):
        raise ValueError("Memória operacional inválida.")

    if "diagnostico_original" not in memoria:
        raise ValueError(
            "A memória não contém o diagnóstico original."
        )

    if "observacoes" not in memoria:
        memoria["observacoes"] = []

    if "total_ciclos" not in memoria:
        memoria["total_ciclos"] = len(
            memoria["observacoes"]
        )

    return memoria


def registrar_observacao(memoria, observacao):
    """
    Acrescenta uma observação por ciclo M5.
    Evita duplicar a mesma vela.
    """

    memoria = validar_memoria(memoria)
    nova = copy.deepcopy(observacao or {})

    horario = nova.get("timestamp")

    if not horario:
        raise ValueError(
            "A observação precisa conter timestamp."
        )

    ultima = memoria.get("ultima_observacao")

    if ultima == horario:
        return memoria, False

    observacoes = memoria["observacoes"]

    if observacoes:
        ultimo_horario = observacoes[-1].get("timestamp")

        if ultimo_horario == horario:
            memoria["ultima_observacao"] = horario
            return memoria, False

    nova["registrada_em"] = agora_utc()

    observacoes.append(nova)

    if len(observacoes) > MAX_OBSERVACOES:
        memoria["observacoes"] = observacoes[
            -MAX_OBSERVACOES:
        ]

    memoria["ultima_observacao"] = horario
    memoria["total_ciclos"] = int(
        memoria.get("total_ciclos", 0)
    ) + 1

    return memoria, True


def obter_diagnostico_original(memoria):
    memoria = validar_memoria(memoria)

    return copy.deepcopy(
        memoria["diagnostico_original"]
    )


def obter_central_inicial(memoria):
    memoria = validar_memoria(memoria)

    return copy.deepcopy(
        memoria.get("central_inicial")
    )


def obter_ultima_observacao(memoria):
    memoria = validar_memoria(memoria)

    observacoes = memoria.get("observacoes", [])

    if not observacoes:
        return None

    return copy.deepcopy(observacoes[-1])


def comparar_regioes(memoria, regiao_atual):
    """
    Compara a região inicial da Central com a região atual.
    """

    memoria = validar_memoria(memoria)

    central = memoria.get("central_inicial") or {}
    mercado_inicial = central.get("mercado_atual") or {}

    regiao_inicial = mercado_inicial.get("regiao")

    if regiao_inicial is None or regiao_atual is None:
        return {
            "disponivel": False,
            "motivo": "Região inicial ou atual indisponível."
        }

    diferenca = int(regiao_atual) - int(regiao_inicial)

    return {
        "disponivel": True,
        "regiao_inicial": int(regiao_inicial),
        "regiao_atual": int(regiao_atual),
        "diferenca": diferenca,
        "situacao": (
            "MESMA_REGIAO"
            if diferenca == 0
            else "REGIAO_SUPERIOR"
            if diferenca > 0
            else "REGIAO_INFERIOR"
        )
    }
