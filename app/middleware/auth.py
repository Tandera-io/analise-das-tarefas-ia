import os
from fastapi import HTTPException, Header
from typing import Optional

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> dict:
    expected_key = os.getenv("API_KEY", "dev-key-123")
    
    if not x_api_key or x_api_key != expected_key:
        raise HTTPException(
            status_code=401,
            detail="API key inválida"
        )
    
    return {"authenticated": True}
