from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date

class ActionItem(BaseModel):
    id: str
    description: str
    responsible: Optional[str] = None
    priority: str = "medium"
    deadline: Optional[date] = None
    meeting_summary: Optional[str] = None
    meeting_title: Optional[str] = None
    meeting_date: Optional[datetime] = None

class ExistingTask(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    responsible: Optional[str] = None
    deadline: Optional[date] = None
    project_id: str
    created_at: datetime
    updated_at: datetime

class AnalysisRequest(BaseModel):
    action_items: List[ActionItem]
    project_id: str
    meeting_title: str
    meeting_summary: Optional[str] = None

class MergeProposal(BaseModel):
    parent_task_id: str
    child_action_items: List[str]
    similarity_score: float
    proposed_title: str
    proposed_status: str
    proposed_description: Optional[str] = None
    reasoning: str

class AnalysisResponse(BaseModel):
    analyzed: bool
    merge_proposals: List[MergeProposal]
    message: str
    lia_reviewed: bool = True
