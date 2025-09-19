# Análise das Tarefas IA

Serviço de análise inteligente de tarefas para o sistema Tandera. Utiliza Anthropic Claude para identificar similaridades entre action_items de reuniões e tarefas existentes, propondo merges automáticos.

## Funcionalidades

- Análise de similaridade entre action_items e tarefas existentes
- Propostas de merge com justificativas da IA
- Integração com Supabase para acesso aos dados
- Sistema de fallback para quando o serviço está offline
- API REST para integração com transcription-app

## Configuração

1. Instalar dependências:
```bash
poetry install
```

2. Configurar variáveis de ambiente:
```bash
cp .env.example .env
# Editar .env com suas chaves
```

3. Executar o serviço:
```bash
poetry run fastapi dev app/main.py
```

## API Endpoints

- `GET /api/health` - Verificação de saúde
- `POST /api/analyze-action-items` - Análise de action_items
- `POST /api/propose-merge` - Execução de merge proposto
- `GET /api/test-anthropic` - Teste de conexão com Anthropic

## Integração

O serviço é chamado automaticamente após a criação de action_items no transcription-app. Se o serviço estiver offline, os action_items recebem a tag "não foi revisto pela LIA".

## Arquitetura

- FastAPI para API REST
- Anthropic Claude para análise de similaridade
- Supabase para acesso aos dados
- Sistema de autenticação por API key
