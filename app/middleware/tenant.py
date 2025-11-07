"""
Middleware para detectar e validar tenant em cada requisição.
VERSÃO ATUALIZADA COM SUPORTE A serviceRole via endpoint /backend-credentials
"""
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import logging
import httpx
import os
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Cache em memória (em produção, usar Redis)
_tenant_cache = {}


class TenantContext:
    """Contexto do tenant para a requisição atual."""
    
    def __init__(self):
        self.tenant_slug: Optional[str] = None
        self.tenant_data: Optional[dict] = None
    
    def set_tenant(self, slug: str, data: dict):
        self.tenant_slug = slug
        self.tenant_data = data
    
    def get_supabase_url(self) -> str:
        if not self.tenant_data:
            raise ValueError("Tenant não configurado")
        return self.tenant_data.get("supabaseUrl", "")
    
    def get_anon_key(self) -> str:
        if not self.tenant_data:
            raise ValueError("Tenant não configurado")
        return self.tenant_data.get("anonKey", "")
    
    def get_service_key(self) -> Optional[str]:
        """Retorna serviceRole se disponível (apenas com backend_credentials=True)"""
        if not self.tenant_data:
            raise ValueError("Tenant não configurado")
        return self.tenant_data.get("serviceRole")


# Contexto isolado por requisição usando contextvars (thread-safe e async-safe)
_tenant_context_var: ContextVar[TenantContext] = ContextVar('tenant_context', default=None)


def get_tenant_context() -> TenantContext:
    """Retorna o contexto do tenant atual da requisição."""
    ctx = _tenant_context_var.get()
    if ctx is None:
        # Criar contexto padrão se não existir
        ctx = TenantContext()
        _tenant_context_var.set(ctx)
    return ctx


