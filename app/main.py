from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

from .services.anthropic_service import AnthropicService
from .services.supabase_service import SupabaseService
from .models.analysis_models import AnalysisRequest, AnalysisResponse, MergeProposal
from .middleware.auth import verify_api_key

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(
    title="Análise das Tarefas IA",
    description="AI-powered task analysis service for Tandera",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    anthropic_service = AnthropicService()
except Exception as e:
    print(f"Warning: Anthropic service initialization failed: {e}")
    anthropic_service = None

try:
    supabase_service = SupabaseService()
except Exception as e:
    print(f"Warning: Supabase service initialization failed: {e}")
    supabase_service = None

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "analise-das-tarefas-ia",
        "version": "0.1.0"
    }

@app.post("/api/analyze-action-items", response_model=AnalysisResponse)
async def analyze_action_items(
    request: AnalysisRequest,
    _: dict = Depends(verify_api_key)
):
    try:
        if not supabase_service:
            raise HTTPException(
                status_code=503,
                detail="Supabase service não disponível"
            )
        
        existing_tasks = await supabase_service.get_active_tasks_for_project(
            request.project_id,
            request.meeting_title
        )
        
        if not existing_tasks:
            return AnalysisResponse(
                analyzed=True,
                merge_proposals=[],
                message="Nenhuma tarefa similar encontrada"
            )
        
        merge_proposals = await anthropic_service.analyze_task_similarity(
            request.action_items,
            existing_tasks,
            request.meeting_title,
            request.meeting_summary
        )
        
        return AnalysisResponse(
            analyzed=True,
            merge_proposals=merge_proposals,
            message=f"Encontradas {len(merge_proposals)} propostas de merge"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro na análise: {str(e)}"
        )

@app.post("/api/propose-merge")
async def propose_merge(
    proposal: MergeProposal,
    _: dict = Depends(verify_api_key)
):
    try:
        result = await supabase_service.execute_task_merge(proposal)
        return {
            "success": True,
            "merged_task_id": result.get("merged_task_id"),
            "message": "Merge executado com sucesso"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro no merge: {str(e)}"
        )

@app.post("/api/get-merge-proposals")
async def get_merge_proposals(
    request: dict,
    _: dict = Depends(verify_api_key)
):
    try:
        project_id = request.get("project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id é obrigatório")
        
        proposals = await supabase_service.get_pending_merge_proposals(project_id)
        return {
            "success": True,
            "proposals": proposals
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao buscar propostas: {str(e)}"
        )

@app.get("/api/test-anthropic")
async def test_anthropic():
    try:
        if not anthropic_service:
            return {"status": "error", "error": "Anthropic service não disponível"}
        
        test_result = await anthropic_service.test_connection()
        return {"status": "success", "result": test_result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
