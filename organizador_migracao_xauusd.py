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

COMPACTADOS = {
    nome: Path(str(caminho) + ".gz")
    for nome, caminho in BANCOS.items()
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


def hash_bytes(conteudo):
    return hashlib.sha256(conteudo).hexdigest()


def preparar_bancos():
    """Restaura os bancos compactados antes de abrir o SQLite."""
    for nome, banco in BANCOS.items():
        compactado = COMPACTADOS[nome]

        if not banco.exists() and compactado.exists():
            banco.write_bytes(
                gzip.decompress(compactado.read_bytes())
            )


def compactar_bancos():
    """Compacta os bancos depois que todas as conexões forem fechadas."""
    for nome, banco in BANCOS.items():
        if banco.exists():
            dados = banco.read_bytes()

            COMPACTADOS[nome].write_bytes(
                gzip.compress(
                    dados,
                    compresslevel=9,
                    mtime=0
                )
            )

            banco.unlink()


def ler_estado():
    if not ARQUIVO_ESTADO.is_file():
        return {}

    try:
        objeto = json.loads(
            ARQUIVO_ESTADO.read_text(encoding="utf-8")
        )
        return objeto if isinstance(objeto, dict) else {}
    except Exception:
        return {}


def classificar(arquivo):
    nome = arquivo.name.lower()

    if nome == ARQUIVO_ESTADO.name.lower():
        return "operacoes"

    if any(x in nome for x in (
        "resultado-agente-gemini",
        "historico-agente-gemini",
        "decisao_gemini",
        "decisão_gemini",
        "historico_decisoes",
        "historico_decisões",
        "analise_tecnica_contexto",
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


class ModuloBase:

    def __init__(self, grupo):
        self.grupo = grupo
        self.tabela = grupo
        self.caminho_banco = BANCOS[grupo]
        self.db = sqlite3.connect(self.caminho_banco)

        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA foreign_keys=ON")

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

        self.db.commit()

    def gravar(self, caminho, tipo, numero, objeto, chave=None):
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

    def ler_json(self, caminho, conteudo):
        objeto = json.loads(
            conteudo.decode("utf-8-sig")
        )

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
        sha = hash_bytes(original)

        anterior = self.db.execute("""
            SELECT hash_sha256
            FROM controle_importacao
            WHERE caminho = ?
        """, (caminho,)).fetchone()

        if anterior and anterior[0] == sha:
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
                sha,
                len(original),
                agora(),
                sqlite3.Binary(original)
            ))

            self.db.execute(
                f"DELETE FROM {self.tabela} WHERE arquivo_origem=?",
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
                bruto = gzip.decompress(original)

                if nome.endswith(".json.gz"):
                    quantidade = self.ler_json(caminho, bruto)
                else:
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
                sha,
                "SUCESSO",
                agora()
            ))

            self.db.commit()
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


