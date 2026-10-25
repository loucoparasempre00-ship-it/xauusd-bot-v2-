from datetime import datetime, timedelta, timezone


IMPACTOS_VALIDOS = {"BAIXO", "MEDIO", "ALTO"}


def analisar_calendario(eventos, agora=None, janela_minutos=30):
    """
    Analisa eventos econômicos já fornecidos ao módulo.
    Espera horários ISO 8601 com fuso horário.
    Não busca notícias nem envia sinais.
    """

    if not isinstance(eventos, list):
        raise ValueError("Eventos devem ser fornecidos em uma lista.")

    if not isinstance(janela_minutos, int) or janela_minutos < 0:
        raise ValueError("A janela deve ser um inteiro não negativo.")

    if agora is None:
        agora = datetime.now(timezone.utc)

    if agora.tzinfo is None:
        raise ValueError("O horário atual precisa ter fuso horário.")

    inicio = agora.astimezone(timezone.utc)
    fim = inicio + timedelta(minutes=janela_minutos)

    proximos = []
    invalidos = []

    for i, evento in enumerate(eventos):
        if not isinstance(evento, dict):
            invalidos.append(i)
            continue

        nome = evento.get("nome")
        horario = evento.get("horario")
        impacto = str(evento.get("impacto", "")).upper()
        moeda = str(evento.get("moeda", "")).upper()

        if not isinstance(nome, str) or not nome.strip():
            invalidos.append(i)
            continue

        if not isinstance(horario, str):
            invalidos.append(i)
            continue

        if impacto not in IMPACTOS_VALIDOS:
            invalidos.append(i)
            continue

        try:
            data_evento = datetime.fromisoformat(
                horario.replace("Z", "+00:00")
            )
        except ValueError:
            invalidos.append(i)
            continue

        if data_evento.tzinfo is None:
            invalidos.append(i)
            continue

        data_evento = data_evento.astimezone(timezone.utc)

        if inicio <= data_evento <= fim:
            proximos.append({
                "nome": nome.strip(),
                "horario_utc": data_evento.isoformat(),
                "impacto": impacto,
                "moeda": moeda,
            })

    alto_impacto = [
        evento for evento in proximos
        if evento["impacto"] == "ALTO"
    ]

    return {
        "eventos_proximos": proximos,
        "alto_impacto_proximo": bool(alto_impacto),
        "eventos_alto_impacto": alto_impacto,
        "eventos_invalidos": invalidos,
        "fonte_consultada": False,
        "status": (
            "DADOS_INVALIDOS_PRESENTES"
            if invalidos
            else "ANALISE_PRELIMINAR"
        ),
        "bloqueia_sinal_automaticamente": False,
    }


if __name__ == "__main__":
    print("Modulo de calendario economico carregado.")
    print("Aguardando eventos fornecidos por uma fonte confiavel.")
    print("Sem consulta externa ou envio de sinais.")
