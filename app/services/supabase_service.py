import os
from supabase import create_client, Client
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from ..models.analysis_models import ExistingTask, MergeProposal

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
            
            return self._filter_similar_meetings(tasks, meeting_title)
            
        except Exception as e:
            raise Exception(f"Erro ao buscar tarefas: {str(e)}")
    
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
