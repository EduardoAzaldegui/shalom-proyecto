from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any
import requests
import os
from datetime import datetime
from dotenv import load_dotenv
import database
from mangum import Mangum

# Load env from .env.local (one level up from backend/) if exists
env_path = os.path.join(os.path.dirname(__file__), '..', '.env.local')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)

SHALOM_API_URL = os.environ.get("SHALOM_API_URL", "https://ecomapp.shalom-api.lat").rstrip("/")

# Master key: leido de variable de entorno. NUNCA se expone al cliente.
# El proxy lo inyecta transparentemente para los endpoints en MASTER_KEY_PATHS.
SHALOM_API_KEY_MASTER = os.environ.get("SHALOM_API_KEY_MASTER", "")
if not SHALOM_API_KEY_MASTER:
    import warnings
    warnings.warn("⚠️  SHALOM_API_KEY_MASTER no está definido en .env.local. Los endpoints restringidos pueden fallar.")

# Paths que requieren la Master Key del lado del servidor.
# El frontend los llama igual que cualquier otro endpoint (via /proxy).
# El proxy detecta la ruta y cambia el header x-api-key automáticamente.
MASTER_KEY_PATHS = {
    "/quote",
    "/track",
    "/track-massive",
    "/ticket-image",
    "/ticket-pdf",
    "/label",
    "/list",
    "/list-minimal",
    "/status",
    "/instances",
}

