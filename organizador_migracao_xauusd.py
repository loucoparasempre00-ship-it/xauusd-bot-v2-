#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR XAUUSD
6 módulos independentes e 6 bancos SQLite.
Preserva os arquivos originais. Não executa operações financeiras.
"""

import csv
import gzip
import hashlib
import io
import json
import sqlite3

from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# ==================================================
# OS SEIS BANCOS
# ==================================================

BANCOS = {
    "graficos": DATA / "graficos.db",
    "sinais": DATA / "sinais.db",
    "memoria": DATA / "memoria.db",
    "operacoes": DATA / "operacoes.db",
    "estatisticas_m5": DATA / "estatisticas_m5.db",
    "resultados_operacoes": DATA / "resultados_operacoes.db",
}

TABELAS = {grupo: grupo for grupo in BANCOS}

EXTENSOES = {
    ".json", ".jsonl", ".csv", ".gz",
    ".txt", ".db", ".sqlite", ".sqlite3"
}


def agora():
    return datetime.now(timezone.utc).isoformat()


def hash_bytes(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def json_texto(objeto):
    return json.dumps(
        objeto,
        ensure_ascii=False,
        sort_keys=True,
        default=str
    )


# ==================================================
# CLASSIFICAÇÃO DOS ARQUIVOS
# ==================================================

def classificar(arquivo):

    nome = arquivo.name.lower()

    # MÓDULO 5 — ESTATÍSTICAS E REGIÕES M5
    # Regras específicas antes das regras gerais de gráficos.

    if any(x in nome for x in (
        "estatisticas_regioes_m5",
        "proximas_regioes_m5",
        "regioes_estaveis_m5",
        "resultados_eventos_m5",
        "resumo_estatistico_m5",
        "transicoes_estaveis_m5",
        "transicoes_probabilidade_m5",
        "organizado_06_estatisticas",
    )):
        return "estatisticas_m5"

    # MÓDULO 6 — RESULTADOS DAS OPERAÇÕES

    if any(x in nome for x in (
        "resultados_operacoes",
        "resumo_resultados_m5",
    )):
        return "resultados_operacoes"

    # MÓDULO 4 — CONDUTOR DE OPERAÇÕES

    if any(x in nome for x in (
        "decisao_agente_operacional",
        "agente_operacional",
        "condutor",
        "operacao",
        "operações",
        "acompanhamento",
        "acompanhador",
        "cadastro_mestre",
        "resultado_operacoes",
        "eventos_operacao",
        "pacote_memoria_condutor",
        "organizado_04_condutor",
        "organizado_05_organizador",
    )):
        return "operacoes"

    # MÓDULO 1 — GRÁFICOS

    if any(x in nome for x in (
        "xauusd_m5",
        "vela",
        "grafico",
        "gráfico",
        "tabuleiro",
        "regiao",
        "região",
        "borda",
        "transicao",
        "transição",
        "sequencia",
        "sequência",
        "ohlc",
        "organizado_01_graficos",
    )):
        return "graficos"

    # MÓDULO 2 — GERADOR DE SINAIS

    if "analise_tecnica_contexto" in nome:
        return "sinais"

    if any(x in nome for x in (
        "sinal",
        "historico_decisoes",
        "historico_decisões",
        "ranking",
        "marcador",
        "setup_matematico",
        "setup_matemático",
        "decisao_sinal",
        "decisão_sinal",
        "organizado_02_gerador",
    )):
        return "sinais"

    # MÓDULO 3 — MEMÓRIA PERSISTENTE

    if any(x in nome for x in (
        "memoria",
        "memória",
        "gemini",
        "aprendizado",
        "aprendizagem",
        "parametro_adaptativo",
        "parâmetro_adaptativo",
        "estatisticas_consolidacao",
        "estatísticas_consolidação",
        "indice_arquivos",
        "índice_arquivos",
        "organizado_03_memoria",
        "relatorio_organizador_memoria",
    )):
        return "memoria"

    return None


# ==================================================
# BASE DOS MÓDULOS
# ==================================================

class ModuloBase:

    def __init__(self, grupo):

        self.grupo = grupo
        self.tabela = TABELAS[grupo]
        self.caminho_banco = BANCOS[grupo]

        self.db = sqlite3.connect(self.caminho_banco)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

        self.criar_estrutura()

        self.arquivos = 0
        self.registros = 0
        self.erros = []

    def criar_estrutura(self):

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

    # ----------------------------------------------
    # GRAVAÇÃO DE REGISTROS
    # ----------------------------------------------

    def gravar(
        self,
        caminho,
        tipo,
        numero,
        objeto,
        chave=None
    ):

        texto = json_texto(objeto)
        resumo = hash_bytes(texto.encode("utf-8"))

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
            resumo,
            agora()
        ))

    # ----------------------------------------------
    # LEITURA JSON
    # ----------------------------------------------

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
                        registrar(
                            item,
                            "item_" + str(chave),
                            chave
                        )

        else:
            registrar(objeto, "valor_principal")

        return numero

    # ----------------------------------------------
    # LEITURA JSONL
    # ----------------------------------------------

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
                json.loads(linha)
            )

        return numero

    # ----------------------------------------------
    # LEITURA CSV
    # ----------------------------------------------

    def ler_csv(self, caminho, conteudo):

        texto = conteudo.decode("utf-8-sig")

        try:
            dialeto = csv.Sniffer().sniff(
                texto[:8192],
                delimiters=",;\t|"
            )
        except csv.Error:
            dialeto = csv.excel

        leitor = csv.DictReader(
            io.StringIO(texto),
            dialect=dialeto
        )

        numero = 0

        for linha in leitor:

            numero += 1

            self.gravar(
                caminho,
                "linha_csv",
                numero,
                linha
            )

        return numero

    # ----------------------------------------------
    # LEITURA DE SQLITE ANTIGO
    # ----------------------------------------------

    def ler_sqlite(self, caminho, conteudo):

        temporario = DATA / (
            ".temporario_leitura_" + self.grupo + ".db"
        )

        temporario.write_bytes(conteudo)

        origem = None
        numero = 0

        try:

            origem = sqlite3.connect(
                f"file:{temporario}?mode=ro",
                uri=True
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
                        nome_tabela
                    )

        finally:

            if origem is not None:
                origem.close()

            temporario.unlink(missing_ok=True)

        return numero

    # ----------------------------------------------
    # PROCESSAMENTO DO ARQUIVO
    # ----------------------------------------------

    def processar(self, arquivo):

        caminho = arquivo.relative_to(ROOT).as_posix()
        original = arquivo.read_bytes()
        hash_atual = hash_bytes(original)

        anterior = self.db.execute("""
            SELECT hash_sha256
            FROM controle_importacao
            WHERE caminho = ?
        """, (caminho,)).fetchone()

        if anterior and anterior[0] == hash_atual:
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
                hash_atual,
                len(original),
                agora(),
                sqlite3.Binary(original)
            ))

            # Substitui somente os registros deste arquivo
            # dentro do banco responsável pelo seu grupo.
            self.db.execute(
                f"DELETE FROM {self.tabela} "
                "WHERE arquivo_origem = ?",
                (caminho,)
            )

            extensao = arquivo.suffix.lower()
            nome = arquivo.name.lower()

            if nome.endswith(".csv.gz"):

                quantidade = self.ler_csv(
                    caminho,
                    gzip.decompress(original)
                )

            elif extensao == ".gz":

                descompactado = gzip.decompress(original)

                if nome.endswith(".json.gz"):
                    quantidade = self.ler_json(
                        caminho,
                        descompactado
                    )
                else:
                    quantidade = self.ler_csv(
                        caminho,
                        descompactado
                    )

            elif extensao == ".json":

                quantidade = self.ler_json(caminho, original)

            elif extensao == ".jsonl":

                quantidade = self.ler_jsonl(caminho, original)

            elif extensao == ".csv":

                quantidade = self.ler_csv(caminho, original)

            elif extensao in (".db", ".sqlite", ".sqlite3"):

                quantidade = self.ler_sqlite(caminho, original)

            else:

                texto = original.decode(
                    "utf-8-sig",
                    errors="replace"
                )

                self.gravar(
                    caminho,
                    "arquivo_texto",
                    1,
                    {"conteudo": texto}
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
                hash_atual,
                "SUCESSO",
                agora()
            ))

            self.db.commit()

            self.arquivos += 1
            self.registros += quantidade

            return "IMPORTADO"

        except Exception as erro:

            self.db.rollback()

            self.erros.append({
                "arquivo": caminho,
                "erro": str(erro)
            })

            return "ERRO"

    def fechar(self):
        self.db.commit()
        self.db.close()


# ==================================================
# OS SEIS MÓDULOS
# ==================================================

class ModuloGraficos(ModuloBase):
    def __init__(self):
        super().__init__("graficos")


class ModuloSinais(ModuloBase):
    def __init__(self):
        super().__init__("sinais")


class ModuloMemoria(ModuloBase):
    def __init__(self):
        super().__init__("memoria")


class ModuloOperacoes(ModuloBase):
    def __init__(self):
        super().__init__("operacoes")


class ModuloEstatisticasM5(ModuloBase):
    def __init__(self):
        super().__init__("estatisticas_m5")


class ModuloResultadosOperacoes(ModuloBase):
    def __init__(self):
        super().__init__("resultados_operacoes")


# ==================================================
# ORGANIZADOR CENTRAL
# ==================================================

class OrganizadorXAUUSD:

    def __init__(self):

        self.modulos = [
            ModuloGraficos(),
            ModuloSinais(),
            ModuloMemoria(),
            ModuloOperacoes(),
            ModuloEstatisticasM5(),
            ModuloResultadosOperacoes(),
        ]

        self.relatorio = {
            "versao": 3,
            "data_utc": agora(),
            "modo": "ORGANIZACAO_POR_SEIS_MODULOS",
            "modulos": {},
            "arquivos_sem_classificacao": [],
            "erros": [],
        }

    # ----------------------------------------------
    # LOCALIZAR ARQUIVOS
    # ----------------------------------------------

    def arquivos_para_processar(self):

        bancos = {
            banco.resolve()
            for banco in BANCOS.values()
        }

        temporarios = {
            f".temporario_leitura_{grupo}.db"
            for grupo in BANCOS
        }

        encontrados = []

        for arquivo in DATA.rglob("*"):

            if not arquivo.is_file():
                continue

            if arquivo.resolve() in bancos:
                continue

            if arquivo.name.startswith("."):
                continue

            if arquivo.name in temporarios:
                continue

            if arquivo.name == "relatorio_organizador_xauusd.json":
                continue

            if arquivo.suffix.lower() not in EXTENSOES:
                continue

            encontrados.append(arquivo)

        return sorted(encontrados)

    # ----------------------------------------------
    # REFERÊNCIAS ENTRE OS SEIS BANCOS
    # ----------------------------------------------

    def registrar_intercambio(self):

        for modulo in self.modulos:

            for outro in self.modulos:

                if modulo.grupo == outro.grupo:
                    continue

                hash_banco = hash_bytes(
                    outro.caminho_banco.read_bytes()
                )

                modulo.db.execute("""
                    INSERT OR IGNORE INTO referencias_modulos (
                        banco_origem,
                        arquivo_origem,
                        hash_origem,
                        data_referencia
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    outro.grupo,
                    outro.caminho_banco.name,
                    hash_banco,
                    agora()
                ))

            modulo.db.commit()

    # ----------------------------------------------
    # EXECUÇÃO
    # ----------------------------------------------

    def executar(self):

        try:

            arquivos = self.arquivos_para_processar()

            print("=" * 55)
            print("ORGANIZADOR XAUUSD")
            print("SEIS MODULOS")
            print("=" * 55)

            for arquivo in arquivos:

                grupo = classificar(arquivo)

                if grupo is None:

                    self.relatorio[
                        "arquivos_sem_classificacao"
                    ].append(
                        arquivo.relative_to(ROOT).as_posix()
                    )

                    continue

                modulo = next(
                    m for m in self.modulos
                    if m.grupo == grupo
                )

                resultado = modulo.processar(arquivo)

                print(
                    f"[{grupo.upper()}] "
                    f"{arquivo.name}: {resultado}"
                )

            self.registrar_intercambio()

            # Verificação e relatório de cada banco.

            for modulo in self.modulos:

                integridade = modulo.db.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

                total = modulo.db.execute(
                    f"SELECT COUNT(*) FROM {modulo.tabela}"
                ).fetchone()[0]

                origens = modulo.db.execute(
                    "SELECT COUNT(*) FROM arquivos_origem"
                ).fetchone()[0]

                self.relatorio["modulos"][modulo.grupo] = {
                    "banco": modulo.caminho_banco.name,
                    "tabela_principal": modulo.tabela,
                    "arquivos_importados": modulo.arquivos,
                    "registros_nesta_execucao": modulo.registros,
                    "total_registros": total,
                    "arquivos_preservados": origens,
                    "integridade": integridade,
                }

                self.relatorio["erros"].extend(
                    modulo.erros
                )

            sem_grupo = len(
                self.relatorio["arquivos_sem_classificacao"]
            )

            total_erros = len(
                self.relatorio["erros"]
            )

            self.relatorio["total_arquivos_sem_grupo"] = sem_grupo
            self.relatorio["total_erros"] = total_erros

            self.relatorio["status"] = (
                "SUCESSO"
                if total_erros == 0 and sem_grupo == 0
                else "CONCLUIDO_COM_PENDENCIAS"
            )

            destino = DATA / "relatorio_organizador_xauusd.json"

            destino.write_text(
                json_texto(self.relatorio),
                encoding="utf-8"
            )

            print("=" * 55)
            print("RESULTADO FINAL")

            for grupo, dados in self.relatorio["modulos"].items():

                print(
                    grupo.upper(),
                    "| registros:",
                    dados["total_registros"],
                    "| integridade:",
                    dados["integridade"]
                )

            print("Arquivos sem classificação:", sem_grupo)
            print("Erros:", total_erros)
            print("Status:", self.relatorio["status"])
            print("Relatório:", destino)
            print("=" * 55)

            return self.relatorio

        finally:

            for modulo in self.modulos:
                modulo.fechar()


# ==================================================
# INÍCIO
# ==================================================

if __name__ == "__main__":

    resultado = OrganizadorXAUUSD().executar()

    if (
        resultado["total_erros"] > 0
        or resultado["total_arquivos_sem_grupo"] > 0
    ):
        raise SystemExit(1)

    print("ORGANIZADOR DE SEIS MODULOS FINALIZADO.")
