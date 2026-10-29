import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_JSON = DATA / "memoria_agente.json"
ARQUIVO_CSV = DATA / "memoria_agente.csv"

CAMPOS = [
    "candle_utc",
    "sinal",
    "decisao",
    "entrada",
    "stop",
    "gain",
    "confianca",
    "pontuacao_final",
    "modelo_utilizado",
    "justificativa",
    "resultado",
    "registrado_utc",
]


def carregar_json():
    if not ARQUIVO_JSON.exists():
        print(f"Memoria JSON ainda nao encontrada: {ARQUIVO_JSON}")
        return []

    try:
        with ARQUIVO_JSON.open("r", encoding="utf-8") as arquivo:
            memoria = json.load(arquivo)
    except (OSError, json.JSONDecodeError) as erro:
        raise RuntimeError(f"Nao foi possivel ler a memoria JSON: {erro}")

    if isinstance(memoria, list):
        historico = memoria
    elif isinstance(memoria, dict):
        historico = memoria.get("historico", [])
    else:
        historico = []

    if not isinstance(historico, list):
        raise ValueError("O campo historico da memoria nao e uma lista.")

    return [item for item in historico if isinstance(item, dict)]


def carregar_csv_existente():
    registros = {}

    if not ARQUIVO_CSV.exists():
        return registros

    with ARQUIVO_CSV.open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")

        for linha in leitor:
            candle = str(linha.get("candle_utc") or "").strip()
            decisao = str(
                linha.get("decisao") or linha.get("sinal") or ""
            ).strip().upper()

            if not candle:
                continue

            chave = (candle, decisao)

            registros[chave] = {
                campo: linha.get(campo, "") or ""
                for campo in CAMPOS
            }

    return registros


def normalizar_registro(item):
    sinal = str(
        item.get("decisao", item.get("sinal", "SEM_SINAL"))
    ).strip().upper()

    if sinal not in ("COMPRA", "VENDA", "SEM_SINAL"):
        sinal = "SEM_SINAL"

    return {
        "candle_utc": str(item.get("candle_utc") or "").strip(),
        "sinal": str(item.get("sinal") or sinal).strip().upper(),
        "decisao": sinal,
        "entrada": item.get("entrada", ""),
        "stop": item.get("stop", item.get("stop_loss", "")),
        "gain": item.get(
            "gain",
            item.get("take_profit", item.get("alvo", ""))
        ),
        "confianca": item.get(
            "confianca",
            item.get("confianca_declarada", "")
        ),
        "pontuacao_final": item.get("pontuacao_final", ""),
        "modelo_utilizado": item.get("modelo_utilizado", ""),
        "justificativa": item.get("justificativa", ""),
        "resultado": item.get("resultado", ""),
        "registrado_utc": item.get("registrado_utc", ""),
    }


def salvar_csv(registros):
    DATA.mkdir(parents=True, exist_ok=True)

    ordenados = sorted(
        registros.values(),
        key=lambda item: (
            str(item.get("candle_utc") or ""),
            str(item.get("decisao") or "")
        )
    )

    with ARQUIVO_CSV.open(
        "w", encoding="utf-8-sig", newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=CAMPOS,
            delimiter=";",
            extrasaction="ignore",
        )

        escritor.writeheader()

        for registro in ordenados:
            escritor.writerow({
                campo: registro.get(campo, "")
                for campo in CAMPOS
            })

    print(f"CSV salvo: {ARQUIVO_CSV}")
    print(f"Total de registros: {len(ordenados)}")


def main():
    print("=== MEMORIA PERSISTENTE GEMINI XAUUSD ===")

    # Mantem os registros que ja estavam no CSV.
    registros = carregar_csv_existente()

    # Importa a memoria consolidada pelo workflow atual.
    historico = carregar_json()

    adicionados = 0
    atualizados = 0

    for item in historico:
        registro = normalizar_registro(item)
        candle = registro["candle_utc"]
        decisao = registro["decisao"]

        if not candle:
            print("Registro ignorado: candle_utc ausente.")
            continue

        chave = (candle, decisao)

        if chave not in registros:
            registros[chave] = registro
            adicionados += 1
        else:
            # Atualiza os campos quando o JSON tiver novos dados,
            # sem apagar valores existentes por campos vazios.
            anterior = registros[chave]

            for campo in CAMPOS:
                valor_novo = registro.get(campo, "")
                if valor_novo not in (None, ""):
                    anterior[campo] = valor_novo

            atualizados += 1

    salvar_csv(registros)

    print(f"Registros novos: {adicionados}")
    print(f"Registros conferidos/atualizados: {atualizados}")
    print("Memoria CSV processada com sucesso.")


if __name__ == "__main__":
    main()
