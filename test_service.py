#!/usr/bin/env python3
"""
Script de teste para o serviço de análise de tarefas IA
"""
import asyncio
import json
from app.services.anthropic_service import AnthropicService
from app.models.analysis_models import ActionItem, ExistingTask
from datetime import datetime

async def test_anthropic_connection():
    """Testa a conexão com Anthropic"""
    print("🔍 Testando conexão com Anthropic...")
    
    service = AnthropicService()
    try:
        result = await service.test_connection()
        print(f"✅ Conexão OK: {result}")
        return True
    except Exception as e:
        print(f"❌ Erro na conexão: {e}")
        return False

async def test_analysis_logic():
    """Testa a lógica de análise de similaridade"""
    print("\n🧠 Testando lógica de análise...")
    
    action_items = [
        ActionItem(
            id="ai1",
            description="Revisar documentação do projeto X",
            priority="medium"
        ),
        ActionItem(
            id="ai2", 
            description="Atualizar status do desenvolvimento",
            priority="high"
        )
    ]
    
    existing_tasks = [
        ExistingTask(
            id="task1",
            title="Documentação projeto X - fase inicial",
            description="Criar documentação básica",
            status="in_progress",
            priority="medium",
            project_id="proj1",
            created_at=datetime.now(),
            updated_at=datetime.now()
        ),
        ExistingTask(
            id="task2",
            title="Status report semanal",
            description="Relatório de progresso",
            status="pending",
            priority="low",
            project_id="proj1",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
    ]
    
    service = AnthropicService()
    try:
        proposals = await service.analyze_task_similarity(
            action_items,
            existing_tasks,
            "Reunião de Status Projeto X",
            "Discussão sobre progresso e documentação"
        )
        
        print(f"✅ Análise concluída: {len(proposals)} propostas encontradas")
        for i, proposal in enumerate(proposals):
            print(f"  Proposta {i+1}: {proposal.proposed_title} (score: {proposal.similarity_score})")
        
        return True
    except Exception as e:
        print(f"❌ Erro na análise: {e}")
        return False

async def main():
    print("🚀 Iniciando testes do serviço de análise IA\n")
    
    anthropic_ok = await test_anthropic_connection()
    
    if anthropic_ok:
        analysis_ok = await test_analysis_logic()
        
        if analysis_ok:
            print("\n✅ Todos os testes passaram!")
        else:
            print("\n❌ Falha nos testes de análise")
    else:
        print("\n❌ Falha na conexão com Anthropic - verifique ANTHROPIC_API_KEY")
    
    print("\n📝 Para testar completamente:")
    print("1. Configure ANTHROPIC_API_KEY no arquivo .env")
    print("2. Execute: poetry run python test_service.py")
    print("3. Execute: poetry run fastapi dev app/main.py")

if __name__ == "__main__":
    asyncio.run(main())