app = FastAPI(title="Shalom API Management Portal", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
#  Auth Models
# ─────────────────────────────────────────────
class AdminLogin(BaseModel):
    username: str
    password: str

class ClientCreate(BaseModel):
    name: str
    email: str
    shalom_username: str
    shalom_password: str

class MagicLogin(BaseModel):
    token: str


# ─────────────────────────────────────────────
#  Admin Auth Dependency
# ─────────────────────────────────────────────
def verify_admin_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    if token != "super-secret-admin-token-123":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# ─────────────────────────────────────────────
#  Admin Endpoints
# ─────────────────────────────────────────────
@app.post("/admin/login")
def admin_login(payload: AdminLogin):
    if database.verify_admin(payload.username, payload.password):
        return {"success": True, "token": "super-secret-admin-token-123"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/admin/clients")
def create_client(client: ClientCreate, is_admin: bool = Depends(verify_admin_token)):
    headers = {
        "x-api-key": SHALOM_API_KEY_MASTER,
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(f"{SHALOM_API_URL}/instances", headers=headers, json={})
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "created":
            raise HTTPException(status_code=400, detail="Failed to create instance in Shalom")
        instance_id = data.get("instanceId")
        api_key = data.get("apiKey")

        # Perform initial login
        login_payload = {
            "instanceId": instance_id,
            "username": client.shalom_username,
            "password": client.shalom_password
        }
        login_resp = requests.post(f"{SHALOM_API_URL}/login", headers=headers, json=login_payload, timeout=15)
        if login_resp.status_code not in (200, 201):
            # Rollback instance
            requests.delete(f"{SHALOM_API_URL}/instances", headers=headers, json={"instanceId": instance_id})
            raise HTTPException(status_code=400, detail="Credenciales de Shalom inválidas.")

        client_id, magic_token = database.create_client(client.name, client.email, instance_id, api_key, client.shalom_username, client.shalom_password)
        return {
            "message": "Client created successfully",
            "client_id": client_id,
            "magic_token": magic_token
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Shalom API error: {str(e)}")

@app.get("/admin/clients")
def list_clients(is_admin: bool = Depends(verify_admin_token)):
    return database.get_clients()

@app.post("/admin/clients/{client_id}/regenerate-token")
def regenerate_token(client_id: str, is_admin: bool = Depends(verify_admin_token)):
    new_token = database.regenerate_magic_token(client_id)
    return {
        "message": "Token regenerated",
        "magic_token": new_token
    }
class StatusUpdate(BaseModel):
    status: str

@app.put("/admin/clients/{client_id}/status")
def update_status(client_id: str, payload: StatusUpdate, is_admin: bool = Depends(verify_admin_token)):
    client = database.get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    if payload.status == "inactive" and client["status"] == "active":
        try:
            requests.delete(f"{SHALOM_API_URL}/instances", 
                          headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"},
                          json={"instanceId": client.get("instance_id")}, timeout=15)
        except Exception as e:
            print(f"Error deleting instance: {e}")
        database.update_client_status(client_id, "inactive")
        return {"message": "Client disabled and instance removed"}
        
    elif payload.status == "active" and client["status"] == "inactive":
        try:
            resp = requests.post(f"{SHALOM_API_URL}/instances", 
                               headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"},
                               json={"name": client["name"], "email": client["email"]}, timeout=15)
            if resp.status_code == 200 or resp.status_code == 201:
                data = resp.json()
                new_instance = data.get("instanceId")
                new_api = data.get("apiKey")
                if new_instance and new_api:
                    # Log in again to bind the new instance
                    login_payload = {
                        "instanceId": new_instance,
                        "username": client.get("shalom_username", ""),
                        "password": client.get("shalom_password", "")
                    }
                    requests.post(f"{SHALOM_API_URL}/login", headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"}, json=login_payload, timeout=15)
                    database.update_client_credentials(client_id, new_instance, new_api)
        except Exception as e:
            print(f"Error creating instance: {e}")
        database.update_client_status(client_id, "active")
        return {"message": "Client enabled and new instance created"}

    return {"message": "No changes made"}

@app.delete("/admin/clients/{client_id}")
def delete_client(client_id: str, is_admin: bool = Depends(verify_admin_token)):
    client = database.get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    if client.get("status") == "active" and client.get("instance_id"):
        try:
            requests.delete(f"{SHALOM_API_URL}/instances", 
                          headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"},
                          json={"instanceId": client.get("instance_id")}, timeout=15)
        except:
            pass
            
    database.delete_client(client_id)
    return {"message": "Client deleted permanently"}


# ─────────────────────────────────────────────
#  Client Auth
# ─────────────────────────────────────────────
@app.post("/auth/magic")
def magic_login(payload: MagicLogin):
    client = database.get_client_by_token(payload.token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid token")
    expires_at = datetime.fromisoformat(client["token_expires_at"])
    if datetime.now() > expires_at:
        raise HTTPException(status_code=401, detail="Token has expired")
    # Se devuelve apiKey al frontend (Opción B elegida por el usuario)
    return {
        "success": True,
        "client": {
            "name": client["name"],
            "email": client["email"],
            "instanceId": client["instance_id"],
            "apiKey": client["api_key"]
        }
    }

@app.post("/auth/refresh-session")
def refresh_shalom_session(payload: MagicLogin):
    client = database.get_client_by_token(payload.token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    headers = {
        "x-api-key": SHALOM_API_KEY_MASTER,
        "Content-Type": "application/json"
    }
    login_payload = {
        "instanceId": client.get("instance_id"),
        "username": client.get("shalom_username", ""),
        "password": client.get("shalom_password", "")
    }
    try:
        resp = requests.post(f"{SHALOM_API_URL}/login", headers=headers, json=login_payload, timeout=15)
        if resp.status_code in (200, 201):
            return {"success": True, "message": "Sesión de Shalom refrescada correctamente."}
        raise HTTPException(status_code=400, detail="No se pudo refrescar la sesión en Shalom.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
#  Terminals Catalog
#  Construido desde datos reales de /pending-shipments.
#  Shalom no expone un endpoint público de terminales.
# ─────────────────────────────────────────────
import time

terminals_cache = {
    "data": [],
    "expires_at": 0
}

@app.get("/terminals")
def get_terminals(search: Optional[str] = None):
    """
    Catálogo de terminales Shalom con sus ter_id obtenidos en tiempo real
    desde el endpoint /list de la API de Shalom. Usa caché de 1 hora.
    """
    global terminals_cache
    
    # Check cache (1 hour = 3600 seconds)
    if time.time() > terminals_cache["expires_at"] or not terminals_cache["data"]:
        headers = {"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"}
        try:
            resp = requests.get(f"{SHALOM_API_URL}/list", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                formatted = []
                for t in data:
                    formatted.append({
                        "ter_id": t.get("ter_id"),
                        "name": t.get("lugar") or t.get("lugar_over") or t.get("nombre") or t.get("zona") or "Agencia Desconocida",
                        "ubigeo": f"{t.get('departamento') or ''} - {t.get('provincia') or ''} - {t.get('zona') or ''}",
                        "abbr": t.get("ter_abrebiatura") or ""
                    })
                terminals_cache["data"] = formatted
                terminals_cache["expires_at"] = time.time() + 3600
        except Exception:
            pass

    catalog = terminals_cache["data"]
    if search:
        q = search.lower()
        catalog = [t for t in catalog if q in (t.get("name") or "").lower() or q in (t.get("ubigeo") or "").lower()]
    return {"count": len(catalog), "terminals": catalog}


# ─────────────────────────────────────────────
#  Smart Proxy  (SEGURO CON API KEY)
# ─────────────────────────────────────────────
class ProxyRequest(BaseModel):
    method: str         # "post" | "get"
    path: str           # e.g. "/quote", "/register-individual"
    body: Optional[Any] = None   # payload de negocio (sin instanceId)

@app.post("/proxy")
def proxy_request(req: ProxyRequest, x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    req.path = req.path if req.path.startswith('/') else f"/{req.path}"

    # 1. Validar x-api-key contra la base de datos
    client = database.get_client_by_api_key(x_api_key)
    if not client:
        raise HTTPException(status_code=401, detail="API Key inválida. Solicita credenciales válidas en tu portal.")

    # 2. Resolver qué API key usar
    api_key = SHALOM_API_KEY_MASTER if req.path in MASTER_KEY_PATHS else client["api_key"]

    # 3. Construir headers internamente (el cliente nunca los ve)
    forward_headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }

    # 4. Inyectar instanceId en el body si el endpoint lo requiere
    body = dict(req.body) if req.body else {}
    INSTANCE_PATHS = {"/register-individual", "/register",
                      "/pending-shipments", "/get-user", "/update-password",
                      "/update-contact-1", "/update-contact-2", "/quote",
                      "/ticket-image", "/ticket-pdf", "/label"}
    
    # Bloquear acceso a /login y /logout desde el proxy
    if req.path in ["/login", "/logout"]:
        raise HTTPException(status_code=403, detail="El proxy gestiona la autenticación automáticamente. Usa /refresh-session si necesitas renovar.")

    if req.path in INSTANCE_PATHS:
        body["instanceId"] = client.get("instance_id")

    # 5. Llamar a Shalom
    url = f"{SHALOM_API_URL}{req.path}"
    try:
        if req.method.upper() == "GET":
            resp = requests.get(url, headers=forward_headers, timeout=15)
        elif req.method.upper() == "POST":
            resp = requests.post(url, headers=forward_headers, json=body, timeout=15)
        else:
            raise HTTPException(status_code=400, detail="Método no soportado.")

        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text, "status_code": resp.status_code}

    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Shalom API timeout. Intentá de nuevo.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def read_root():
    return {"message": "Shalom API Management Portal v2.0 — Proxy Seguro (Serverless)"}

# AWS Lambda Handler
handler = Mangum(app)
