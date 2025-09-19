import anthropic
import os
import json
from typing import List, Dict, Any
from ..models.analysis_models import ActionItem, ExistingTask, MergeProposal

class AnthropicService:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
    
    async def test_connection(self) -> str:
        try:
            response = self.client.completions.create(
                model="claude-instant-1.2",
                prompt="Human: Responda apenas 'Conexão OK' se você conseguir me ouvir.\n\nAssistant:",
                max_tokens_to_sample=50
            )
            return response.completion.strip()
        except Exception as e:
            raise Exception(f"Erro na conexão com Anthropic: {str(e)}")
    
    async def analyze_task_similarity(
        self,
        action_items: List[ActionItem],
        existing_tasks: List[ExistingTask],
        meeting_title: str,
        meeting_summary: str = None
    ) -> List[MergeProposal]:
        
        prompt = self._build_analysis_prompt(
            action_items, existing_tasks, meeting_title, meeting_summary
        )
        
        try:
            response = self.client.completions.create(
                model="claude-instant-1.2",
                prompt=f"Human: {prompt}\n\nAssistant:",
                max_tokens_to_sample=2000
            )
            
            response_text = response.completion.strip()
            return self._parse_analysis_response(response_text, action_items, existing_tasks)
            
        except Exception as e:
            raise Exception(f"Erro na análise Anthropic: {str(e)}")
    
    def _build_analysis_prompt(
        self,
        action_items: List[ActionItem],
        existing_tasks: List[ExistingTask],
        meeting_title: str,
        meeting_summary: str = None
    ) -> str:
        
        action_items_text = "\n".join([
            f"- ID: {item.id}, Descrição: {item.description}, Responsável: {item.responsible or 'Não definido'}"
            for item in action_items
        ])
        
        existing_tasks_text = "\n".join([
            f"- ID: {task.id}, Título: {task.title}, Status: {task.status}, Responsável: {task.responsible or 'Não definido'}, Descrição: {task.description or 'Sem descrição'}"
            for task in existing_tasks
        ])
        
        return f"""
Você é um assistente especializado em análise de tarefas para o sistema Tandera. Sua função é identificar se novos action_items de uma reunião podem ser merges/atualizações de tarefas existentes.

REUNIÃO ATUAL:
Título: {meeting_title}
Resumo: {meeting_summary or 'Não disponível'}

NOVOS ACTION ITEMS:
{action_items_text}

TAREFAS EXISTENTES ATIVAS (status 'pending', 'in_progress'):
{existing_tasks_text}

INSTRUÇÕES:
1. Analise se algum dos novos action_items pode ser uma atualização/continuação de tarefas existentes
2. Considere similaridade de conteúdo, contexto e responsáveis
3. Para cada merge identificado, proponha:
   - Qual tarefa existente seria a "mãe" (parent_task_id)
   - Quais action_items seriam "filhos" (child_action_items)
   - Score de similaridade (0.0 a 1.0)
   - Novo título proposto para a tarefa mãe
   - Novo status proposto (pending, in_progress, completed)
   - Justificativa do merge

FORMATO DE RESPOSTA (JSON):
{{
  "merges": [
    {{
      "parent_task_id": "id_da_tarefa_existente",
      "child_action_items": ["id1", "id2"],
      "similarity_score": 0.85,
      "proposed_title": "Título atualizado baseado na evolução",
      "proposed_status": "in_progress",
      "proposed_description": "Descrição atualizada se necessário",
      "reasoning": "Explicação do por que este merge faz sentido"
    }}
  ]
}}

Se não houver merges relevantes, retorne: {{"merges": []}}

Seja criterioso - apenas sugira merges quando houver clara relação entre as tarefas.
"""
    
    def _parse_analysis_response(
        self,
        response_text: str,
        action_items: List[ActionItem],
        existing_tasks: List[ExistingTask]
    ) -> List[MergeProposal]:
        
        try:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start == -1 or json_end == 0:
                return []
            
            json_text = response_text[json_start:json_end]
            parsed = json.loads(json_text)
            
            proposals = []
            for merge in parsed.get("merges", []):
                proposal = MergeProposal(
                    parent_task_id=merge["parent_task_id"],
                    child_action_items=merge["child_action_items"],
                    similarity_score=merge["similarity_score"],
                    proposed_title=merge["proposed_title"],
                    proposed_status=merge["proposed_status"],
                    proposed_description=merge.get("proposed_description"),
                    reasoning=merge["reasoning"]
                )
                proposals.append(proposal)
            
            return proposals
            
        except Exception as e:
            return []
