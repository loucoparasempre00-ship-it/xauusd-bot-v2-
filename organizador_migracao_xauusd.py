
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR XAUUSD
Arquitetura com 4 módulos e 4 bancos SQLite.

Módulos:
1. Gráficos
2. Gerador de sinais
3. Memória persistente
4. Condutor de operações

Cada módulo:
- Identifica seus arquivos.
- Lê os dados.
- Organiza os registros.
- Grava no seu banco.
- Preserva o arquivo original.
- Evita duplicar registros na reexecução.

O Organizador não executa operações financeiras.
Não apaga arquivos antigos.
"""

import csv
import gzip
import hashlib
import io
import json
import sqlite3

from datetime import datetime, timezone
from pathlib import Path


# ==================================================
# CONFIGURAÇÃO GERAL
# ==================================================

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

DATA.mkdir(exist_ok=True)

BANCOS = {
    "graficos": DATA / "graficos.db",
    "sinais": DATA / "sinais.db",
    "memoria": DATA / "memoria.db",
    "operacoes": DATA / "operacoes.db",
}

TABELAS = {
    "graficos": "graficos",
    "sinais": "sinais",
    "memoria": "memoria",
    "operacoes": "operacoes",
}

EXTENSOES = {
    ".json",
    ".jsonl",
    ".csv",
    ".gz",
    ".txt",
    ".db",
    ".sqlite",
    ".sqlite3",
}


def agora():
    return datetime.now(timezone.utc).isoformat()


def sha256(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def json_texto(objeto):
    return json.dumps(
        objeto,
        ensure_ascii=False,
        sort_keys=True,
        default=str
    )


# ==================================================
# CLASSIFICADOR CENTRAL
# ==================================================

def classificar(arquivo):
    nome = arquivo.name.lower()
    caminho = arquivo.as_posix().lower()

    # ----------------------------------------------
    # 1. CONDUTOR DE OPERAÇÕES
    # Prioridade para não confundir decisões
    # operacionais com sinais de entrada.
    # ----------------------------------------------

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
    )):
        return "operacoes"

    if "organizado_04_condutor" in nome:
        return "operacoes"

    if "organizado_05_organizador" in nome:
        return "operacoes"

    # ----------------------------------------------
    # 2. GRÁFICOS
    # ----------------------------------------------

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
    )):
        return "graficos"

    if "organizado_01_graficos" in nome:
        return "graficos"

    # ----------------------------------------------
    # 3. GERADOR DE SINAIS
    # ----------------------------------------------

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
    )):
        return "sinais"

    if "organizado_02_gerador" in nome:
        return "sinais"

    # ----------------------------------------------
    # 4. MEMÓRIA PERSISTENTE
    # ----------------------------------------------

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
    )):
        return "memoria"

    if "organizado_03_memoria" in nome:
        return "memoria"

    if "organizado_06_estatisticas" in nome:
        return "memoria"

    if "relatorio_organizador_memoria" in nome:
        return "memoria"

    # Arquivo não identificado:
    # fica fora da migração para evitar
    # classificação incorreta.
    return None


# ==================================================
# BASE DO MÓDULO
# ==================================================

class ModuloBase:

    def __init__(self, grupo):
        self.grupo = grupo
        self.tabela = TABELAS[grupo]
        self.caminho_banco = BANCOS[grupo]

        self.db = sqlite3.connect(
            self.caminho_banco
        )

        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

        self.criar_estrutura()

        self.arquivos = 0
        self.registros = 0
        self.erros = []

    def criar_estrutura(self):

        # Tabela principal do módulo.
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

        # Preservação integral dos arquivos de origem.
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

        # Controle de importação.
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

        # Referências para os demais módulos.
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
    # PRESERVAR ARQUIVO ORIGINAL
    # ----------------------------------------------

    def preservar_origem(self, caminho, arquivo, conteudo):

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
            sha256(conteudo),
            len(conteudo),
            agora(),
            sqlite3.Binary(conteudo)
        ))

    # ----------------------------------------------
    # GRAVAR REGISTRO
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
        resumo = hashlib.sha256(
            texto.encode("utf-8")
        ).hexdigest()

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

        texto = conteudo.decode("utf-8-sig")
        objeto = json.loads(texto)

        numero = 0

        def registrar(valor, tipo, chave=None):
            nonlocal numero

            numero += 1

            self.gravar(
                caminho,
                tipo,
                numero,
                valor,
                chave
            )

        if isinstance(objeto, list):

            for item in objeto:
                registrar(item, "item_lista")

        elif isinstance(objeto, dict):

            # Preserva o objeto principal.
            registrar(
                objeto,
                "objeto_principal",
                "raiz"
            )

            # Extrai listas internas para registros
            # individuais, sem perder o objeto original.
            for chave, valor in objeto.items():

                if isinstance(valor, list):

                    for item in valor:
                        registrar(
                            item,
                            "item_" + str(chave),
                            chave
                        )

        else:
            registrar(
                objeto,
                "valor_principal"
            )

        return numero

    # ----------------------------------------------
    # LEITURA JSONL
    # ----------------------------------------------

    def ler_jsonl(self, caminho, conteudo):

        numero = 0

        for linha in conteudo.decode(
            "utf-8-sig"
        ).splitlines():

            if not linha.strip():
                continue

            objeto = json.loads(linha)
            numero += 1

            self.gravar(
                caminho,
                "linha_jsonl",
                numero,
                objeto
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
    # LEITURA SQLITE ANTIGO
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

                consulta = (
                    f'SELECT * FROM "{nome_tabela}"'
                )

                for linha in origem.execute(consulta):

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
    # PROCESSAR ARQUIVO
    # ----------------------------------------------

    def processar(self, arquivo):

        caminho = arquivo.relative_to(ROOT).as_posix()

        conteudo = arquivo.read_bytes()
        hash_atual = sha256(conteudo)

        # Verifica se já foi importado sem alterações.
        anterior = self.db.execute("""
            SELECT hash_sha256
            FROM controle_importacao
            WHERE caminho = ?
        """, (caminho,)).fetchone()

        if anterior and anterior[0] == hash_atual:
            return "INALTERADO"

        try:

            self.preservar_origem(
                caminho,
                arquivo,
                conteudo
            )

            # Remove somente os registros anteriores
            # deste arquivo neste módulo.
            self.db.execute(
                f"DELETE FROM {self.tabela} "
                "WHERE arquivo_origem = ?",
                (caminho,)
            )

            extensao = arquivo.suffix.lower()

            if arquivo.name.lower().endswith(".csv.gz"):
                conteudo = gzip.decompress(conteudo)
                quantidade = self.ler_csv(
                    caminho,
                    conteudo
                )

            elif extensao == ".gz":
                conteudo = gzip.decompress(conteudo)

                if caminho.lower().endswith(".json.gz"):
                    quantidade = self.ler_json(
                        caminho,
                        conteudo
                    )
                else:
                    quantidade = self.ler_csv(
                        caminho,
                        conteudo
                    )

            elif extensao == ".json":
                quantidade = self.ler_json(
                    caminho,
                    conteudo
                )

            elif extensao == ".jsonl":
                quantidade = self.ler_jsonl(
                    caminho,
                    conteudo
                )

            elif extensao == ".csv":
                quantidade = self.ler_csv(
                    caminho,
                    conteudo
                )

            elif extensao in (
                ".db",
                ".sqlite",
                ".sqlite3"
            ):
                quantidade = self.ler_sqlite(
                    caminho,
                    conteudo
                )

            else:
                quantidade = self.gravar_texto(
                    caminho,
                    conteudo
                )

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

    def gravar_texto(self, caminho, conteudo):

        texto = conteudo.decode(
            "utf-8-sig",
            errors="replace"
        )

        self.gravar(
            caminho,
            "arquivo_texto",
            1,
            {"conteudo": texto}
        )

        return 1

    def fechar(self):
        self.db.commit()
        self.db.close()


# ==================================================
# MÓDULO 1 - GRÁFICOS
# ==================================================

class ModuloGraficos(ModuloBase):

    def __init__(self):
        super().__init__("graficos")

    def aceitar(self, arquivo):
        return classificar(arquivo) == "graficos"


# ==================================================
# MÓDULO 2 - GERADOR DE SINAIS
# ==================================================

class ModuloSinais(ModuloBase):

    def __init__(self):
        super().__init__("sinais")

    def aceitar(self, arquivo):
        return classificar(arquivo) == "sinais"


# ==================================================
# MÓDULO 3 - MEMÓRIA PERSISTENTE
# ==================================================

class ModuloMemoria(ModuloBase):

    def __init__(self):
        super().__init__("memoria")

    def aceitar(self, arquivo):
        return classificar(arquivo) == "memoria"


# ==================================================
# MÓDULO 4 - CONDUTOR DE OPERAÇÕES
# ==================================================

class ModuloOperacoes(ModuloBase):

    def __init__(self):
        super().__init__("operacoes")

    def aceitar(self, arquivo):
        return classificar(arquivo) == "operacoes"


# ==================================================
# COORDENAÇÃO E INTERCÂMBIO ENTRE MÓDULOS
# ==================================================

class OrganizadorXAUUSD:

    def __init__(self):

        self.modulos = [
            ModuloGraficos(),
            ModuloSinais(),
            ModuloMemoria(),
            ModuloOperacoes(),
        ]

        self.relatorio = {
            "versao": 2,
            "data_utc": agora(),
            "modo": "ORGANIZACAO_POR_QUATRO_MODULOS",
            "modulos": {},
            "arquivos_sem_classificacao": [],
            "erros": [],
        }

    def arquivos_para_processar(self):

        ignorar = {
            banco.resolve()
            for banco in BANCOS.values()
        }

        temporarios = {
            ".temporario_leitura_graficos.db",
            ".temporario_leitura_sinais.db",
            ".temporario_leitura_memoria.db",
            ".temporario_leitura_operacoes.db",
            ".origem_temporaria.db",
        }

        encontrados = []

        for arquivo in DATA.rglob("*"):

            if not arquivo.is_file():
                continue

            if arquivo.resolve() in ignorar:
                continue

            if arquivo.name in temporarios:
                continue

            if arquivo.name.startswith("."):
                continue

            if arquivo.suffix.lower() not in EXTENSOES:
                continue

            encontrados.append(arquivo)

        return sorted(encontrados)

    def registrar_intercambio(self):

        # Cada módulo registra a existência dos outros
        # três bancos. A informação original continua
        # pertencendo ao banco do seu módulo.

        for modulo in self.modulos:

            for outro in self.modulos:

                if modulo.grupo == outro.grupo:
                    continue

                hash_banco = hashlib.sha256(
                    outro.caminho_banco.read_bytes()
                ).hexdigest()

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

    def executar(self):

        arquivos = self.arquivos_para_processar()

        print("=" * 55)
        print("ORGANIZADOR XAUUSD")
        print("QUATRO MODULOS")
        print("=" * 55)

        try:

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

            # Verificação de integridade e relatório.
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

            self.relatorio["total_arquivos_sem_grupo"] = len(
                self.relatorio["arquivos_sem_classificacao"]
            )

            self.relatorio["total_erros"] = len(
                self.relatorio["erros"]
            )

            self.relatorio["status"] = (
                "SUCESSO"
                if self.relatorio["total_erros"] == 0
                else "CONCLUIDO_COM_ERROS"
            )

            relatorio_path = (
                DATA / "relatorio_organizador_xauusd.json"
            )

            relatorio_path.write_text(
                json_texto(self.relatorio),
                encoding="utf-8"
            )

            print("=" * 55)
            print("RESULTADO FINAL")

            for grupo, dados in self.relatorio[
                "modulos"
            ].items():

                print(
                    grupo.upper(),
                    "| registros:",
                    dados["total_registros"],
                    "| integridade:",
                    dados["integridade"]
                )

            print(
                "Arquivos sem classificação:",
                self.relatorio["total_arquivos_sem_grupo"]
            )

            print(
                "Erros:",
                self.relatorio["total_erros"]
            )

            print(
                "Relatório:",
                relatorio_path
            )

            print("=" * 55)

            return self.relatorio

        finally:

            for modulo in self.modulos:
                modulo.fechar()


# ==================================================
# EXECUÇÃO
# ==================================================

if __name__ == "__main__":

    organizador = OrganizadorXAUUSD()
    resultado = organizador.executar()

    if resultado["total_erros"] > 0:
        raise SystemExit(1)

    print("ORGANIZADOR FINALIZADO.")
