-- ESQUEMA OFICIAL XAUUSD
-- SQLite / Cloudflare D1
-- Exatamente 8 tabelas. Nenhum dado ou modulo e migrado por este arquivo.
-- Retencao: os modulos deverao executar a limpeza dos registros com mais de 10 dias
-- somente depois de confirmar a gravacao/validacao dos novos dados.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS m5 (
    data_hora TEXT PRIMARY KEY,
    abertura REAL NOT NULL,
    maxima REAL NOT NULL,
    minima REAL NOT NULL,
    fechamento REAL NOT NULL,
    volume REAL,
    fonte TEXT,
    CHECK (maxima >= abertura AND maxima >= fechamento AND maxima >= minima),
    CHECK (minima <= abertura AND minima <= fechamento AND minima <= maxima)
);

CREATE TABLE IF NOT EXISTS m15 (
    data_hora TEXT PRIMARY KEY,
    abertura REAL NOT NULL,
    maxima REAL NOT NULL,
    minima REAL NOT NULL,
    fechamento REAL NOT NULL,
    volume REAL,
    fonte TEXT,
    CHECK (maxima >= abertura AND maxima >= fechamento AND maxima >= minima),
    CHECK (minima <= abertura AND minima <= fechamento AND minima <= maxima)
);

CREATE TABLE IF NOT EXISTS h1 (
    data_hora TEXT PRIMARY KEY,
    abertura REAL NOT NULL,
    maxima REAL NOT NULL,
    minima REAL NOT NULL,
    fechamento REAL NOT NULL,
    volume REAL,
    fonte TEXT,
    CHECK (maxima >= abertura AND maxima >= fechamento AND maxima >= minima),
    CHECK (minima <= abertura AND minima <= fechamento AND minima <= maxima)
);

-- Uma linha por horario; os quatro indicadores ficam em colunas proprias.
CREATE TABLE IF NOT EXISTS indicadores (
    timeframe TEXT NOT NULL CHECK (timeframe IN ('M5','M15','H1')),
    data TEXT NOT NULL,
    hora TEXT NOT NULL,
    preco REAL NOT NULL,
    ema9 REAL,
    ema21 REAL,
    rsi REAL,
    macd REAL,
    atr REAL,
    PRIMARY KEY (timeframe, data, hora)
);

-- Registro unificado do Tabuleiro: estado atual e distribuicoes por horizonte.
CREATE TABLE IF NOT EXISTS tabuleiro (
    id INTEGER PRIMARY KEY,
    data_hora TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe IN ('M5','M15','H1')),
    tipo_registro TEXT NOT NULL CHECK (tipo_registro IN ('ESTADO','PROBABILIDADE')),
    sequencia_json TEXT,
    quantidade_candles INTEGER,
    ocorrencias_encontradas INTEGER,
    ocorrencias_exatas INTEGER,
    distancia_maxima INTEGER,
    horizonte INTEGER,
    evento_futuro TEXT,
    ocorrencias INTEGER,
    total_amostras INTEGER,
    probabilidade REAL,
    distancia_media REAL,
    versao TEXT,
    CHECK (
        (tipo_registro = 'ESTADO' AND sequencia_json IS NOT NULL)
        OR
        (tipo_registro = 'PROBABILIDADE' AND horizonte IS NOT NULL AND evento_futuro IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS sinais_validados (
    codigo TEXT PRIMARY KEY,
    data_hora TEXT NOT NULL,
    direcao TEXT,
    entrada REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    classificacao TEXT NOT NULL,
    justificativa TEXT,
    modelo TEXT,
    pontuacao REAL,
    resposta_json TEXT
);

CREATE TABLE IF NOT EXISTS sinais_negados (
    codigo TEXT PRIMARY KEY,
    data_hora TEXT NOT NULL,
    direcao TEXT,
    classificacao TEXT NOT NULL,
    justificativa TEXT NOT NULL,
    modelo TEXT,
    pontuacao REAL,
    resposta_json TEXT
);

CREATE TABLE IF NOT EXISTS ranking (
    codigo TEXT PRIMARY KEY,
    data_hora TEXT NOT NULL,
    classificacao_gemini TEXT NOT NULL,
    direcao TEXT,
    entrada REAL,
    stop_loss REAL,
    take_profit REAL,
    pontuacao REAL,
    resultado_usdt REAL,
    status_resultado TEXT NOT NULL DEFAULT 'PENDENTE',
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_m5_data_hora ON m5(data_hora);
CREATE INDEX IF NOT EXISTS idx_m15_data_hora ON m15(data_hora);
CREATE INDEX IF NOT EXISTS idx_h1_data_hora ON h1(data_hora);
CREATE INDEX IF NOT EXISTS idx_indicadores_data_hora ON indicadores(timeframe, data, hora);
CREATE INDEX IF NOT EXISTS idx_tabuleiro_data_hora ON tabuleiro(data_hora);
CREATE INDEX IF NOT EXISTS idx_sinais_validados_data_hora ON sinais_validados(data_hora);
CREATE INDEX IF NOT EXISTS idx_sinais_negados_data_hora ON sinais_negados(data_hora);
CREATE INDEX IF NOT EXISTS idx_ranking_data_hora ON ranking(data_hora);

-- Limpeza de retencao: executar pelos modulos apos gravacao confirmada.
-- Datas armazenadas em UTC no formato ISO 8601.
-- DELETE FROM m5 WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM m15 WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM h1 WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM indicadores WHERE (data || 'T' || hora) < strftime('%Y-%m-%dT%H:%M:%S','now','-10 days');
-- DELETE FROM tabuleiro WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM sinais_validados WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM sinais_negados WHERE data_hora < datetime('now','-10 days');
-- DELETE FROM ranking WHERE data_hora < datetime('now','-10 days');
