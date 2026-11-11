
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ORGANIZADOR XAUUSD
Seis módulos, seis bancos SQLite.
Organiza, cruza e elimina duplicidades.
Gera códigos oficiais OP-000001.
operacoes.db guarda somente operações ATIVAS confirmadas por ENTREI.
Não executa ordens financeiras.
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

ARQUIVO_ESTADO = DATA / "acompanhamento_autonomo_estado.json"
ARQUIVO_RELATORIO = DATA / "relatorio_organizador_xauusd.json"

BANCOS = {
    "graficos": DATA / "graficos.db",
    "sinais": DATA / "sinais.db",
    "memoria": DATA / "memoria.db",
    "operacoes": DATA / "operacoes.db",
    "estatisticas_m5": DATA / "estatisticas_m5.db",
    "resultados_operacoes": DATA / "resultados_operacoes.db",
}

EXTENSOES = {
    ".json", ".jsonl", ".csv", ".gz",
    ".txt", ".db", ".sqlite", ".sqlite3"
}


def agora():
    return datetime.now(timezone.utc).isoformat()


def json_texto(objeto):
    return json.dumps(
        objeto,
        ensure_ascii=False,
        sort_keys=True,
        default=str
    )


def hash_texto(texto):
    return hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()


def hash_bytes(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


# =========================================================
# CLASSIFICAÇÃO DOS ARQUIVOS
# =========================================================

def classificar(arquivo):
    nome = arquivo.name.lower()

    if nome == ARQUIVO_ESTADO.name.lower():
        return "operacoes"

    # Produção e histórico de decisões do Gemini pertencem
    # ao banco de sinais, não à memória adaptativa.
    if any(x in nome for x in (
        "resultado-agente-gemini",
        "historico-agente-gemini",
        "decisao_gemini",
        "decisão_gemini",
        "historico_decisoes",
        "historico_decisões",
    )):
        return "sinais"

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

    if any(x in nome for x in (
        "resultados_operacoes",
        "resultado_operacoes",
        "resumo_resultados_m5",
        "eventos_operacao",
        "cadastro_mestre",
        "condutor",
        "acompanhamento",
        "acompanhador",
        "agente_operacional",
        "decisao_agente_operacional",
        "organizado_04_condutor",
        "organizado_05_organizador",
    )):
        return "resultados_operacoes"

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

    if "analise_tecnica_contexto" in nome:
        return "sinais"

    if any(x in nome for x in (
        "sinal",
        "ranking",
        "marcador",
        "setup_matematico",
        "setup_matemático",
        "decisao_sinal",
        "decisão_sinal",
        "organizado_02_gerador",
    )):
        return "sinais"

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


# =========================================================
# BASE DOS MÓDULOS
# =========================================================

class ModuloBase:

    def __init__(self, grupo):
        self.grupo = grupo
        self.tabela = grupo
        self.caminho_banco = BANCOS[grupo]
        self.db = sqlite3.connect(self.caminho_banco)

        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

        self.arquivos = 0
        self.registros = 0
        self.erros = []

        self.criar_estrutura()

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

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS duplicatas_registros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_duplicado TEXT NOT NULL,
                arquivo_duplicado TEXT NOT NULL,
                registro_original_id INTEGER NOT NULL,
                removido_utc TEXT NOT NULL
            )
        """)

        self.db.commit()

    def gravar(self, caminho, tipo, numero, objeto, chave=None):
        texto = json_texto(objeto)
        resumo = hash_texto(texto)

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

    def ler_sqlite(self, caminho, conteudo):
        temporario = DATA / (
            ".temporario_" + self.grupo + ".db"
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
                    caminho, nome, hash_sha256,
                    tamanho_bytes, data_importacao, conteudo
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

            self.db.execute(
                f"DELETE FROM {self.tabela} WHERE arquivo_origem = ?",
                (caminho,)
            )

            nome = arquivo.name.lower()
            extensao = arquivo.suffix.lower()

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
                    caminho, grupo, quantidade_registros,
                    hash_sha256, status, atualizado_utc
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

    def eliminar_duplicatas(self):
        linhas = self.db.execute(f"""
            SELECT id, arquivo_origem, hash_registro
            FROM {self.tabela}
            ORDER BY id
        """).fetchall()

        vistos = {}
        removidos = 0

        for registro_id, origem, resumo in linhas:
            if resumo not in vistos:
                vistos[resumo] = registro_id
                continue

            original_id = vistos[resumo]

            self.db.execute("""
                INSERT INTO duplicatas_registros (
                    hash_duplicado,
                    arquivo_duplicado,
                    registro_original_id,
                    removido_utc
                )
                VALUES (?, ?, ?, ?)
            """, (
                resumo,
                origem,
                original_id,
                agora()
            ))

            self.db.execute(
                f"DELETE FROM {self.tabela} WHERE id = ?",
                (registro_id,)
            )
            removidos += 1

        self.db.commit()
        return removidos

    def fechar(self):
        self.db.commit()
        self.db.close()


# =========================================================
# MÓDULOS 1, 2, 3, 5 E 6
# =========================================================

class ModuloGraficos(ModuloBase):
    def __init__(self):
        super().__init__("graficos")


class ModuloSinais(ModuloBase):
    def __init__(self):
        super().__init__("sinais")


class ModuloMemoria(ModuloBase):
    def __init__(self):
        super().__init__("memoria")


class ModuloEstatisticasM5(ModuloBase):
    def __init__(self):
        super().__init__("estatisticas_m5")


class ModuloResultadosOperacoes(ModuloBase):
    def __init__(self):
        super().__init__("resultados_operacoes")


# =========================================================
# MÓDULO 4 — CÓDIGOS E OPERAÇÕES ATIVAS
# =========================================================

class ModuloOperacoes(ModuloBase):

    def __init__(self):
        super().__init__("operacoes")

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS controle_codigos_operacoes (
                sinal_id TEXT PRIMARY KEY,
                codigo_operacao TEXT NOT NULL UNIQUE,
                criado_utc TEXT NOT NULL
            )
        """)

        self.db.commit()

    def recuperar_codigos_antigos(self):
        linhas = self.db.execute("""
            SELECT payload_json
            FROM operacoes
        """).fetchall()

        for (texto,) in linhas:
            try:
                item = json.loads(texto)
            except Exception:
                continue

            codigo = (
                item.get("codigo_operacao")
                or item.get("id")
                or item.get("operacao_id")
            )
            sinal_id = (
                item.get("sinal_run_id")
                or item.get("id_sinal")
                or item.get("signal_id")
            )

            if (
                sinal_id
                and isinstance(codigo, str)
                and codigo.startswith("OP-")
            ):
                self.db.execute("""
                    INSERT OR IGNORE INTO controle_codigos_operacoes
                    (sinal_id, codigo_operacao, criado_utc)
                    VALUES (?, ?, ?)
                """, (str(sinal_id), codigo, agora()))

        self.db.commit()

    def proximo_codigo(self):
        codigos = self.db.execute("""
            SELECT codigo_operacao
            FROM controle_codigos_operacoes
        """).fetchall()

        maior = 0
        for (codigo,) in codigos:
            try:
                maior = max(
                    maior,
                    int(str(codigo).replace("OP-", ""))
                )
            except ValueError:
                continue

        return f"OP-{maior + 1:06d}"

    def obter_codigo(self, sinal_id):
        sinal_id = str(sinal_id)

        existente = self.db.execute("""
            SELECT codigo_operacao
            FROM controle_codigos_operacoes
            WHERE sinal_id = ?
        """, (sinal_id,)).fetchone()

        if existente:
            return existente[0]

        codigo = self.proximo_codigo()

        self.db.execute("""
            INSERT INTO controle_codigos_operacoes (
                sinal_id, codigo_operacao, criado_utc
            )
            VALUES (?, ?, ?)
        """, (sinal_id, codigo, agora()))

        self.db.commit()
        return codigo

    def carregar_confirmadas(self):
        if not ARQUIVO_ESTADO.is_file():
            return []

        estado = json.loads(
            ARQUIVO_ESTADO.read_text(encoding="utf-8")
        )

        operacoes = estado.get("operacoes", [])

        if not isinstance(operacoes, list):
            operacoes = []

        individual = estado.get("operacao")
        if isinstance(individual, dict):
            operacoes.append(individual)

        ativas = []
        sinais_vistos = set()

        for original in operacoes:
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
                # Sem identificador do sinal não é possível
                # confirmar a identidade de forma segura.
                continue

            sinal_id = str(sinal_id)

            if sinal_id in sinais_vistos:
                continue

            sinais_vistos.add(sinal_id)
            item = dict(original)

            codigo = self.obter_codigo(sinal_id)
            item["codigo_operacao"] = codigo
            item["id"] = codigo
            item["sinal_run_id"] = sinal_id

            ativas.append(item)

        return ativas

    def reconstruir_banco_ativo(self):
        self.recuperar_codigos_antigos()

        # A tabela principal é sempre reconstruída a partir
        # das operações com ENTREI confirmado e status ATIVA.
        self.db.execute("DELETE FROM operacoes")

        ativas = self.carregar_confirmadas()

        for numero, operacao in enumerate(ativas, start=1):
            sinal_id = str(operacao["sinal_run_id"])
            self.gravar(
                "ENTREI_CONFIRMADO",
                "operacao_ativa",
                numero,
                operacao,
                sinal_id
            )

        self.db.commit()
        return len(ativas)


