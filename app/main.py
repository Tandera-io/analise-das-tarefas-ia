from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv
import logging
import time

from .services.anthropic_service import AnthropicService
from .services.supabase_service import SupabaseService
from .models.analysis_models import AnalysisRequest, AnalysisResponse, MergeProposal
from .middleware.auth import verify_api_key

load_dotenv()

# Logger básico
logger = logging.getLogger("analise_das_tarefas_ia")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service.starting")
    yield
    logger.info("service.stopped")

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

# Adicionar middleware de tenant (DEPOIS do CORS para que OPTIONS seja processado primeiro)
from .middleware.tenant import TenantMiddleware
app.add_middleware(TenantMiddleware)

try:
    anthropic_service = AnthropicService()
    logger.info(
        "anthropic.initialized model=%s", getattr(anthropic_service, "model", "unknown")
    )
except Exception as e:
    print(f"Warning: Anthropic service initialization failed: {e}")
    anthropic_service = None

try:
    supabase_service = SupabaseService()
    logger.info("supabase.initialized")
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
        start = time.perf_counter()
        logger.info(
            "analyze.start project_id=%s action_items=%d meeting_title=%s",
            request.project_id,
            len(request.action_items),
            request.meeting_title,
        )
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
            logger.info("analyze.no_tasks project_id=%s", request.project_id)
            return AnalysisResponse(
                analyzed=True,
                merge_proposals=[],
                message="Nenhuma tarefa similar encontrada",
                no_merge_notes=[]
            )
        
        analysis = await anthropic_service.analyze_task_similarity(
            request.action_items,
            existing_tasks,
            request.meeting_title,
            request.meeting_summary
        )
        
        # Gerar notas de não-merge a partir do diff entre AIs e merges
        merged_ai_ids = set()
        for mp in analysis.get("merge_proposals", []):
            for ai in mp.child_action_items:
                merged_ai_ids.add(ai)
        # Unir notas vindas da IA + fallback para itens não cobertos
        no_merge_notes = analysis.get("no_merge_notes", [])
        existing_noted = {n["action_item_id"] for n in no_merge_notes}
        for ai in request.action_items:
            if ai.id not in merged_ai_ids:
                if ai.id not in existing_noted:
                    reason = "Sem evidências claras de continuidade/duplicidade nas últimas reuniões correlatas."
                    no_merge_notes.append({"action_item_id": ai.id, "lia_reasoning": reason})
                # persistir insight
                try:
                    await supabase_service.upsert_lia_insight(ai.id, next((n["lia_reasoning"] for n in no_merge_notes if n["action_item_id"] == ai.id), reason))
                except Exception:
                    logger.warning("insight.persist_failed action_item_id=%s", ai.id)

        resp = AnalysisResponse(
            analyzed=True,
            merge_proposals=analysis.get("merge_proposals", []),
            message=f"Encontradas {len(analysis.get('merge_proposals', []))} propostas de merge",
            no_merge_notes=no_merge_notes
        )
        logger.info(
            "analyze.done project_id=%s proposals=%d duration_ms=%d",
            request.project_id,
            len(analysis.get("merge_proposals", [])),
            int((time.perf_counter() - start) * 1000),
        )
        return resp
        
    except Exception as e:
        logger.exception("analyze.error project_id=%s error=%s", request.project_id, str(e))
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
        start = time.perf_counter()
        logger.info(
            "merge.start parent_task_id=%s children=%d",
            proposal.parent_task_id,
            len(proposal.child_action_items),
        )
        result = await supabase_service.execute_task_merge(proposal)
        resp = {
            "success": True,
            "merged_task_id": result.get("merged_task_id"),
            "message": "Merge executado com sucesso"
        }
        logger.info(
            "merge.done parent_task_id=%s duration_ms=%d",
            proposal.parent_task_id,
            int((time.perf_counter() - start) * 1000),
        )
        return resp
    except Exception as e:
        logger.exception("merge.error parent_task_id=%s error=%s", proposal.parent_task_id, str(e))
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
        logger.info("proposals.fetch project_id=%s count=%d", project_id, len(proposals or []))
        return {
            "success": True,
            "proposals": proposals
        }
    except Exception as e:
        logger.exception("proposals.error project_id=%s error=%s", request.get("project_id"), str(e))
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
        logger.info("anthropic.test_ok result=%s", test_result)
        return {"status": "success", "result": test_result}
    except Exception as e:
        logger.exception("anthropic.test_error error=%s", str(e))
        return {"status": "error", "error": str(e)}

@app.get("/api/insights/{action_item_id}")
async def get_insight(action_item_id: str, _: dict = Depends(verify_api_key)):
    try:
        if not supabase_service:
            raise HTTPException(status_code=503, detail="Supabase service não disponível")
        data = await supabase_service.get_lia_insight(action_item_id)
        if not data:
            return {"found": False}
        return {"found": True, "lia_reasoning": data.get("reason"), "checked_at": data.get("checked_at")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar insight: {str(e)}")
