import os
from supabase import create_client, Client
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import re
from ..models.analysis_models import ExistingTask, MergeProposal
import logging
import time

class SupabaseService:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        self.supabase: Client = create_client(url, key)
    
    async def get_active_tasks_for_project(
        self,
        project_id: str,
        meeting_title: str
    ) -> List[ExistingTask]:
        try:
            start = time.perf_counter()
            response = self.supabase.table("kanban_tasks").select("*").eq(
                "project_id", project_id
            ).in_(
                "status", ["pending", "in_progress"]
            ).order("created_at", desc=True).limit(50).execute()
            
            tasks = []
            for task_data in response.data:
                task = ExistingTask(
                    id=task_data["id"],
                    title=task_data["title"],
                    description=task_data.get("description"),
                    status=task_data["status"],
                    priority=task_data.get("priority", "medium"),
                    responsible=task_data.get("responsible"),
                    deadline=task_data.get("deadline"),
                    project_id=task_data["project_id"],
                    created_at=datetime.fromisoformat(task_data["created_at"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(task_data["updated_at"].replace("Z", "+00:00"))
                )
                tasks.append(task)
            
            filtered = self._filter_similar_meetings(tasks, meeting_title)
            logging.getLogger("analise_das_tarefas_ia").info(
                "supabase.tasks project_id=%s fetched=%d filtered=%d duration_ms=%d",
                project_id,
                len(tasks),
                len(filtered),
                int((time.perf_counter() - start) * 1000),
            )
            return filtered
            
        except Exception as e:
            raise Exception(f"Erro ao buscar tarefas: {str(e)}")

    def _tokenize_reuniao(self, reuniao: str) -> List[str]:
        if not reuniao:
            return []
        text = reuniao.lower()
        # Remover datas comuns (yyyy-mm-dd, dd/mm/yyyy, dd-mm-yyyy, yyyymmdd)
        text = re.sub(r"\b\d{4}[-/]?\d{2}[-/]?\d{2}\b", " ", text)
        text = re.sub(r"\b\d{2}[-/]?\d{2}[-/]?\d{4}\b", " ", text)
        # Separar por não alfanuméricos
        tokens = re.split(r"[^a-z0-9]+", text)
        stop = {
            "", "gravacao", "gravação", "reuniao", "reunião", "meeting", "meet",
            "audio", "video", "recording", "mp4", "wav", "m4a"
        }
        return [t for t in tokens if t not in stop and len(t) >= 2]

    def _score_by_tokens(self, current_tokens: List[str], candidate_reuniao: str) -> int:
        cand_tokens = set(self._tokenize_reuniao(candidate_reuniao))
        return len(set(current_tokens).intersection(cand_tokens))

    async def get_related_meetings(self, project_id: str, current_reuniao: str) -> List[Dict[str, Any]]:
        try:
            # Buscar transcrições do mesmo projeto nos últimos 90 dias
            ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            response = self.supabase.table("transcriptions").select(
                "id,reuniao,created_at,transcription"
            ).eq("project_id", project_id).gte("created_at", ninety_days_ago).order(
                "created_at", desc=True
            ).limit(100).execute()

            current_tokens = set(self._tokenize_reuniao(current_reuniao))
            scored: List[Dict[str, Any]] = []
            for row in response.data or []:
                score = self._score_by_tokens(list(current_tokens), row.get("reuniao") or "")
                if score > 0:
                    scored.append({**row, "_score": score})

            # Ordenar por score desc e data desc, pegar top 3
            scored.sort(key=lambda r: (r.get("_score", 0), r.get("created_at", "")), reverse=True)
            top = scored[:3]

            # Anexar action_items de cada transcrição
            related: List[Dict[str, Any]] = []
            for row in top:
                ai_resp = self.supabase.table("action_items").select(
                    "id,description,responsible,priority,deadline,status"
                ).eq("transcription_id", row["id"]).order("created_at", desc=True).limit(50).execute()
                related.append({
                    "id": row["id"],
                    "reuniao": row.get("reuniao"),
                    "created_at": row.get("created_at"),
                    "transcription": row.get("transcription"),
                    "action_items": ai_resp.data or []
                })

            return related
        except Exception as e:
            raise Exception(f"Erro ao buscar reuniões correlatas: {str(e)}")
    
    def _filter_similar_meetings(
        self,
        tasks: List[ExistingTask],
        meeting_title: str
    ) -> List[ExistingTask]:
        meeting_keywords = set(meeting_title.lower().split())
        
        filtered_tasks = []
        for task in tasks:
            task_keywords = set(task.title.lower().split())
            
            if len(meeting_keywords.intersection(task_keywords)) >= 1:
                filtered_tasks.append(task)
            elif (datetime.now(timezone.utc) - task.created_at).days <= 30:
                filtered_tasks.append(task)
        
        return filtered_tasks[:20]
    
    async def execute_task_merge(self, proposal: MergeProposal) -> Dict[str, Any]:
        try:
            parent_task_response = self.supabase.table("kanban_tasks").select("*").eq(
                "id", proposal.parent_task_id
            ).execute()
            
            if not parent_task_response.data:
                raise Exception("Tarefa pai não encontrada")
            
            parent_task = parent_task_response.data[0]
            
            previous_title = parent_task.get("title")
            previous_status = parent_task.get("status")
            previous_description = parent_task.get("description")

            updated_description = proposal.proposed_description or parent_task.get("description", "")
            if parent_task.get("description"):
                updated_description = f"{parent_task['description']}\n\n--- Atualização da reunião ---\n{updated_description}"
            
            update_response = self.supabase.table("kanban_tasks").update({
                "title": proposal.proposed_title,
                "status": proposal.proposed_status,
                "description": updated_description,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", proposal.parent_task_id).execute()
            
            # Observação: não alteramos mais o status dos action_items aqui
            # para respeitar o check constraint do banco e evitar estados inválidos.
            
            # Registrar histórico do merge
            try:
                self.supabase.table("task_merge_history").insert({
                    "parent_task_id": proposal.parent_task_id,
                    "child_action_items": proposal.child_action_items,
                    "similarity_score": proposal.similarity_score,
                    "previous_title": previous_title,
                    "previous_status": previous_status,
                    "previous_description": previous_description,
                    "new_title": proposal.proposed_title,
                    "new_status": proposal.proposed_status,
                    "new_description": updated_description,
                    "reasoning": proposal.reasoning,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }).execute()
            except Exception as history_error:
                # Não bloquear o fluxo do merge se o histórico falhar
                print(f"Aviso: falha ao registrar histórico do merge: {history_error}")

            return {
                "merged_task_id": proposal.parent_task_id,
                "updated_task": update_response.data[0] if update_response.data else None
            }
            
        except Exception as e:
            raise Exception(f"Erro ao executar merge: {str(e)}")
    
    async def get_pending_merge_proposals(self, project_id: str) -> List[Dict[str, Any]]:
        try:
            return []
        except Exception as e:
            raise Exception(f"Erro ao buscar propostas: {str(e)}")
