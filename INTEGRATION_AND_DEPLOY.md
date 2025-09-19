### Integração e Deploy - Análise das Tarefas IA

#### Visão Geral
Este serviço analisa action_items de uma reunião e identifica similaridade/continuidade com tarefas existentes do projeto (Supabase). Quando há correspondência, retorna propostas de merge para o front aprovar.

#### Dependências e Versões
- Python 3.12
- FastAPI ^0.104.1, Uvicorn ^0.24.0
- supabase ^2.8.1, httpx ^0.27.2

#### Variáveis de Ambiente
Veja `.env.example`. Necessárias em produção (Railway):
- API_KEY
- SUPABASE_URL
- SUPABASE_KEY (use Service Role)
- ANTHROPIC_API_KEY
 - ANTHROPIC_MODEL (opcional, default: `claude-3-haiku-20240307`)

#### Deploy no Railway
1. Conecte o repositório.
2. Procfile já define:
   `web: poetry run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Se usar "Custom Start Command", copie exatamente o comando do Procfile.
4. Defina as variáveis de ambiente acima.
5. Runtime Python 3.12 (Nixpacks detecta automaticamente via Poetry/pyproject).

Se estiver atualizando de versões antigas, remova o lockfile no repositório para o resolver gerar versões compatíveis em build.

#### Modelo da Anthropic
- O serviço usa a Messages API e permite parametrizar o modelo por `ANTHROPIC_MODEL`.
- Recomendados: `claude-3-haiku-20240307` (rápido/barato) ou `claude-3-5-sonnet-20241022`.

#### Endpoints do Serviço
- GET `/api/health` → status
- POST `/api/analyze-action-items` → retorna `merge_proposals` (ou vazio)
- POST `/api/propose-merge` → aplica merge (atualiza `kanban_tasks` e marca `action_items` como `merged`)
- GET `/api/test-anthropic` → testa Anthropic

Header obrigatório em endpoints protegidos: `X-Api-Key: {API_KEY}`

#### Contrato de Integração (transcription-app)
Acionar logo após o endereçamento da reunião a um projeto, antes da etapa de aprovação das tarefas.

Request (POST `/api/analyze-action-items`):
```json
{
  "project_id": "uuid-do-projeto",
  "meeting_title": "Título da reunião",
  "meeting_summary": "Resumo curto",
  "action_items": [
    { "id": "uuid-ai-1", "description": "texto...", "responsible": "Nome", "priority": "medium" }
  ]
}
```

Response (exemplo):
```json
{
  "analyzed": true,
  "merge_proposals": [
    {
      "parent_task_id": "uuid-task",
      "child_action_items": ["uuid-ai-1"],
      "similarity_score": 0.84,
      "proposed_title": "Novo título",
      "proposed_status": "in_progress",
      "proposed_description": "Descrição proposta",
      "reasoning": "Justificativa"
    }
  ],
  "message": "Encontradas 1 propostas de merge",
  "lia_reviewed": true
}
```

Fallback: se erro/timeout, marcar a tarefa no front com a tag "Não auditado LIA".

Aplicar merge (POST `/api/propose-merge`):
```json
{
  "parent_task_id": "uuid-task",
  "child_action_items": ["uuid-ai-1"],
  "similarity_score": 0.84,
  "proposed_title": "Novo título",
  "proposed_status": "in_progress",
  "proposed_description": "Descrição proposta",
  "reasoning": "Justificativa"
}
```

Efeito: atualiza a tarefa mãe em `kanban_tasks` e marca os `action_items` como `merged`.

#### Requisitos no transcription-app (instruções para outro repositório)
1. Disparo da análise: após endereçar a reunião a um projeto, fazer fetch para este serviço.
   - Header: `X-Api-Key`
   - Body: conforme contrato acima, usando `project_id`, `meeting_title`, `meeting_summary` e os `action_items` recém-criados.
2. Exibição no UI:
   - Tarefa nova: fluxo normal.
   - Atualização sugerida: exibir rótulo, diff (vermelho antigo / verde novo) e botão "Aprovar merge".
3. Histórico: persistir no card a decisão (aprovado/recusado) com trechos comparados e timestamp.
   - Sugestão: criar tabela `task_merge_history` ou campo JSON `merge_history` em `kanban_tasks`.
4. Fallback: em erro, aplicar tag "Não auditado LIA" no card.
5. Aprovação: ao clicar, chamar `/api/propose-merge` com a proposta retornada.

#### Teste fim-a-fim
1. GET `/api/health` ⇒ 200.
2. GET `/api/test-anthropic` ⇒ exige `ANTHROPIC_API_KEY` válido.
3. POST `/api/analyze-action-items` com dados reais de um `project_id`.
4. Validar `merge_proposals` e aplicar `/api/propose-merge`.
5. Confirmar atualizações no Supabase e no UI.

#### Observações
- Estados: `pending` ↔ "A Fazer", `in_progress` ↔ "Em Progresso".
- Busca no Supabase filtra últimas tarefas ativas do projeto e aplica heurística por título de reunião.

#### Nova Tabela: task_merge_history (criar no Supabase)
Execute o SQL abaixo no Supabase para registrar o histórico dos merges aprovados no front:

```sql
create table if not exists public.task_merge_history (
  id uuid primary key default gen_random_uuid(),
  parent_task_id uuid not null references public.kanban_tasks(id) on delete cascade,
  child_action_items uuid[] not null,
  similarity_score double precision,
  previous_title text,
  previous_status text,
  previous_description text,
  new_title text,
  new_status text,
  new_description text,
  reasoning text,
  created_at timestamptz not null default now()
);

-- Opcional: habilitar RLS conforme sua política de acesso
-- alter table public.task_merge_history enable row level security;
```

O serviço grava automaticamente um registro nesta tabela a cada merge aprovado via `/api/propose-merge`.
