import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PASTA_DADOS = Path("dados_v2")
ARQUIVO_ESTADO = PASTA_DADOS / "estado_sinais.json"
VERSAO_ESTADO = 1


def agora_utc():
    return datetime.now(timezone.utc).isoformat()


def estado_inicial():
    return {
        "versao": VERSAO_ESTADO,
        "atualizado_em": agora_utc(),
        "eventos_processados": [],
        "ultimo_sinal": None,
        "bot_ligado": False
    }


def carregar_estado(caminho=ARQUIVO_ESTADO):
    caminho = Path(caminho)

    if not caminho.exists():
        return estado_inicial()

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)
    except (OSError, json.JSONDecodeError) as erro:
        raise RuntimeError(
            f"Não foi possível ler o estado: {erro}"
        ) from erro

    if not isinstance(estado, dict):
        raise RuntimeError("Estado inválido: era esperado um objeto JSON.")

    if estado.get("versao") != VERSAO_ESTADO:
        raise RuntimeError("Versão do arquivo de estado incompatível.")

    eventos = estado.get("eventos_processados")
    if not isinstance(eventos, list):
        raise RuntimeError("Lista de eventos processados inválida.")

    return estado


def salvar_estado(estado, caminho=ARQUIVO_ESTADO):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    estado = dict(estado)
    estado["atualizado_em"] = agora_utc()

    temporario = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(caminho.parent),
            delete=False
        ) as arquivo:
            temporario = arquivo.name
            json.dump(
                estado,
                arquivo,
                ensure_ascii=False,
                indent=2
            )
            arquivo.flush()
            os.fsync(arquivo.fileno())

        os.replace(temporario, caminho)

    except OSError as erro:
        if temporario and os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass

        raise RuntimeError(
            f"Não foi possível salvar o estado: {erro}"
        ) from erro


def evento_ja_processado(chave, estado):
    return chave in estado["eventos_processados"]


def registrar_evento(chave, estado, limite=5000):
    if not isinstance(chave, str) or not chave.strip():
        raise ValueError("A chave do evento não pode estar vazia.")

    eventos = estado["eventos_processados"]

    if chave in eventos:
        return False

    eventos.append(chave)

    if len(eventos) > limite:
        estado["eventos_processados"] = eventos[-limite:]

    return True


def registrar_sinal(sinal, estado):
    if not isinstance(sinal, dict):
        raise ValueError("O sinal deve ser um dicionário.")

    estado["ultimo_sinal"] = {
        **sinal,
        "registrado_em": agora_utc()
    }


if __name__ == "__main__":
    print("Módulo de estado persistente V2 carregado.")
    print("Nenhuma API, corretora ou Telegram será acessado.")
