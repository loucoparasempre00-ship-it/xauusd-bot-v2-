"""
Auditoria dos sinais da V2.

Registra decisões para análise posterior.
Não envia mensagens nem executa operações.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


ARQUIVO_AUDITORIA = Path("dados_v2") / "auditoria_sinais.jsonl"


def registrar_evento(evento, caminho=ARQUIVO_AUDITORIA):
    """
    Acrescenta um evento ao histórico de auditoria.

    O evento deve ser um dicionário com dados serializáveis em JSON.
    Não inclua tokens, senhas ou chaves de API.
    """
    if not isinstance(evento, dict):
        raise ValueError("O evento precisa ser um dicionário.")

    registro = {
        "registrado_em": datetime.now(timezone.utc).isoformat(),
        "evento": evento,
    }

    arquivo = Path(caminho)
    arquivo.parent.mkdir(parents=True, exist_ok=True)

    linha = json.dumps(
        registro,
        ensure_ascii=False,
        allow_nan=False,
    )

    # Cada evento é acrescentado como uma linha JSON.
    # O arquivo é local; a persistência entre execuções será
    # definida na etapa de integração.
    with arquivo.open("a", encoding="utf-8") as f:
        f.write(linha + "\n")
        f.flush()
        os.fsync(f.fileno())

    return True


def ler_auditoria(caminho=ARQUIVO_AUDITORIA):
    """Lê os eventos gravados, preservando a ordem."""
    arquivo = Path(caminho)

    if not arquivo.exists():
        return []

    eventos = []

    with arquivo.open("r", encoding="utf-8") as f:
        for numero, linha in enumerate(f, start=1):
            if not linha.strip():
                continue

            try:
                eventos.append(json.loads(linha))
            except json.JSONDecodeError as erro:
                raise ValueError(
                    f"Registro inválido na linha {numero} da auditoria."
                ) from erro

    return eventos


if __name__ == "__main__":
    print("Módulo de auditoria carregado.")
    print("Nenhum evento foi registrado.")
    print("Não há envio de sinais nem execução de ordens.")
