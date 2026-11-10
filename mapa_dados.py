import os

# ==============================================================================
# 1. OS 6 FUNIS FINAIS (As únicas saídas limpas que o sistema vai consumir)
# ==============================================================================
FUNIL_6_SAIDAS = {
    "01_mercado": "data/funil/01_mercado_limpo.json",
    "02_sinais": "data/funil/02_sinais_limpos.json",
    "03_operacoes": "data/funil/03_operacoes_limpas.json",
    "04_alertas": "data/funil/04_alertas_estado.json",
    "05_memoria": "data/funil/05_memoria_consolidada.json",
    "06_inteligencia": "data/funil/06_briefing_ia.json",
}

# ==============================================================================
# 2. A PONTE (Mapeia os nomes antigos -> para as pastas organizadas)
# ==============================================================================
PONTE_ARQUIVOS = {
    # --- Módulo 1: Velas e Mercado ---
    "xauusd_m5.csv": "data/01_mercado/xauusd_m5.csv",
    "xauusd_m5_fechados.csv": "data/01_mercado/xauusd_m5_fechados.csv",
    "velas_m5_ativas_90d.csv": "data/01_mercado/velas_m5_ativas_90d.csv",
    "historico_velas_m5.csv": "data/01_mercado/historico_velas_m5.csv",
    "organizado_01_graficos_velas.json": "data/01_mercado/organizado_01_graficos_velas.json",

    # --- Módulo 2: Sinais e Decisões ---
    "marcadores_sinais_xau.json": "data/02_sinais/marcadores_sinais_xau.json",
    "ranking_sinais_xau.json": "data/02_sinais/ranking_sinais_xau.json",
    "historico_decisoes_xau.json": "data/02_sinais/historico_decisoes_xau.json",
    "decisao_agente_operacional.json": "data/02_sinais/decisao_agente_operacional.json",
    "organizado_02_gerador_sinais.json": "data/02_sinais/organizado_02_gerador_sinais.json",

    # --- Módulo 3: Operações e Resultados ---
    "resultados_operacoes.csv": "data/03_operacoes/resultados_operacoes.csv",
    "resultados_operacoes.json": "data/03_operacoes/resultados_operacoes.json",
    "resumo_resultados_m5.csv": "data/03_operacoes/resumo_resultados_m5.csv",
    "organizado_04_condutor_operacoes.json": "data/03_operacoes/organizado_04_condutor_operacoes.json",

    # --- Módulo 4: Alertas e Acompanhamento ---
    "acompanhamento_autonomo_estado.json": "data/04_alertas/acompanhamento_autonomo_estado.json",
    "contexto_inteligente_acompanhador.json": "data/04_alertas/contexto_inteligente_acompanhador.json",
    "condutor_ultima_memoria_run.json": "data/04_alertas/condutor_ultima_memoria_run.json",
    "pacote_agente_operacional.json": "data/04_alertas/pacote_agente_operacional.json",
    "pacote_memoria_condutor.json": "data/04_alertas/pacote_memoria_condutor.json",

    # --- Módulo 5: Organizador de Memória ---
    "cadastro_mestre_operacoes.json": "data/05_memoria/cadastro_mestre_operacoes.json",
    "indice_arquivos_memoria.json": "data/05_memoria/indice_arquivos_memoria.json",
    "relatorio_organizador_memoria.json": "data/05_memoria/relatorio_organizador_memoria.json",
    "memoria_agente.csv": "data/05_memoria/memoria_agente.csv",
    "memoria_agente.json": "data/05_memoria/memoria_agente.json",
    "memoria_gemini_diaria.json": "data/05_memoria/memoria_gemini_diaria.json",
    "historico_conexoes_gemini.json": "data/05_memoria/historico_conexoes_gemini.json",
    "organizado_03_memoria_persistente.json": "data/05_memoria/organizado_03_memoria_persistente.json",
    "organizado_05_organizador_memoria.json": "data/05_memoria/organizado_05_organizador_memoria.json",

    # --- Módulo 6: Tabuleiro, Estatísticas e Inteligência ---
    "tabuleiro_m5.db": "data/06_tabuleiro_ia/tabuleiro_m5.db",
    "tabuleiro_m5.csv": "data/06_tabuleiro_ia/tabuleiro_m5.csv",
    "tabuleiro_estavel_m5.csv": "data/06_tabuleiro_ia/tabuleiro_estavel_m5.csv",
    "contexto_tabuleiro_m5.json": "data/06_tabuleiro_ia/contexto_tabuleiro_m5.json",
    "relatorio_tabuleiro_m5.json": "data/06_tabuleiro_ia/relatorio_tabuleiro_m5.json",
    "relatorio_geral_tabuleiro_m5.csv": "data/06_tabuleiro_ia/relatorio_geral_tabuleiro_m5.csv",
    "eventos_bordas_m5.csv": "data/06_tabuleiro_ia/eventos_bordas_m5.csv",
    "estatisticas_regioes_m5.csv": "data/06_tabuleiro_ia/estatisticas_regioes_m5.csv",
    "proximas_regioes_m5.json": "data/06_tabuleiro_ia/proximas_regioes_m5.json",
    "regioes_estaveis_m5.csv": "data/06_tabuleiro_ia/regioes_estaveis_m5.csv",
    "transicoes_estaveis_m5.csv": "data/06_tabuleiro_ia/transicoes_estaveis_m5.csv",
    "transicoes_probabilidade_m5.csv": "data/06_tabuleiro_ia/transicoes_probabilidade_m5.csv",
    "sequencias_provaveis_m5.csv": "data/06_tabuleiro_ia/sequencias_provaveis_m5.csv",
    "resumo_estatistico_m5.json": "data/06_tabuleiro_ia/resumo_estatistico_m5.json",
    "analise_tecnica_contexto.json": "data/06_tabuleiro_ia/analise_tecnica_contexto.json",
    "organizado_06_estatisticas_consolidacao.json": "data/06_tabuleiro_ia/organizado_06_estatisticas_consolidacao.json",
}

def preparar_pastas():
    """Cria toda a estrutura de pastas automaticamente se ainda não existir."""
    pastas = set(os.path.dirname(p) for p in PONTE_ARQUIVOS.values())
    pastas.update(os.path.dirname(p) for p in FUNIL_6_SAIDAS.values())
    for pasta in pastas:
        os.makedirs(pasta, exist_ok=True)

def caminho(nome_arquivo):
    """
    Função de ponte: recebe o nome antigo do arquivo e devolve o novo caminho.
    Se o arquivo ainda não estiver na lista, mantém o padrão antigo em 'data/'.
    """
    preparar_pastas()
    # Remove 'data/' caso o script antigo já passe o caminho completo
    nome_limpo = os.path.basename(nome_arquivo)
    return PONTE_ARQUIVOS.get(nome_limpo, os.path.join("data", nome_limpo))

def saida_funil(chave_modulo):
    """Retorna o caminho de um dos 6 arquivos finais limpos."""
    preparar_pastas()
    return FUNIL_6_SAIDAS[chave_modulo]
