
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SAIDA = DATA / "coletor_saida"
MANIFESTO = SAIDA / "manifesto_coletor.json"

GRUPOS = (
    "graficos",
    "sinais",
    "memoria",
    "operacoes",
    "estatisticas_m5",
    "resultados_operacoes",
)

BANCOS = {
    nome: DATA / f"{nome}.db"
    for nome in GRUPOS
}

RELATORIO = DATA / "relatorio_organizador_xauusd.json"


def agora():
    return datetime.now(timezone.utc).isoformat()


def json_texto(objeto):
    return json.dumps(
        objeto,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def sha256(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def preparar_bancos():
    DATA.mkdir(exist_ok=True)

    for nome, banco in BANCOS.items():
        compactado = Path(str(banco) + ".gz")

        if not banco.exists() and compactado.exists():
            banco.write_bytes(gzip.decompress(compactado.read_bytes()))


def compactar_bancos():
    for banco in BANCOS.values():
        if banco.exists():
            destino = Path(str(banco) + ".gz")

            destino.write_bytes(
                gzip.compress(
                    banco.read_bytes(),
                    compresslevel=9,
                    mtime=0,
                )
            )

            banco.unlink()


def validar_coletor():
    if not MANIFESTO.is_file():
        raise RuntimeError("Manifesto do Coletor não encontrado.")

    manifesto = json.loads(MANIFESTO.read_text(encoding="utf-8"))

    if manifesto.get("status") != "SUCESSO":
        raise RuntimeError(
            "Coletor não terminou com sucesso. "
            "Organizador bloqueado para proteger os bancos."
        )

    for grupo in GRUPOS:
        pasta = SAIDA / grupo

        if not pasta.is_dir():
            raise RuntimeError(f"Saída ausente: {grupo}")


class Modulo:

    def __init__(self, grupo):
        self.grupo = grupo
        self.tabela = grupo
        self.banco = BANCOS[grupo]
        self.db = sqlite3.connect(self.banco)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.erros = []

        self.criar_tabelas()

    def criar_tabelas(self):
        self.db.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.tabela} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                arquivo_origem TEXT NOT NULL,
                tipo_registro TEXT NOT NULL,
                numero_registro INTEGER NOT NULL,
                chave_registro TEXT,
                payload_json TEXT NOT NULL,
                hash_registro TEXT NOT NULL,
                criado_utc TEXT NOT NULL,
                UNIQUE (
                    arquivo_origem,
                    tipo_registro,
                    numero_registro
                )
            )
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS arquivos_origem (
                caminho TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                hash_sha256 TEXT NOT NULL,
                tamanho_bytes INTEGER NOT NULL,
                data_importacao TEXT NOT NULL,
                conteudo BLOB NOT NULL
            )
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS controle_importacao (
                caminho TEXT PRIMARY KEY,
                grupo TEXT NOT NULL,
                quantidade_registros INTEGER NOT NULL,
                hash_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                atualizado_utc TEXT NOT NULL
            )
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS referencias_modulos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banco_origem TEXT NOT NULL,
                arquivo_origem TEXT NOT NULL,
                hash_origem TEXT NOT NULL,
                data_referencia TEXT NOT NULL,
                UNIQUE (
                    banco_origem,
                    arquivo_origem,
                    hash_origem
                )
            )
        """)

        self.db.commit()

    def gravar(self, caminho, tipo, numero, objeto, chave=None):
        texto = json_texto(objeto)

        self.db.execute(f"""
            INSERT OR REPLACE INTO {self.tabela} (
                arquivo_origem,
                tipo_registro,
                numero_registro,
                chave_registro,
                payload_json,
                hash_registro,
                criado_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            caminho,
            tipo,
            numero,
            str(chave) if chave is not None else None,
            texto,
            sha256(texto.encode("utf-8")),
            agora(),
        ))

    def ler_json(self, caminho, conteudo):
        objeto = json.loads(conteudo.decode("utf-8-sig"))
        numero = 0

        def registrar(valor, tipo, chave=None):
            nonlocal numero
            numero += 1
            self.gravar(caminho, tipo, numero, valor, chave)

        if isinstance(objeto, list):
            for item in objeto:
                registrar(item, "item_lista")

        elif isinstance(objeto, dict):
            registrar(objeto, "objeto_principal", "raiz")

            for chave, valor in objeto.items():
                if isinstance(valor, list):
                    for item in valor:
                        registrar(item, "item_" + str(chave), chave)

        else:
            registrar(objeto, "valor_principal")

        return numero

    def ler_jsonl(self, caminho, conteudo):
        numero = 0

        for linha in conteudo.decode("utf-8-sig").splitlines():
            if not linha.strip():
                continue

            numero += 1
            self.gravar(
                caminho,
                "linha_jsonl",
                numero,
                json.loads(linha),
            )

        return numero

    def ler_csv(self, caminho, conteudo):
        texto = conteudo.decode("utf-8-sig")

        try:
            dialeto = csv.Sniffer().sniff(
                texto[:8192],
                delimiters=",;\t|",
            )
        except csv.Error:
            dialeto = csv.excel

        leitor = csv.DictReader(
            io.StringIO(texto),
            dialect=dialeto,
        )

        numero = 0

        for linha in leitor:
            numero += 1
            self.gravar(caminho, "linha_csv", numero, linha)

        return numero

    def ler_sqlite(self, caminho, conteudo):
        temporario = DATA / f".temporario_{self.grupo}.db"
        temporario.write_bytes(conteudo)

        origem = None
        numero = 0

        try:
            origem = sqlite3.connect(
                f"file:{temporario}?mode=ro",
                uri=True,
            )

            tabelas = origem.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                AND name NOT LIKE 'sqlite_%'
            """).fetchall()

            for (nome_tabela,) in tabelas:
                colunas = [
                    coluna[1]
                    for coluna in origem.execute(
                        f'PRAGMA table_info("{nome_tabela}")'
                    )
                ]

                for linha in origem.execute(
                    f'SELECT * FROM "{nome_tabela}"'
                ):
                    numero += 1
                    self.gravar(
                        caminho,
                        "tabela_" + nome_tabela,
                        numero,
                        dict(zip(colunas, linha)),
                        nome_tabela,
                    )

        finally:
            if origem is not None:
                origem.close()

            temporario.unlink(missing_ok=True)

        return numero

    def importar(self, arquivo, pasta_grupo):
        # Usa o caminho original lógico, sem o prefixo coletor_saida/grupo.
        relativo = arquivo.relative_to(pasta_grupo)
        caminho = "data/" + relativo.as_posix()

        original = arquivo.read_bytes()
        resumo = sha256(original)

        anterior = self.db.execute("""
            SELECT hash_sha256
            FROM controle_importacao
            WHERE caminho = ?
        """, (caminho,)).fetchone()

        if anterior and anterior[0] == resumo:
            return "INALTERADO"

        try:
            self.db.execute("""
                INSERT OR REPLACE INTO arquivos_origem (
                    caminho,
                    nome,
                    hash_sha256,
                    tamanho_bytes,
                    data_importacao,
                    conteudo
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                caminho,
                arquivo.name,
                resumo,
                len(original),
                agora(),
                sqlite3.Binary(original),
            ))

            self.db.execute(
                f"DELETE FROM {self.tabela} WHERE arquivo_origem=?",
                (caminho,),
            )

            nome = arquivo.name.lower()
            extensao = arquivo.suffix.lower()

            if nome.endswith(".csv.gz"):
                quantidade = self.ler_csv(
                    caminho,
                    gzip.decompress(original),
                )

            elif nome.endswith(".json.gz"):
                quantidade = self.ler_json(
                    caminho,
                    gzip.decompress(original),
                )

            elif extensao == ".gz":
                bruto = gzip.decompress(original)

                try:
                    quantidade = self.ler_json(caminho, bruto)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    quantidade = self.ler_csv(caminho, bruto)

            elif extensao == ".json":
                quantidade = self.ler_json(caminho, original)

            elif extensao == ".jsonl":
                quantidade = self.ler_jsonl(caminho, original)

            elif extensao == ".csv":
                quantidade = self.ler_csv(caminho, original)

            elif extensao in (".db", ".sqlite", ".sqlite3"):
                quantidade = self.ler_sqlite(caminho, original)

            else:
                texto = original.decode("utf-8-sig", errors="replace")
                self.gravar(
                    caminho,
                    "arquivo_texto",
                    1,
                    {"conteudo": texto},
                )
                quantidade = 1

            self.db.execute("""
                INSERT OR REPLACE INTO controle_importacao (
                    caminho,
                    grupo,
                    quantidade_registros,
                    hash_sha256,
                    status,
                    atualizado_utc
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                caminho,
                self.grupo,
                quantidade,
                resumo,
                "SUCESSO",
                agora(),
            ))

            self.db.commit()
            return f"IMPORTADO ({quantidade} registros)"

        except Exception as erro:
            self.db.rollback()
            self.erros.append({
                "arquivo": caminho,
                "erro": str(erro),
            })
            return "ERRO"


class ModuloOperacoes(Modulo):

    def reconstruir_ativas(self):
        # Preserva os códigos já atribuídos às operações ativas.
        anteriores = {}

        linhas = self.db.execute("""
            SELECT payload_json FROM operacoes
        """).fetchall()

        for (texto,) in linhas:
            try:
                item = json.loads(texto)
            except Exception:
                continue

            if not isinstance(item, dict):
                continue

            sinal_id = (
                item.get("sinal_run_id")
                or item.get("id_sinal")
                or item.get("signal_id")
            )

            codigo = item.get("codigo_operacao")

            if sinal_id is not None and codigo:
                anteriores[str(sinal_id)] = str(codigo)

        pasta = SAIDA / "operacoes"
        estados = list(pasta.rglob("acompanhamento_autonomo_estado.json"))

        if not estados:
            raise RuntimeError(
                "Estado do acompanhamento não foi coletado. "
                "Banco de operações não será alterado."
            )

        estado = json.loads(estados[0].read_text(encoding="utf-8"))

        lista = estado.get("operacoes", [])

        if not isinstance(lista, list):
            lista = []

        individual = estado.get("operacao")

        if isinstance(individual, dict):
            lista.append(individual)

        numero = int(
            estado.get("proximo_numero_operacao", 1) or 1
        )

        ativos = []
        sinais_vistos = set()
        codigos_usados = set()

        for original in lista:
            if not isinstance(original, dict):
                continue

            if str(original.get("status", "")).upper() != "ATIVA":
                continue

            sinal_id = (
                original.get("sinal_run_id")
                or original.get("id_sinal")
                or original.get("signal_id")
            )

            if sinal_id is None:
                continue

            sinal_id = str(sinal_id)

            if sinal_id in sinais_vistos:
                continue

            sinais_vistos.add(sinal_id)

            item = dict(original)

            codigo = (
                item.get("codigo_operacao")
                or anteriores.get(sinal_id)
            )

            if not isinstance(codigo, str) or not re.fullmatch(
                r"OP-[0-9]{6,}", codigo
            ):
                codigo = None

            if codigo is None or codigo in codigos_usados:
                while f"OP-{numero:06d}" in codigos_usados:
                    numero += 1

                codigo = f"OP-{numero:06d}"
                numero += 1

            codigos_usados.add(codigo)

            item["codigo_operacao"] = codigo
            item["id"] = codigo
            item["sinal_run_id"] = sinal_id
            item["status"] = "ATIVA"

            ativos.append(item)

        self.db.execute("DELETE FROM operacoes")

        for indice, item in enumerate(ativos, start=1):
            self.gravar(
                "ENTREI_CONFIRMADO",
                "operacao_ativa",
                indice,
                item,
                item["sinal_run_id"],
            )

        self.db.commit()
        return len(ativos)


def executar():
    validar_coletor()
    preparar_bancos()

    modulos = {}

    try:
        for grupo in GRUPOS:
            if grupo == "operacoes":
                modulos[grupo] = ModuloOperacoes(grupo)
            else:
                modulos[grupo] = Modulo(grupo)

        relatorio = {
            "versao": 6,
            "data_utc": agora(),
            "status": "INICIADO",
            "modulos": {},
            "erros": [],
            "operacoes_ativas": 0,
        }

        print("ORGANIZADOR XAUUSD - SEIS MÓDULOS")

        for grupo in GRUPOS:
            pasta = SAIDA / grupo
            modulo = modulos[grupo]

            arquivos = sorted(
                arquivo for arquivo in pasta.rglob("*")
                if arquivo.is_file()
            )

            for arquivo in arquivos:
                resultado = modulo.importar(arquivo, pasta)
                print(f"[{grupo.upper()}] {arquivo.name}: {resultado}")

                if resultado == "ERRO":
                    continue

            if grupo == "operacoes":
                relatorio["operacoes_ativas"] = (
                    modulo.reconstruir_ativas()
                )

        # Referências cruzadas entre os seis bancos.
        for nome, modulo in modulos.items():
            for outro_nome, outro in modulos.items():
                if nome == outro_nome:
                    continue

                resumo = sha256(outro.banco.read_bytes())

                modulo.db.execute("""
                    INSERT OR IGNORE INTO referencias_modulos (
                        banco_origem,
                        arquivo_origem,
                        hash_origem,
                        data_referencia
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    outro_nome,
                    outro.banco.name,
                    resumo,
                    agora(),
                ))

            modulo.db.commit()

        for nome, modulo in modulos.items():
            integridade = modulo.db.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            total = modulo.db.execute(
                f"SELECT COUNT(*) FROM {nome}"
            ).fetchone()[0]

            origens = modulo.db.execute(
                "SELECT COUNT(*) FROM arquivos_origem"
            ).fetchone()[0]

            relatorio["modulos"][nome] = {
                "banco": nome + ".db.gz",
                "registros": total,
                "arquivos_origem": origens,
                "integridade": integridade,
            }

            relatorio["erros"].extend(modulo.erros)

            if integridade != "ok":
                relatorio["erros"].append({
                    "banco": nome,
                    "erro": "Falha no integrity_check",
                })

        relatorio["total_erros"] = len(relatorio["erros"])

        relatorio["status"] = (
            "SUCESSO"
            if relatorio["total_erros"] == 0
            else "CONCLUIDO_COM_ERROS"
        )

        RELATORIO.write_text(
            json_texto(relatorio),
            encoding="utf-8",
        )

        print("OPERAÇÕES ATIVAS:", relatorio["operacoes_ativas"])

        for nome, dados in relatorio["modulos"].items():
            print(
                nome.upper(),
                "| registros:", dados["registros"],
                "| integridade:", dados["integridade"],
            )

        print("ERROS:", relatorio["total_erros"])
        print("STATUS:", relatorio["status"])

        if relatorio["status"] != "SUCESSO":
            raise RuntimeError("Organizador terminou com erros.")

    finally:
        for modulo in modulos.values():
            modulo.db.commit()
            modulo.db.close()

    compactar_bancos()
    print("SEIS BANCOS COMPACTADOS.")


if __name__ == "__main__":
    executar()