class ModuloOperacoes(ModuloBase):

    def __init__(self):
        super().__init__("operacoes")

    def codigo_existente(self, sinal_id, anterior):
        # Mantém o código da operação enquanto ela continuar ativa.
        if sinal_id in anterior:
            return anterior[sinal_id]

        return None

    def reconstruir_ativas(self):
        estado = ler_estado()

        # Guarda os códigos das operações que ainda estavam ativas
        # antes de limpar a tabela.
        anteriores = {}

        linhas = self.db.execute("""
            SELECT payload_json
            FROM operacoes
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

        # A tabela será reconstruída somente com ATIVAS.
        self.db.execute("DELETE FROM operacoes")

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

            status = str(
                original.get("status", "")
            ).strip().upper()

            # Somente operações efetivamente ativas entram no banco.
            if status != "ATIVA":
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
                or item.get("operacao_id")
                or item.get("id")
                or self.codigo_existente(sinal_id, anteriores)
            )

            if not isinstance(codigo, str) or not re.fullmatch(
                r"OP-[0-9]{6,}", codigo
            ):
                while f"OP-{numero:06d}" in codigos_usados:
                    numero += 1

                codigo = f"OP-{numero:06d}"
                numero += 1

            if codigo in codigos_usados:
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

        for indice, item in enumerate(ativos, start=1):
            self.gravar(
                "ENTREI_CONFIRMADO",
                "operacao_ativa",
                indice,
                item,
                item["sinal_run_id"]
            )

        self.db.commit()
        return len(ativos)


class OrganizadorXAUUSD:

    def __init__(self):
        self.modulos = {
            "graficos": ModuloBase("graficos"),
            "sinais": ModuloBase("sinais"),
            "memoria": ModuloBase("memoria"),
            "operacoes": ModuloOperacoes(),
            "estatisticas_m5": ModuloBase("estatisticas_m5"),
            "resultados_operacoes": ModuloBase("resultados_operacoes"),
        }

        self.relatorio = {
            "versao": 5,
            "data_utc": agora(),
            "status": "INICIADO",
            "modulos": {},
            "arquivos_sem_classificacao": [],
            "erros": [],
            "operacoes_ativas": 0,
        }

    def arquivos_para_processar(self):
        bancos = {
            caminho.resolve()
            for caminho in BANCOS.values()
        }

        compactados = {
            caminho.resolve()
            for caminho in COMPACTADOS.values()
        }

        encontrados = []

        for arquivo in DATA.rglob("*"):
            if not arquivo.is_file():
                continue

            if arquivo.resolve() in bancos:
                continue

            if arquivo.resolve() in compactados:
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

                resumo = hash_bytes(
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
                    resumo,
                    agora()
                ))

            modulo.db.commit()

    def executar(self):
        try:
            print("ORGANIZADOR XAUUSD")

            for arquivo in self.arquivos_para_processar():
                grupo = classificar(arquivo)

                if grupo is None:
                    self.relatorio[
                        "arquivos_sem_classificacao"
                    ].append(
                        arquivo.relative_to(ROOT).as_posix()
                    )
                    continue

                # O estado é lido diretamente pelo módulo de operações.
                # Não é importado como histórico de operações.
                if (
                    grupo == "operacoes"
                    and arquivo.name == ARQUIVO_ESTADO.name
                ):
                    continue

                resultado = self.modulos[grupo].processar(arquivo)

                print(
                    f"[{grupo.upper()}] "
                    f"{arquivo.name}: {resultado}"
                )

            quantidade = (
                self.modulos["operacoes"].reconstruir_ativas()
            )

            self.relatorio["operacoes_ativas"] = quantidade

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

                self.relatorio["modulos"][nome] = {
                    "banco": COMPACTADOS[nome].name,
                    "registros": total,
                    "arquivos_origem": origens,
                    "integridade": integridade,
                }

                self.relatorio["erros"].extend(modulo.erros)

                if integridade != "ok":
                    self.relatorio["erros"].append({
                        "banco": nome,
                        "erro": "Falha no integrity_check"
                    })

            sem_grupo = len(
                self.relatorio["arquivos_sem_classificacao"]
            )

            erros = len(self.relatorio["erros"])

            self.relatorio["total_arquivos_sem_grupo"] = sem_grupo
            self.relatorio["total_erros"] = erros

            self.relatorio["status"] = (
                "SUCESSO"
                if erros == 0 and sem_grupo == 0
                else "CONCLUIDO_COM_PENDENCIAS"
            )

            ARQUIVO_RELATORIO.write_text(
                json_texto(self.relatorio),
                encoding="utf-8"
            )

            print("OPERAÇÕES ATIVAS:", quantidade)

            for nome, dados in self.relatorio["modulos"].items():
                print(
                    nome.upper(),
                    "| registros:", dados["registros"],
                    "| integridade:", dados["integridade"]
                )

            print("Arquivos sem classificação:", sem_grupo)
            print("Erros:", erros)
            print("STATUS:", self.relatorio["status"])

            return self.relatorio

        finally:
            for modulo in self.modulos.values():
                modulo.fechar()


if __name__ == "__main__":
    preparar_bancos()

    resultado = OrganizadorXAUUSD().executar()

    compactar_bancos()

    if (
        resultado["total_erros"] > 0
        or resultado["total_arquivos_sem_grupo"] > 0
    ):
        raise SystemExit(1)

    print("ORGANIZADOR XAUUSD FINALIZADO.")