# =========================================================
# ORGANIZADOR CENTRAL
# =========================================================

class OrganizadorXAUUSD:

    def __init__(self):
        self.modulos = {
            "graficos": ModuloGraficos(),
            "sinais": ModuloSinais(),
            "memoria": ModuloMemoria(),
            "operacoes": ModuloOperacoes(),
            "estatisticas_m5": ModuloEstatisticasM5(),
            "resultados_operacoes": ModuloResultadosOperacoes(),
        }

        self.relatorio = {
            "versao": 4,
            "data_utc": agora(),
            "status": "INICIADO",
            "modulos": {},
            "arquivos_sem_classificacao": [],
            "erros": [],
            "duplicatas_removidas": {},
            "operacoes_ativas": 0,
        }

    def arquivos_para_processar(self):
        bancos = {
            caminho.resolve()
            for caminho in BANCOS.values()
        }

        encontrados = []

        for arquivo in DATA.rglob("*"):
            if not arquivo.is_file():
                continue

            if arquivo.resolve() in bancos:
                continue

            if arquivo.name.startswith("."):
                continue

            if arquivo.name == ARQUIVO_RELATORIO.name:
                continue

            if arquivo.suffix.lower() not in EXTENSOES:
                continue

            encontrados.append(arquivo)

        return sorted(encontrados)

    def registrar_referencias(self):
        for modulo in self.modulos.values():
            for outro_nome, outro in self.modulos.items():
                if modulo.grupo == outro_nome:
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
                    outro_nome,
                    outro.caminho_banco.name,
                    hash_banco,
                    agora()
                ))

            modulo.db.commit()

    def executar(self):
        try:
            print("=" * 60)
            print("ORGANIZADOR XAUUSD — SEIS MÓDULOS")
            print("=" * 60)

            for arquivo in self.arquivos_para_processar():
                grupo = classificar(arquivo)

                if grupo is None:
                    self.relatorio[
                        "arquivos_sem_classificacao"
                    ].append(
                        arquivo.relative_to(ROOT).as_posix()
                    )
                    continue

                # O estado de ENTREI é usado diretamente pelo
                # módulo de operações, não importado como histórico.
                if (
                    grupo == "operacoes"
                    and arquivo.name == ARQUIVO_ESTADO.name
                ):
                    continue

                modulo = self.modulos[grupo]
                resultado = modulo.processar(arquivo)

                print(
                    f"[{grupo.upper()}] "
                    f"{arquivo.name}: {resultado}"
                )

            # Deduplicação exata em cada banco.
            for nome, modulo in self.modulos.items():
                if nome == "operacoes":
                    continue

                removidos = modulo.eliminar_duplicatas()
                self.relatorio[
                    "duplicatas_removidas"
                ][nome] = removidos

            # Operações ativas são reconstruídas exclusivamente
            # a partir do estado criado pelo comando ENTREI.
            modulo_operacoes = self.modulos["operacoes"]
            quantidade_ativas = (
                modulo_operacoes.reconstruir_banco_ativo()
            )

            self.relatorio["operacoes_ativas"] = quantidade_ativas

            # Conferência de integridade e cruzamento dos bancos.
            self.registrar_referencias()

            for nome, modulo in self.modulos.items():
                integridade = modulo.db.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

                total = modulo.db.execute(
                    f"SELECT COUNT(*) FROM {modulo.tabela}"
                ).fetchone()[0]

                origens = modulo.db.execute(
                    "SELECT COUNT(*) FROM arquivos_origem"
                ).fetchone()[0]

                duplicatas = modulo.db.execute(
                    "SELECT COUNT(*) FROM duplicatas_registros"
                ).fetchone()[0]

                self.relatorio["modulos"][nome] = {
                    "banco": modulo.caminho_banco.name,
                    "registros": total,
                    "arquivos_origem": origens,
                    "duplicatas_eliminadas_acumuladas": duplicatas,
                    "integridade": integridade,
                }

                self.relatorio["erros"].extend(modulo.erros)

                if integridade != "ok":
                    self.relatorio["erros"].append({
                        "banco": nome,
                        "erro": "Falha no PRAGMA integrity_check"
                    })

            sem_grupo = len(
                self.relatorio["arquivos_sem_classificacao"]
            )

            total_erros = len(self.relatorio["erros"])

            self.relatorio["total_arquivos_sem_grupo"] = sem_grupo
            self.relatorio["total_erros"] = total_erros

            self.relatorio["status"] = (
                "SUCESSO"
                if total_erros == 0 and sem_grupo == 0
                else "CONCLUIDO_COM_PENDENCIAS"
            )

            ARQUIVO_RELATORIO.write_text(
                json_texto(self.relatorio),
                encoding="utf-8"
            )

            print("=" * 60)
            print("OPERAÇÕES ATIVAS:", quantidade_ativas)

            for nome, dados in self.relatorio["modulos"].items():
                print(
                    nome.upper(),
                    "| registros:", dados["registros"],
                    "| integridade:", dados["integridade"]
                )

            print("Arquivos sem classificação:", sem_grupo)
            print("Erros:", total_erros)
            print("STATUS:", self.relatorio["status"])
            print("=" * 60)

            return self.relatorio

        finally:
            for modulo in self.modulos.values():
                modulo.fechar()


if __name__ == "__main__":
    resultado = OrganizadorXAUUSD().executar()

    if (
        resultado["total_erros"] > 0
        or resultado["total_arquivos_sem_grupo"] > 0
    ):
        raise SystemExit(1)

    print("ORGANIZADOR XAUUSD FINALIZADO.")
