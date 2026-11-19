# Módulo Gestor XAUUSD

Módulo interno responsável pela conversa com o usuário e pelo acompanhamento consultivo das operações.

## Fronteiras

- O Gestor possui inteligência Gemini própria, independente do Gemini Sinal da Central.
- Não gera nem altera sinais da Central.
- Não envia ordens à corretora; recomendações dependem de confirmação manual pelo Telegram.
- Consome os dados oficiais do projeto, sem criar cópias ou bancos espelhados.
- Memória conversacional e decisões do Gestor permanecem separadas das do Gemini Sinal.

## Canais de comunicação

Existem dois canais Telegram distintos no projeto:

1. **Telegram da Central:** permanece sob responsabilidade dos componentes e workflows atuais da Central. Este módulo não altera seu bot, token, webhook ou fluxo de sinais.
2. **Telegram do Gestor:** canal conversacional próprio, isolado por configurações `GESTOR_TELEGRAM_*`, atendido por `telegram.js` e `conversador.js`.

O canal do Gestor não encaminha mensagens para a Central, não dispara `repository_dispatch` e não usa o webhook da Central. Os tokens e segredos dos dois canais não devem ser compartilhados.

## Arquivos

- `validadores.js`: valida pacotes recebidos.
- `mercado.js`: organiza contexto de mercado recebido.
- `telegram.js`: webhook e envio de mensagens do canal Telegram do Gestor.
- `conversador.js`: coordena memória, contexto e resposta do Gemini Gestor por dependências próprias.
- `gemini_gestor.js`: inteligência própria do Gestor, com prompt independente, histórico contextual e fallback de modelos.
- `memoria.js`: memória conversacional persistente no D1 `gestor-xauusd-db`, usando as tabelas existentes `conversas` e `mensagens`.
- `acompanhamento.js`: avaliação consultiva de operações abertas.

## Gemini Gestor

- Usa `GESTOR_GEMINI_API_KEY` (ou `GEMINI_API_KEY` como compatibilidade técnica).
- Modelo principal: `GESTOR_GEMINI_MODEL`; na ausência, `GEMINI_MODEL` e depois `gemini-3.1-flash-lite`.
- Reservas opcionais: `GESTOR_GEMINI_MODELS_JSON`, contendo uma lista JSON de nomes de modelos.
- A seleção automática publicada pelo Testador da Central ainda não é consumida diretamente pelo Worker: o artefato `selecao-modelo-gemini` é temporário e o banco oficial não é acessível diretamente pelo Worker. A ligação desse resultado ao Gestor será feita por uma integração explícita, sem duplicar o Testador.

## Estado

Os módulos de validação, organização de mercado, webhook/envio Telegram, Gemini Gestor e memória D1 estão incorporados. O conversador agora recupera e salva o histórico automaticamente pelo binding `GESTOR_DB`. A ligação ao ponto de entrada do Worker ainda não foi ativada nem testada.\n\n### Acesso ao banco oficial XAUUSD\n\nO banco oficial `data/xauusd.db` permanece no repositório V2 e não é acessível diretamente pelo Worker Cloudflare como um arquivo SQLite local. Nenhum espelho ou segundo banco foi criado. Para consultar esses dados pelo Gestor, ainda é necessária uma ponte sob demanda entre o Worker e a Central, com resposta restrita aos dados solicitados. Essa ponte não foi criada nesta etapa para evitar alterar o workflow da Central ou conceder acesso amplo ao repositório.\n\nNenhum Worker, secret, webhook publicado, workflow da Central ou banco oficial foi alterado nesta etapa. A integração D1 foi incorporada ao código, mas depende da confirmação do binding já existente no Worker e de teste real.
