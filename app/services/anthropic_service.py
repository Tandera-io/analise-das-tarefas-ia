import anthropic
import os
import json
import textwrap
from typing import List, Dict, Any
from ..models.analysis_models import ActionItem, ExistingTask, MergeProposal
from .supabase_service import SupabaseService
import logging
import time
import httpx

class AnthropicService:
    def __init__(self):
        # Forçar HTTP/1.1 e configurar timeouts/retries para evitar erros de handshake/TLS
        http_client = httpx.Client(
            http2=False,
            timeout=httpx.Timeout(60.0, connect=30.0, read=60.0, write=60.0),
            transport=httpx.HTTPTransport(retries=3)
        )
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            http_client=http_client
        )
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.supabase = SupabaseService()

    async def test_connection(self) -> str:
        try:
            start = time.perf_counter()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=50,
                messages=[
                    {
                        "role": "user",
                        "content": "Responda apenas 'Conexão OK' se você conseguir me ouvir."
                    }
                ]
            )
            text_parts = []
            for part in getattr(response, "content", []) or []:
                # SDKs podem retornar dicts ou objetos com .type/.text
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                else:
                    if getattr(part, "type", None) == "text":
                        text_parts.append(getattr(part, "text", ""))
            result = ("".join(text_parts)).strip() or ""
            logging.getLogger("analise_das_tarefas_ia").info(
                "anthropic.test duration_ms=%d", int((time.perf_counter() - start) * 1000)
            )
            return result
        except Exception as e:
            raise Exception(f"Erro na conexão com Anthropic: {str(e)}")

    async def analyze_task_similarity(
        self,
        action_items: List[ActionItem],
        existing_tasks: List[ExistingTask],
        meeting_title: str,
        meeting_summary: str = None
    ) -> List[MergeProposal]:
        # Buscar reuniões correlatas (até 3) com base no project_id e no campo "reuniao"
        project_id = existing_tasks[0].project_id if existing_tasks else None
        related_meetings: List[Dict[str, Any]] = []
        if project_id:
            try:
                related_meetings = await self.supabase.get_related_meetings(project_id, meeting_title)
            except Exception as _e:
                related_meetings = []

        system_prompt, user_prompt = self._build_analysis_prompt(
            action_items, existing_tasks, meeting_title, meeting_summary, related_meetings
        )

        try:
            start = time.perf_counter()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )

            text_parts = []
            for part in getattr(response, "content", []) or []:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                else:
                    if getattr(part, "type", None) == "text":
                        text_parts.append(getattr(part, "text", ""))
            response_text = ("".join(text_parts)).strip()
            logging.getLogger("analise_das_tarefas_ia").info(
                "anthropic.analyze duration_ms=%d chars=%d",
                int((time.perf_counter() - start) * 1000),
                len(response_text),
            )
            return self._parse_analysis_response(response_text, action_items, existing_tasks)

        except Exception as e:
            raise Exception(f"Erro na análise Anthropic: {str(e)}")

    def _build_analysis_prompt(
        self,
        action_items: List[ActionItem],
        existing_tasks: List[ExistingTask],
        meeting_title: str,
        meeting_summary: str = None,
        related_meetings: List[Dict[str, Any]] = None
    ) -> str:

        action_items_text = "\n".join([
            f"- ID: {item.id}, Descrição: {item.description}, Responsável: {item.responsible or 'Não definido'}"
            for item in action_items
        ])

        existing_tasks_text = "\n".join([
            f"- ID: {task.id}, Título: {task.title}, Status: {task.status}, Responsável: {task.responsible or 'Não definido'}, Descrição: {task.description or 'Sem descrição'}"
            for task in existing_tasks
        ])

        system = (
            "Você é um assistente especializado em análise de tarefas para o sistema Tandera. "
            "Responda ESTRITAMENTE em JSON conforme o formato especificado, sem comentários extras."
        )
        example = {
            "merges": [
                {
                    "parent_task_id": "id_da_tarefa_existente",
                    "child_action_items": ["id1", "id2"],
                    "similarity_score": 0.85,
                    "proposed_title": "Título atualizado baseado na evolução",
                    "proposed_status": "in_progress",
                    "proposed_description": "Descrição atualizada se necessário",
                    "reasoning": "Explicação do por que este merge faz sentido"
                }
            ]
        }
        empty_merges = {"merges": []}

        # Montar bloco de reuniões correlatas
        related_block_lines: List[str] = []
        if related_meetings:
            for rm in related_meetings:
                ai_lines: List[str] = []
                for ai in rm.get("action_items", []) or []:
                    ai_lines.append(
                        f"    - id: {ai.get('id')} | descrição: {ai.get('description')} | responsável: {ai.get('responsible') or 'Não definido'}"
                    )
                ai_text = "\n".join(ai_lines) if ai_lines else "    - (sem action_items)"
                trans_text = (rm.get('transcription') or '')
                block = textwrap.dedent(
                    f"- id: {rm.get('id')} | reuniao: \"{rm.get('reuniao','')}\" | data: {rm.get('created_at','')}\n"
                    "  action_items:\n"
                    f"{ai_text}\n"
                    "  transcrição:\n"
                    f"    {trans_text}"
                )
                related_block_lines.append(block)
        related_block = "\n".join(related_block_lines) if related_block_lines else "(nenhuma reunião correlata encontrada)"

        user = textwrap.dedent(
            f"""
            REUNIÃO ATUAL:
            reuniao: {meeting_title}
            Resumo: {meeting_summary or 'Não disponível'}

            NOVOS ACTION ITEMS:
            {action_items_text}

            TAREFAS EXISTENTES ATIVAS (status 'pending', 'in_progress'):
            {existing_tasks_text}

            REUNIÕES CORRELATAS (até 3, últimos 90 dias, mesmo project_id, por similaridade de `reuniao`; stopwords/datas ignoradas):
            {related_block}

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
            {json.dumps(example, ensure_ascii=False, indent=2)}

            Se não houver merges relevantes, retorne:
            {json.dumps(empty_merges, ensure_ascii=False)}

            Seja criterioso - apenas sugira merges quando houver clara relação entre as tarefas.
            """
        ).strip()
        return system, user

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