async def get_tenant_from_registry(slug: str, include_backend_credentials: bool = False) -> Optional[dict]:
    """
    Busca dados do tenant no Registry API.
    
    Args:
        slug: Slug do tenant (ex: 'acme-corp')
        include_backend_credentials: Se True, busca credenciais completas incluindo serviceRole (requer autenticação)
    
    Returns:
        Dados do tenant incluindo credenciais Supabase
    """
    # Cache separado para credenciais de backend
    cache_key = f"{slug}:backend" if include_backend_credentials else slug
    
    # Verificar cache
    if cache_key in _tenant_cache:
        cached_data = _tenant_cache[cache_key]
        cached_url = cached_data.get("supabaseUrl", "N/A") if cached_data else "N/A"
        has_service_role = bool(cached_data.get("serviceRole")) if cached_data else False
        logger.info(f"[Registry] CACHE HIT para '{slug}' | Supabase: {cached_url[:50]}... | cache_key={cache_key} | has_serviceRole={has_service_role}")
        
        # Se solicitou backend_creds mas cache não tem serviceRole, buscar novamente
        if include_backend_credentials and not has_service_role:
            logger.warning(f"[Registry] Cache para '{slug}' não tem serviceRole, forçando nova busca no Registry")
            # Limpar cache e buscar novamente
            del _tenant_cache[cache_key]
        else:
            return cached_data
    
    # Configurações do Registry
    registry_url = os.getenv("REGISTRY_API_URL", "http://localhost:3000")
    
    # Validar que a URL tem protocolo
    if registry_url and not registry_url.startswith(("http://", "https://")):
        logger.error(f"REGISTRY_API_URL inválida (sem protocolo): {registry_url}")
        return None
    
    # Buscar no Registry
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Escolher endpoint baseado em credenciais necessárias
            if include_backend_credentials:
                # Endpoint autenticado para backends (inclui serviceRole)
                url = f"{registry_url}/api/tenants/by-slug/{slug}/backend-credentials"
                
                # Token de service-to-service authentication
                service_token = os.getenv("REGISTRY_SERVICE_TOKEN")
                if not service_token:
                    logger.error("REGISTRY_SERVICE_TOKEN não configurado - não é possível obter credenciais de backend")
                    return None
                
                headers = {"X-Registry-Service-Token": service_token}
                logger.debug(f"Buscando credenciais de backend para tenant '{slug}'")
            else:
                # Endpoint público (sem serviceRole)
                url = f"{registry_url}/api/tenants/by-slug/{slug}"
                headers = {}
            
            response = await client.get(url, headers=headers)
            
            if response.status_code == 404:
                logger.error(f"Tenant '{slug}' não encontrado no Registry")
                return None
            
            if response.status_code == 401:
                logger.error(f"Não autorizado a buscar credenciais de backend para '{slug}' - verifique REGISTRY_SERVICE_TOKEN")
                return None
            
            if response.status_code >= 400:
                logger.error(f"Erro ao buscar tenant: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            tenant_data = data.get("tenant", {})
            
            # Log detalhado do que foi retornado
            supabase_url = tenant_data.get("supabaseUrl", "N/A") if tenant_data else "N/A"
            has_anon_key = bool(tenant_data.get("anonKey")) if tenant_data else False
            has_service_role = bool(tenant_data.get("serviceRole")) if tenant_data else False
            logger.info(f"[Registry] Tenant '{slug}' carregado | Supabase: {supabase_url[:50]}... | backend_creds={include_backend_credentials} | has_anonKey={has_anon_key} | has_serviceRole={has_service_role}")
            
            # IMPORTANTE: Se solicitou backend_creds mas não veio serviceRole, logar erro
            if include_backend_credentials and not has_service_role:
                logger.error(f"[Registry] ATENÇÃO: Solicitou backend_creds mas Registry não retornou serviceRole para '{slug}'. Endpoint: {url} | Status: {response.status_code}")
            
            # Armazenar no cache
            _tenant_cache[cache_key] = tenant_data
            logger.debug(f"[Registry] Cache atualizado para '{slug}' (cache_key={cache_key})")
            
            return tenant_data
            
    except Exception as e:
        logger.error(f"Erro ao comunicar com Registry: {e}")
        return None


def clear_tenant_cache(slug: Optional[str] = None):
    """Limpa o cache de tenants."""
    global _tenant_cache
    if slug:
        _tenant_cache.pop(slug, None)
        _tenant_cache.pop(f"{slug}:backend", None)
        logger.info(f"Cache do tenant '{slug}' limpo")
    else:
        _tenant_cache.clear()
        logger.info("Cache de todos os tenants limpo")


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware que detecta o tenant via:
    1. Header X-Tenant-Slug
    2. Subdomain (ex: acme.localhost)
    3. Query param ?tenant=slug
    """
    
    async def dispatch(self, request: Request, call_next):
        # Ignorar requisições OPTIONS (CORS preflight) - elas não têm headers customizados
        if request.method == "OPTIONS":
            logger.debug(f"[TenantMiddleware] Ignorando OPTIONS para {request.url.path}")
            response = await call_next(request)
            return response
        
        # CRÍTICO: Criar um novo contexto para esta requisição
        # Isso garante isolamento completo entre requisições concorrentes
        new_context = TenantContext()
        token = _tenant_context_var.set(new_context)
        
        try:
            tenant_slug = None
            
            # 1. Tentar via header (prioritário)
            tenant_slug = request.headers.get("X-Tenant-Slug")
            
            # 2. Tentar via subdomain do FRONTEND (Origin ou Referer)
            if not tenant_slug:
                # Tentar extrair do Origin (CORS requests)
                origin = request.headers.get("origin", "")
                if origin:
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(origin)
                        host = parsed.netloc or parsed.hostname or ""
                        if "." in host and not host.startswith("localhost"):
                            tenant_slug = host.split(".")[0]
                            logger.debug(f"Tenant detectado via Origin: {tenant_slug}")
                    except Exception as e:
                        logger.debug(f"Erro ao extrair tenant do Origin: {e}")
                
                # Fallback: tentar Referer
                if not tenant_slug:
                    referer = request.headers.get("referer", "")
                    if referer:
                        try:
                            from urllib.parse import urlparse
                            parsed = urlparse(referer)
                            host = parsed.netloc or parsed.hostname or ""
                            if "." in host and not host.startswith("localhost"):
                                tenant_slug = host.split(".")[0]
                                logger.debug(f"Tenant detectado via Referer: {tenant_slug}")
                        except Exception as e:
                            logger.debug(f"Erro ao extrair tenant do Referer: {e}")
            
            # 3. Tentar via query param
            if not tenant_slug:
                tenant_slug = request.query_params.get("tenant")
            
            # Se não encontrou tenant, usar tenant padrão (para compatibilidade)
            if not tenant_slug:
                tenant_slug = os.getenv("DEFAULT_TENANT_SLUG", "dev")
                logger.debug(f"Nenhum tenant especificado, usando '{tenant_slug}'")
            
            # Buscar dados do tenant no Registry (sem serviceRole no middleware)
            # O serviceRole será obtido sob demanda pelo supabase_service quando necessário
            try:
                tenant_data = await get_tenant_from_registry(tenant_slug, include_backend_credentials=False)
                
                if not tenant_data:
                    # Se não encontrou no Registry, deixar passar para usar .env (fallback)
                    logger.warning(f"[TenantMiddleware] Tenant '{tenant_slug}' não encontrado, usando credenciais padrão")
                    new_context.set_tenant(tenant_slug, {})
                else:
                    # Configurar contexto do tenant
                    new_context.set_tenant(tenant_slug, tenant_data)
                    # Log detalhado para debug
                    supabase_url = tenant_data.get("supabaseUrl", "N/A")
                    logger.info(f"[TenantMiddleware] Tenant configurado: {tenant_slug} | Supabase: {supabase_url[:50]}...")
                
            except Exception as e:
                logger.error(f"Erro ao buscar tenant '{tenant_slug}': {e}")
                # Continuar com credenciais padrão
                new_context.set_tenant(tenant_slug, {})
            
            # Processar requisição
            response = await call_next(request)
            return response
        finally:
            # CRÍTICO: Resetar o contexto após processar a requisição
            # Isso garante que o contexto não vaze para outras requisições
            _tenant_context_var.reset(token)


# Manter função legada para compatibilidade
async def tenant_middleware(request: Request, call_next):
    """Função wrapper para compatibilidade com código legado."""
    middleware = TenantMiddleware(app=None)
    return await middleware.dispatch(request, call_next)
