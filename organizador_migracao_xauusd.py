
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR XAUUSD - SEIS MODULOS

Importa os arquivos que chegaram.
Ausencias geram avisos e nao interrompem os outros modulos.
Modulos sem fonte nova preservam seus dados anteriores.
"""

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
    DATA.mkdir(parents=True, exist_ok=True)

    for banco in BANCOS.values():
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
    avisos = []

    if not MANIFESTO.is_file():
        avisos.append("Manifesto do Coletor ausente.")
        manifesto = {}
    else:
        try:
            manifesto = json.loads(
                MANIFESTO.read_text(encoding="utf-8")
            )
        except Exception as erro:
            manifesto = {}
            avisos.append(
                f"Manifesto do Coletor invalido: {type(erro).__name__}: {erro}"
            )

    if manifesto.get("status") != "SUCESSO":
        avisos.append(
            "Coletor nao terminou sem avisos. "
            f"Status recebido: {manifesto.get('status', 'DESCONHECIDO')}."
        )

    for grupo in GRUPOS:
        pasta = SAIDA / grupo
        pasta.mkdir(parents=True, exist_ok=True)

    avisos.extend(
        manifesto.get("avisos", [])
        if isinstance(manifesto.get("avisos"), list)
        else []
    )

    ausentes = manifesto.get("arquivos_obrigatorios_ausentes", [])
    if ausentes:
        avisos.append({
            "etapa": "coletor",
            "arquivos_obrigatorios_ausentes": ausentes,
        })

    return manifesto, avisos


class Modulo:

    def __init__(self, grupo):
        self.grupo = grupo
        self.tabela = grupo
        self.banco = BANCOS[grupo]
        self.db = sqlite3.connect(self.banco)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.erros = []
        self.avisos = []
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

            detalhe = (
                f"{type(erro).__name__}: {erro}"
            )

            self.erros.append({
                "arquivo": caminho,
                "erro": detalhe,
            })

            print(f"[ERRO DE IMPORTACAO] {caminho}: {detalhe}")
            return "ERRO"


class ModuloOperacoes(Modulo):

    def reconstruir_ativas(self):
        """
        Usa a recepcao nova como fonte.
        Se o arquivo nao existir, preserva o banco atual.
        Se existir e estiver valido, reconstrói a lista ativa,
        inclusive quando a lista estiver legitimamente vazia.
        """

        candidatos = list(
            (SAIDA / "operacoes").rglob(
                "entrada_recepcao_operacoes.json"
            )
        )

        if not candidatos:
            self.avisos.append(
                "entrada_recepcao_operacoes.json ausente; "
                "banco de operacoes preservado."
            )
            print(
                "[OPERACOES] Recepcao ausente. "
                "Banco anterior preservado."
            )
            return None

        arquivo = candidatos[0]

        try:
            estado = json.loads(
                arquivo.read_text(encoding="utf-8-sig")
            )
        except Exception as erro:
            raise RuntimeError(
                "Arquivo de recepcao invalido: "
                f"{type(erro).__name__}: {erro}"
            )

        if not isinstance(estado, dict):
            raise RuntimeError(
                "Formato da recepcao invalido: esperado objeto JSON."
            )

        lista_dict = estado.get("operacoes_ativas")

        if not isinstance(lista_dict, dict):
            raise RuntimeError(
                "Campo operacoes_ativas ausente ou invalido. "
                "Banco anterior preservado."
            )

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

        numero = 1
        ativos = []
        sinais_vistos = set()
        codigos_usados = set()

        for chave, original in lista_dict.items():
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
                self.avisos.append(
                    f"Operacao {chave} ignorada: sem identificador de sinal."
                )
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

        # A fonte foi encontrada e validada.
        # Portanto, uma lista vazia aqui e um resultado valido.
        self.db.execute("DELETE FROM operacoes")

        for indice, item in enumerate(ativos, start=1):
            self.gravar(
                "ENTRADA_RECEPCAO_OPERACOES",
                "operacao_ativa",
                indice,
                item,
                item["sinal_run_id"],
            )

        self.db.commit()
        return len(ativos)


def executar():
    manifesto, avisos_coletor = validar_coletor()
    preparar_bancos()

    modulos = {}
    relatorio = {
        "versao": 7,
        "data_utc": agora(),
        "status": "INICIADO",
        "origem_coletor": manifesto,
        "modulos": {},
        "avisos": list(avisos_coletor),
        "erros": [],
        "operacoes_ativas": None,
        "operacoes_reconstruidas": False,
    }

    try:
        for grupo in GRUPOS:
            if grupo == "operacoes":
                modulos[grupo] = ModuloOperacoes(grupo)
            else:
                modulos[grupo] = Modulo(grupo)

        print("ORGANIZADOR XAUUSD - SEIS MODULOS")

        for grupo in GRUPOS:
            pasta = SAIDA / grupo
            modulo = modulos[grupo]

            arquivos = sorted(
                arquivo for arquivo in pasta.rglob("*")
                if arquivo.is_file()
            )

            if not arquivos:
                aviso = (
                    f"Nenhum arquivo recebido para o modulo {grupo}; "
                    "dados anteriores preservados."
                )
                modulo.avisos.append(aviso)
                print(f"[AVISO {grupo.upper()}] {aviso}")

            for arquivo in arquivos:
                resultado = modulo.importar(arquivo, pasta)
                print(
                    f"[{grupo.upper()}] {arquivo.name}: {resultado}"
                )

            if grupo == "operacoes":
                try:
                    quantidade = modulo.reconstruir_ativas()

                    if quantidade is None:
                        relatorio["avisos"].append(
                            "Operacoes nao foram reconstruidas: "
                            "fonte de recepcao ausente."
                        )
                    else:
                        relatorio["operacoes_ativas"] = quantidade
                        relatorio["operacoes_reconstruidas"] = True

                except Exception as erro:
                    detalhe = f"{type(erro).__name__}: {erro}"
                    modulo.erros.append({
                        "etapa": "reconstruir_operacoes",
                        "erro": detalhe,
                    })
                    print("[ERRO OPERACOES]", detalhe)

        # Referencias entre os bancos.
        for nome, modulo in modulos.items():
            for outro_nome, outro in modulos.items():
                if nome == outro_nome:
                    continue

                try:
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

                except Exception as erro:
                    modulo.erros.append({
                        "etapa": "referencia_cruzada",
                        "banco": outro_nome,
                        "erro": f"{type(erro).__name__}: {erro}",
                    })

            modulo.db.commit()

        for nome, modulo in modulos.items():
            try:
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
                    "avisos": modulo.avisos,
                    "erros": modulo.erros,
                }

                relatorio["avisos"].extend(
                    {
                        "modulo": nome,
                        "mensagem": aviso,
                    }
                    for aviso in modulo.avisos
                )

                relatorio["erros"].extend(
                    {
                        "modulo": nome,
                        **erro,
                    }
                    for erro in modulo.erros
                )

                if integridade != "ok":
                    relatorio["erros"].append({
                        "banco": nome,
                        "erro": "Falha no integrity_check",
                    })

            except Exception as erro:
                relatorio["erros"].append({
                    "banco": nome,
                    "erro": f"{type(erro).__name__}: {erro}",
                })

        relatorio["total_avisos"] = len(relatorio["avisos"])
        relatorio["total_erros"] = len(relatorio["erros"])

        if relatorio["total_erros"] > 0:
            relatorio["status"] = "CONCLUIDO_COM_ERROS"
        elif relatorio["total_avisos"] > 0:
            relatorio["status"] = "CONCLUIDO_COM_AVISOS"
        else:
            relatorio["status"] = "SUCESSO"

        RELATORIO.write_text(
            json_texto(relatorio),
            encoding="utf-8",
        )

        print("=" * 60)
        print("OPERAÇÕES ATIVAS:", relatorio["operacoes_ativas"])
        print("AVISOS:", relatorio["total_avisos"])
        print("ERROS:", relatorio["total_erros"])
        print("STATUS:", relatorio["status"])
        print("RELATORIO:", RELATORIO)
        print("=" * 60)

    finally:
        for modulo in modulos.values():
            try:
                modulo.db.commit()
                modulo.db.close()
            except Exception:
                pass

    # Compacta os seis bancos mesmo quando houve avisos ou erros.
    compactar_bancos()
    print("SEIS BANCOS COMPACTADOS.")


if __name__ == "__main__":
    executar()
