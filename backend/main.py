from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Any
import requests
import os
import time
import json
import base64
from datetime import datetime
from dotenv import load_dotenv
import database
from mangum import Mangum

# Content-Types binarios que NO deben pasar por resp.text (UTF-8) porque corrompen
# el archivo. Para estos, el proxy devuelve {encoding: "base64", base64: "..."} y
# el CRM cliente abre el archivo correctamente.
BINARY_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "application/octet-stream",
}

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

# Paths que requieren validación de ownership (la guía debe pertenecer al remitente del cliente).
OWNERSHIP_CHECK_PATHS = {"/track", "/track-massive", "/ticket-image", "/ticket-pdf", "/label"}

# ─────────────────────────────────────────────
#  Cache de /get-user por instance_id (TTL 5 min)
#  Evita llamar a Shalom en cada request de tracking.
# ─────────────────────────────────────────────
_user_cache: dict[str, dict] = {}
_USER_CACHE_TTL = 300  # segundos

def get_shalom_user_document(instance_id: str, api_key_to_use: str) -> Optional[str]:
    """
    Llama a /get-user en Shalom y devuelve el DNI (document) del remitente.
    Usa cache en memoria por instance_id con TTL de 5 minutos.
    Devuelve None si no se puede obtener el documento.
    """
    now = time.time()
    cached = _user_cache.get(instance_id)
    if cached and now < cached["expires_at"]:
        return cached["document"]

    headers = {"x-api-key": api_key_to_use, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{SHALOM_API_URL}/get-user",
            headers=headers,
            json={"instanceId": instance_id},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            person = data.get("person") or {}
            company = data.get("company") or {}
            document = str(person.get("document") or company.get("number_document", "")).strip()
            if document:
                _user_cache[instance_id] = {
                    "document": document,
                    "expires_at": now + _USER_CACHE_TTL
                }
                return document
    except Exception as e:
        print(f"[ownership] Error en /get-user para instance {instance_id}: {e}")
    return None


def get_shalom_user_full(instance_id: str, api_key_to_use: str) -> Optional[dict]:
    """
    Llama a /get-user en Shalom y devuelve el objeto person completo.
    Usado al crear/activar clientes para persistir nombre del remitente.
    """
    headers = {"x-api-key": api_key_to_use, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{SHALOM_API_URL}/get-user",
            headers=headers,
            json={"instanceId": instance_id},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            person = data.get("person") or {}
            company = data.get("company") or {}
            return {
                "person_id": data.get("person_id") or company.get("id"),
                "full_name": person.get("full_name") or company.get("legal_name"),
                "document": str(person.get("document") or company.get("number_document", "")).strip(),
                "phone": person.get("phone") or company.get("phone"),
            }
    except Exception as e:
        print(f"[get-user] Error: {e}")
    return None


def extract_sender_document_from_track(response_data: dict) -> Optional[str]:
    """
    Extrae el DNI del remitente de la respuesta de /track.
    Estructura: response.search.data.remitente.documento
    """
    try:
        return str(response_data["search"]["data"]["remitente"]["documento"]).strip()
    except (KeyError, TypeError):
        return None


def extract_sender_documents_from_track_massive(response_data: dict) -> list[str]:
    """
    Extrae los DNIs del remitente de la respuesta de /track-massive.
    Estructura: response[].search.data.remitente.documento (array de resultados)
    """
    docs = []
    try:
        if isinstance(response_data, list):
            for item in response_data:
                doc = extract_sender_document_from_track(item)
                if doc:
                    docs.append(doc)
    except Exception:
        pass
    return docs


# ─────────────────────────────────────────────
#  Clasificación y tipado de errores del proxy
#
#  Todo error que sale del proxy lleva un envelope con un TAG de origen
#  (`error.source`) para que el cliente sepa de quién es la culpa:
#    - shalom_session  → la sesión Shalom de esa instancia venció / auto-login
#                        falló. Recuperable re-logueando. HTTP 401.
#    - shalom_down      → Shalom no responde (timeout / conexión / 502/503/504).
#                        HTTP 503/504. NO es culpa del cliente ni del proxy.
#    - shalom_upstream  → Shalom respondió un error de negocio (4xx/5xx). Se
#                        propaga el status real de Shalom.
#    - proxy_internal   → error nuestro (DB, bug, etc.). HTTP 500.
# ─────────────────────────────────────────────

# Marcadores que indican que la sesión Shalom expiró y el auto-login falló.
# Se buscan (lowercase) en el body de la respuesta de Shalom.
_SESSION_ERROR_MARKERS = (
    "auto-login failed",
    "max retries",
    "call /login",
    "llamar a /login",
    "session expired",
    "sesión expir",
    "sesion expir",
    "no autorizado",
    "unauthorized",
    "token expir",
)


def _payload_text(payload: Any) -> str:
    """Serializa el body de Shalom a texto lowercase para buscar marcadores."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.lower()
    try:
        return json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        return str(payload).lower()


def _is_session_error(status_code: int, payload: Any) -> bool:
    """
    True si la respuesta de Shalom indica sesión vencida / auto-login fallido.
    Detecta tanto por status 401 como por los marcadores de texto (Shalom a
    veces devuelve el error de auto-login con status 500 o incluso 200).
    """
    if status_code == 401:
        return True
    text = _payload_text(payload)
    return any(marker in text for marker in _SESSION_ERROR_MARKERS)


def _extract_message(payload: Any, default: str) -> str:
    """Extrae el mensaje legible del body de error de Shalom."""
    if isinstance(payload, dict):
        for key in ("message", "error", "detail", "msg"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                inner = val.get("message") or val.get("detail")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:500]
    return default


def _error_envelope(source: str, code: str, message: str, path: str,
                    http_status: int, upstream_status: Optional[int] = None,
                    recovery_attempted: Optional[bool] = None) -> JSONResponse:
    """Construye la respuesta de error tipada con el status HTTP elegido."""
    err = {
        "source": source,
        "code": code,
        "message": message,
        "path": path,
    }
    if upstream_status is not None:
        err["upstream_status"] = upstream_status
    if recovery_attempted is not None:
        err["recovery_attempted"] = recovery_attempted
    return JSONResponse(
        status_code=http_status,
        content={"success": False, "error": err},
    )


def _shalom_relogin(client: dict) -> bool:
    """
    Re-loguea la instancia del cliente en Shalom usando las credenciales
    guardadas en DynamoDB. Devuelve True si Shalom aceptó el login.
    Esto es lo que hace al proxy "auto-gestionar la auth": si la sesión venció,
    la renueva solo antes de reintentar el request original.
    """
    username = client.get("shalom_username")
    instance_id = client.get("instance_id")
    if not username or not instance_id:
        return False
    try:
        r = requests.post(
            f"{SHALOM_API_URL}/login",
            headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"},
            json={
                "instanceId": instance_id,
                "username": username,
                "password": client.get("shalom_password", ""),
            },
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[auth] Re-login falló para instance {instance_id}: {e}")
        return False


def _classify_shalom_error(resp, payload: Any, path: str,
                           recovery_attempted: bool) -> JSONResponse:
    """
    Convierte una respuesta fallida de Shalom en un envelope tipado con el
    status HTTP correcto. Asume que ya se determinó que es un error.
    """
    status = resp.status_code

    # 1. Sesión vencida / auto-login agotado (recuperable por el admin).
    if _is_session_error(status, payload):
        return _error_envelope(
            source="shalom_session",
            code="AUTOLOGIN_FAILED",
            message=(
                "La sesión de Shalom de esta cuenta expiró y el re-login automático "
                "no fue aceptado. Verificá que las credenciales Shalom del cliente "
                "sean válidas (panel admin) o usá /auth/refresh-session."
            ),
            path=path,
            http_status=401,
            upstream_status=status,
            recovery_attempted=recovery_attempted,
        )

    # 2. Shalom caído / no responde correctamente (gateway errors).
    if status in (502, 503, 504):
        return _error_envelope(
            source="shalom_down",
            code="UPSTREAM_UNAVAILABLE",
            message=_extract_message(payload, "Shalom no está respondiendo. Intentá de nuevo en unos minutos."),
            path=path,
            http_status=503,
            upstream_status=status,
        )

    # 3. Error de negocio de Shalom → se propaga su status real.
    return _error_envelope(
        source="shalom_upstream",
        code="SHALOM_ERROR",
        message=_extract_message(payload, "Shalom rechazó la solicitud."),
        path=path,
        http_status=status if status >= 400 else 502,
        upstream_status=status,
    )


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

        # Obtener datos del remitente (person) desde Shalom y almacenarlos
        person_info = get_shalom_user_full(instance_id, SHALOM_API_KEY_MASTER)
        person_name = person_info.get("full_name") if person_info else None
        person_document = person_info.get("document") if person_info else None

        client_id, magic_token = database.create_client(
            client.name, client.email,
            instance_id, api_key,
            client.shalom_username, client.shalom_password,
            person_name=person_name,
            person_document=person_document
        )
        return {
            "message": "Client created successfully",
            "client_id": client_id,
            "magic_token": magic_token,
            "person_name": person_name,
            "person_document": person_document,
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
            if resp.status_code in (200, 201):
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

                    # Refrescar datos del remitente (person) en la nueva instancia
                    person_info = get_shalom_user_full(new_instance, SHALOM_API_KEY_MASTER)
                    if person_info:
                        database.update_client_person(
                            client_id,
                            person_name=person_info.get("full_name"),
                            person_document=person_info.get("document")
                        )
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
#  Admin: Ver datos del remitente (persona Shalom) de un cliente
# ─────────────────────────────────────────────
@app.get("/admin/clients/{client_id}/user")
def get_client_shalom_user(client_id: str, is_admin: bool = Depends(verify_admin_token)):
    """
    Devuelve los datos de la persona (remitente) asociada al cliente en Shalom.
    Llama a /get-user en tiempo real y también devuelve los datos cacheados en DB.
    """
    client = database.get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.get("status") != "active":
        raise HTTPException(status_code=400, detail="El cliente debe estar activo para consultar su usuario en Shalom.")

    # Datos cacheados en DB (guardados al crear/activar)
    cached = {
        "person_name": client.get("person_name"),
        "person_document": client.get("person_document"),
    }

    # Datos frescos de Shalom en tiempo real
    live_info = get_shalom_user_full(client.get("instance_id"), SHALOM_API_KEY_MASTER)

    return {
        "client_id": client_id,
        "client_name": client["name"],
        "cached": cached,
        "live": live_info,
    }


# ─────────────────────────────────────────────
#  Admin: Health-check de la API de Shalom (tercero)
#  Permite al admin verificar desde el panel si Shalom está vivo,
#  midiendo latencia real contra un endpoint liviano con la master key.
# ─────────────────────────────────────────────
@app.get("/admin/health/shalom")
def shalom_health(is_admin: bool = Depends(verify_admin_token)):
    """
    Hace un ping real a la API de Shalom (GET /list con la master key) y
    reporta si está operativa, con latencia y status del upstream.
    No es un problema del proxy ni de los clientes: refleja SOLO a Shalom.
    """
    started = time.time()
    headers = {"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"}
    try:
        resp = requests.get(f"{SHALOM_API_URL}/list", headers=headers, timeout=8)
        latency_ms = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            return {
                "ok": True,
                "status": "up",
                "upstream_status": 200,
                "latency_ms": latency_ms,
                "url": SHALOM_API_URL,
                "message": "Shalom está operativa.",
            }
        # Respondió pero con error → degradado/caído según el código.
        is_down = resp.status_code in (502, 503, 504)
        return {
            "ok": False,
            "status": "down" if is_down else "degraded",
            "upstream_status": resp.status_code,
            "latency_ms": latency_ms,
            "url": SHALOM_API_URL,
            "message": f"Shalom respondió con HTTP {resp.status_code}.",
        }
    except requests.exceptions.Timeout:
        return {
            "ok": False, "status": "down", "upstream_status": None,
            "latency_ms": round((time.time() - started) * 1000),
            "url": SHALOM_API_URL,
            "message": "Shalom no respondió a tiempo (timeout). Puede estar caída o saturada.",
        }
    except requests.exceptions.RequestException as e:
        return {
            "ok": False, "status": "down", "upstream_status": None,
            "latency_ms": round((time.time() - started) * 1000),
            "url": SHALOM_API_URL,
            "message": f"No se pudo conectar con Shalom: {e}",
        }


# ─────────────────────────────────────────────
#  Client Auth
# ─────────────────────────────────────────────
@app.post("/auth/magic")
def magic_login(payload: MagicLogin):
    # El magic token no expira. Revocación: admin desactiva el cliente
    # (status: inactive) o regenera el token vía /admin/clients/:id/regenerate-token.
    client = database.get_client_by_token(payload.token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid token")
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
# ─────────────────────────────────────────────
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
#  Smart Proxy  (SEGURO CON API KEY + OWNERSHIP CHECK)
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

    # 4.5 ── OWNERSHIP PRE-CHECK PARA BINARIOS ────────────────────────────
    # Para /ticket-image, /ticket-pdf, /label, Shalom devuelve binario sin datos del remitente.
    # Pre-validar ownership consultando /track antes de hacer el request a Shalom.
    if req.path in ["/ticket-image", "/ticket-pdf", "/label"]:
        instance_id = client.get("instance_id")
        sender_doc = get_shalom_user_document(instance_id, SHALOM_API_KEY_MASTER)
        if sender_doc:
            order_number = body.get("orderNumber")
            order_code = body.get("orderCode")
            if order_number and order_code:
                try:
                    pre_resp = requests.post(
                        f"{SHALOM_API_URL}/track",
                        headers={"x-api-key": SHALOM_API_KEY_MASTER, "Content-Type": "application/json"},
                        json={"orderNumber": order_number, "orderCode": order_code},
                        timeout=10
                    )
                    if pre_resp.status_code == 200:
                        pre_data = pre_resp.json()
                        if pre_data.get("success") is False:
                            msg = pre_data.get("message") or pre_data.get("search", {}).get("message") or "La guía solicitada no fue encontrada en Shalom."
                            raise HTTPException(status_code=404, detail=msg)

                        remitente_doc = extract_sender_document_from_track(pre_data)
                        if remitente_doc and remitente_doc != sender_doc:
                            raise HTTPException(
                                status_code=403,
                                detail="Acceso denegado: esta guía no pertenece a tu cuenta Shalom. "
                                       "El remitente registrado no coincide con tu usuario."
                            )
                except HTTPException:
                    raise
                except Exception as pre_err:
                    print(f"[ownership] WARNING: pre-check /track falló para {order_number}: {pre_err}")

    # 5. Llamar a Shalom
    url = f"{SHALOM_API_URL}{req.path}"

    def _do_request():
        if req.method.upper() == "GET":
            return requests.get(url, headers=forward_headers, timeout=15)
        elif req.method.upper() == "POST":
            return requests.post(url, headers=forward_headers, json=body, timeout=15)
        raise HTTPException(status_code=400, detail="Método no soportado.")

    try:
        resp = _do_request()

        # Detectar binarios (PDF/PNG/JPG) y devolver base64 antes de intentar JSON.
        # resp.text fuerza UTF-8 y corrompe el binario. Solo si la respuesta es OK;
        # si falló, cae al manejo de error JSON de abajo.
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if resp.ok and content_type in BINARY_CONTENT_TYPES:
            return {
                "success": True,
                "content_type": content_type,
                "encoding": "base64",
                "base64": base64.b64encode(resp.content).decode("ascii"),
                "status_code": resp.status_code,
            }

        try:
            shalom_response = resp.json()
        except Exception:
            if resp.ok:
                return {"raw_text": resp.text, "status_code": resp.status_code}
            shalom_response = resp.text

        # 5.5 ── AUTO-RECUPERACIÓN DE SESIÓN ──────────────────────────────────
        # Si Shalom dice que la sesión venció / auto-login agotó reintentos, el
        # proxy se re-loguea solo con las credenciales guardadas y reintenta UNA
        # vez. Esto es lo que hace que la auth sea realmente "automática".
        recovery_attempted = False
        if _is_session_error(resp.status_code, shalom_response) and client.get("shalom_username"):
            recovery_attempted = True
            print(f"[auth] Sesión vencida en {req.path} (instance {client.get('instance_id')}). Re-logueando…")
            if _shalom_relogin(client):
                resp = _do_request()
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if resp.ok and content_type in BINARY_CONTENT_TYPES:
                    return {
                        "success": True,
                        "content_type": content_type,
                        "encoding": "base64",
                        "base64": base64.b64encode(resp.content).decode("ascii"),
                        "status_code": resp.status_code,
                    }
                try:
                    shalom_response = resp.json()
                except Exception:
                    if resp.ok:
                        return {"raw_text": resp.text, "status_code": resp.status_code}
                    shalom_response = resp.text

        # 5.6 ── ERRORES DE SHALOM (status real + envelope tipado) ────────────
        # Cualquier no-2xx o error de sesión persistente se devuelve clasificado.
        if not resp.ok or _is_session_error(resp.status_code, shalom_response):
            return _classify_shalom_error(resp, shalom_response, req.path, recovery_attempted)

        # 6. ── OWNERSHIP CHECK ──────────────────────────────────────────────
        # Para endpoints de tracking e imágenes, validar que la guía
        # pertenezca al remitente del cliente autenticado.
        if req.path in OWNERSHIP_CHECK_PATHS:
            instance_id = client.get("instance_id")
            sender_doc = get_shalom_user_document(instance_id, SHALOM_API_KEY_MASTER)

            # 6a. Si Shalom indica error → propagar como 404 con su propio mensaje.
            # Shalom usa dos estructuras distintas según el endpoint:
            #   - {"success": false, "message": "..."} → /track y similares
            #   - {"error": "..."} → /ticket-image, /ticket-pdf, /label
            shalom_error_msg: Optional[str] = None
            if isinstance(shalom_response, dict):
                if shalom_response.get("success") is False:
                    shalom_error_msg = (
                        shalom_response.get("message")
                        or shalom_response.get("search", {}).get("message")
                        or "La guía solicitada no fue encontrada en Shalom."
                    )
                elif "error" in shalom_response:
                    shalom_error_msg = str(shalom_response["error"])

            if shalom_error_msg:
                raise HTTPException(status_code=404, detail=shalom_error_msg)

            if sender_doc:
                if req.path == "/track-massive":
                    # Filtrar la lista: excluir guías que no existen (success:false)
                    # y guías que no pertenecen al usuario (remitente diferente)
                    if isinstance(shalom_response, list):
                        filtered = []
                        for item in shalom_response:
                            if isinstance(item, dict) and item.get("success") is False:
                                continue
                            item_doc = extract_sender_document_from_track(item)
                            if item_doc is None or item_doc == sender_doc:
                                filtered.append(item)
                        return filtered

                elif req.path == "/track":
                    # /track incluye el remitente directamente en su respuesta
                    remitente_doc = extract_sender_document_from_track(shalom_response)
                    if remitente_doc and remitente_doc != sender_doc:
                        raise HTTPException(
                            status_code=403,
                            detail="Acceso denegado: esta guía no pertenece a tu cuenta Shalom. "
                                   "El remitente registrado no coincide con tu usuario."
                        )
            else:
                print(f"[ownership] WARNING: No se pudo verificar ownership para instance {instance_id}. Permitiendo request.")

        return shalom_response

    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        return _error_envelope(
            source="shalom_down",
            code="UPSTREAM_TIMEOUT",
            message="Shalom no respondió a tiempo. Puede estar saturado o caído. Intentá de nuevo en unos minutos.",
            path=req.path,
            http_status=504,
        )
    except requests.exceptions.ConnectionError:
        return _error_envelope(
            source="shalom_down",
            code="UPSTREAM_UNREACHABLE",
            message="No se pudo conectar con Shalom. El servicio parece estar caído.",
            path=req.path,
            http_status=503,
        )
    except requests.exceptions.RequestException as e:
        return _error_envelope(
            source="shalom_upstream",
            code="UPSTREAM_REQUEST_FAILED",
            message=f"Fallo al comunicarse con Shalom: {e}",
            path=req.path,
            http_status=502,
        )
    except Exception as e:
        # Error nuestro (DB, bug, serialización…). Es el único caso 'proxy_internal'.
        print(f"[proxy] ERROR interno en {req.path}: {e}")
        return _error_envelope(
            source="proxy_internal",
            code="PROXY_INTERNAL_ERROR",
            message="Error interno del proxy. No es un problema de Shalom.",
            path=req.path,
            http_status=500,
        )


# ─────────────────────────────────────────────
#  llms.txt — Contexto estructurado para LLMs (estándar llmstxt.org)
#  El cliente le pasa este URL a su LLM (Claude/GPT/etc) y el LLM
#  obtiene TODA la API, sus credenciales y las reglas de uso.
# ─────────────────────────────────────────────
def _render_llms_txt(client: Optional[dict] = None) -> str:
    api_base = os.environ.get("PUBLIC_API_BASE", "https://9lrgs4st13.execute-api.us-east-1.amazonaws.com/dev")
    if client:
        client_name = client.get("name", "")
        api_key = client.get("api_key", "")
        instance_id = client.get("instance_id", "")
        person_doc = client.get("person_document", "")
        person_name = client.get("person_name", "")
        creds_block = f"""## Credenciales (auto-inyectadas — listas para usar)

Estas credenciales pertenecen al cliente **{client_name}** y deben usarse en TODAS las llamadas a `/proxy`:

- `api_base`: `{api_base}`
- `x-api-key` (header): `{api_key}`
- `instance_id`: `{instance_id}`  ← el proxy lo inyecta solo, no lo agregues al body manualmente
- Remitente registrado (ownership): `{person_name}` — DNI/RUC `{person_doc}`
"""
    else:
        creds_block = f"""## Credenciales

Esta es la vista genérica de llms.txt. Para obtener credenciales personalizadas (api_key e instance_id auto-inyectadas), pedile al administrador que te genere un magic token y volvé a abrir:

`{api_base}/llms.txt?token=<tu_magic_token>`

API base por defecto: `{api_base}`
"""

    return f"""# Shalom API Management Portal — Proxy Seguro

> Plataforma serverless que enruta operaciones de envío hacia la API oficial de Shalom Perú con inyección automática de credenciales, validación de ownership de guías y conversión de binarios a base64. Tu LLM puede usar este archivo como contexto para asistir con integraciones, generación de payloads y resolución de errores.

{creds_block}

## Reglas críticas (leé esto antes de generar requests)

1. **Toda llamada a Shalom pasa por** `POST {api_base}/proxy` con el header `x-api-key`. NO llames directo a `https://ecomapp.shalom-api.lat`.
2. **El proxy inyecta `instanceId` automáticamente** en los endpoints que lo requieren: `/register-individual`, `/register`, `/pending-shipments`, `/get-user`, `/update-password`, `/update-contact-1`, `/update-contact-2`, `/quote`, `/ticket-image`, `/ticket-pdf`, `/label`. NO incluyas `instanceId` en el body.
3. **Endpoints master (no requieren credenciales del usuario, solo la api_key del cliente)**: `/quote`, `/track`, `/track-massive`, `/ticket-image`, `/ticket-pdf`, `/label`, `/list`, `/list-minimal`, `/status`, `/instances`.
4. **Ownership filter** en `/track`, `/track-massive`, `/ticket-image`, `/ticket-pdf`, `/label`: el proxy verifica que la guía pertenezca al DNI/RUC del cliente. Guías ajenas → `403`.
5. **Binarios devuelven base64** (NO raw bytes ni raw_text): `/ticket-pdf`, `/ticket-image`, `/label` → JSON `{{"success": true, "content_type": "...", "encoding": "base64", "base64": "...", "status_code": 200}}`. Decodificá con `atob` (JS) o `base64.b64decode` (Python).
6. **`/login` y `/logout` están bloqueados** desde `/proxy`. Para renovar la sesión Shalom, usá `POST {api_base}/auth/refresh-session` con el magic token.
7. **Bloqueo de input**: el campo `clave` en endpoints empresariales requiere los 4 dígitos del PIN Shalom (NO la contraseña).

## Wrapper request — formato OBLIGATORIO para POST /proxy

```json
{{
  "method": "post",
  "path": "/track",
  "body": {{ "orderNumber": "81221187", "orderCode": "3WHN" }}
}}
```

- `method`: `"get"` o `"post"`.
- `path`: el path de Shalom (ej: `/track`, `/register-individual`).
- `body`: el payload de negocio sin `instanceId` (el proxy lo agrega).

## Manejo de errores — IMPORTANTE para tu integración

Todo error sale con el **status HTTP correcto** (NO siempre 200) y un body tipado que te dice DE QUIÉN es la culpa, vía `error.source`:

```json
{{ "success": false,
   "error": {{ "source": "shalom_session", "code": "AUTOLOGIN_FAILED",
              "message": "...", "upstream_status": 500, "path": "/register-individual",
              "recovery_attempted": true }} }}
```

Valores de `error.source` (usalo para decidir qué hacer):

| `source` | Significa | HTTP | Qué hacer |
|----------|-----------|------|-----------|
| `shalom_session` | La sesión Shalom venció y el re-login automático no fue aceptado | `401` | Avisar al admin que revise las credenciales Shalom del cliente, o llamar `POST {api_base}/auth/refresh-session` |
| `shalom_down` | Shalom no responde (timeout / conexión / 502-503-504) | `503`/`504` | NO es tu culpa ni de tus datos. Reintentar en unos minutos |
| `shalom_upstream` | Shalom rechazó la solicitud (datos inválidos, etc.) | status real de Shalom | Corregir el payload según `error.message` |
| `proxy_internal` | Error interno del proxy (no de Shalom) | `500` | Reportar al equipo del portal |

> El error `"Auto-login failed: Max retries reached"` es `source: shalom_session`. El proxy ya intenta re-loguearse solo (`recovery_attempted: true`); si igual falla, las credenciales Shalom del cliente necesitan revisión.

## Endpoints disponibles

### POST /proxy → /track  — Rastrear envío individual

Devuelve el estado actual del envío y datos del remitente/destinatario.

**Body**:
- `orderNumber` (string, requerido) — Número de guía de 8 dígitos.
- `orderCode` (string, requerido) — Código de 4 letras (ej: `WHPM`, `3WHN`).

**Ejemplo**:
```bash
curl -X POST {api_base}/proxy \\
  -H "Content-Type: application/json" \\
  -H "x-api-key: {client.get('api_key', '<TU_API_KEY>') if client else '<TU_API_KEY>'}" \\
  -d '{{"method":"post","path":"/track","body":{{"orderNumber":"81221187","orderCode":"3WHN"}}}}'
```

**Response 200**: `{{ "search": {{ "data": {{ "remitente": {{ "documento": "..." }} }} }}, "statuses": {{ "message": "En tránsito" }} }}`. **403** si la guía no es del cliente.

---

### POST /proxy → /track-massive  — Rastreo en lote

**Body**: `{{ "orders": [{{ "orderNumber": "...", "orderCode": "..." }}, ...] }}` (hasta 50).
Filtra silenciosamente guías que no pertenecen al cliente.

---

### POST /proxy → /ticket-pdf  — PDF del ticket

**Body**: `{{ "orderNumber": "...", "orderCode": "..." }}`.
**Response 200**: `{{ "encoding": "base64", "content_type": "application/pdf", "base64": "JVBERi0xLjQK..." }}`. PDF ~35KB. Solo después de recepción física.

---

### POST /proxy → /ticket-image  — PNG del ticket

**Body**: `{{ "orderNumber": "...", "orderCode": "..." }}`.
**Response 200**: `{{ "encoding": "base64", "content_type": "image/png", "base64": "iVBORw0KGgo..." }}`. PNG ~190KB.

---

### POST /proxy → /label  — Etiqueta PDF de despacho

**Body**: `{{ "orderNumber": "...", "orderCode": "..." }}`.
**Response 200**: `{{ "encoding": "base64", "content_type": "application/pdf", "base64": "..." }}`. PDF ~560KB.

---

### POST /proxy → /register-individual  — Crear envío individual

⚠️ **PRODUCCIÓN**: cada llamada genera una guía REAL y cargo.

**Body** (sin `instanceId`):
- `origen` (number) — `ter_id` numérico de la terminal origen (ver `/terminals`).
- `destino` (number) — `ter_id` destino. Para envío aéreo el string puede empezar con `"0"` (ej `"052"`).
- `documento` (string) — DNI/RUC del destinatario.
- `name`, `firstname`, `lastname`, `phone`.
- `content` (string) — descripción del paquete (ej `"SOBRE"`).
- `peso`, `alto`, `ancho`, `largo` (number) — dimensiones.
- `cantidad` (number).
- `clave` (string) — PIN de 4 dígitos de la cuenta Shalom Pro.

**Response 200**: `{{ "success": true, "data": {{ "guia": 79417376, "codigo": "9WWH", "ose_id": ..., "precio": 8 }} }}`.

---

### POST /proxy → /register  — Crear envíos en lote

**Body**: `{{ "shipments": [{{ "origin": 71, "destination": 293, "documento": "...", ... }}, ...] }}`.

---

### POST /proxy → /quote  — Cotizar costo de envío

**Body**: `{{ "origen": 71, "destino": 293, "peso": 1, "alto": 20, "ancho": 20, "largo": 20, "cantidad": 1 }}`.

---

### POST /proxy → /pending-shipments  — Listar envíos pendientes

**Body**: `{{}}`.
**Response 200**: objeto indexado por número (`{{"0": {{...}}, "1": {{...}}}}`). Cada item incluye `service_order_guia_empresarial` (= `orderNumber`) y `code_service_order_empresarial` (= `orderCode`).

---

### POST /proxy → /get-user  — Datos del remitente registrado

**Body**: `{{}}`.
**Response 200**: incluye `person.full_name`, `person.document` (DNI usado para ownership), `person.phone`, `person.ubigeo`.

---

### POST /proxy → /update-password, /update-contact-1, /update-contact-2

Mantenimiento del perfil del remitente. Body con los nuevos valores.

---

### GET {api_base}/terminals?search=<query>  — Catálogo de agencias

NO va por `/proxy`. Endpoint propio del backend. Devuelve catálogo con `ter_id` para usar en `origen`/`destino`. Caché 1h.

**Response 200**: `{{ "count": 547, "terminals": [{{ "ter_id": 71, "name": "SANTA ANITA", "ubigeo": "LIMA - LIMA - SANTA ANITA", "abbr": "STA" }}, ...] }}`.

---

### POST {api_base}/auth/refresh-session  — Renovar sesión Shalom

Si recibís 401 desde Shalom, llamá esto con el magic token para que el proxy haga login interno de nuevo.
**Body**: `{{ "token": "<magic_token>" }}`.

---

### POST {api_base}/auth/magic  — Validar magic token

**Body**: `{{ "token": "<magic_token>" }}`.
**Response 200**: `{{ "success": true, "client": {{ "name", "email", "instanceId", "apiKey" }} }}`. El token nunca expira; la revocación se hace por status:inactive o regenerate-token (admin).

## Errores típicos

- `401 Missing x-api-key header` → te faltó el header `x-api-key` en `/proxy`.
- `401 API Key inválida` → la api_key no existe o el cliente está inactivo.
- `403 Acceso denegado: esta guía no pertenece a tu cuenta` → la guía es de otro DNI. Solo podés operar tus propias guías.
- `403 El proxy gestiona la autenticación automáticamente` → intentaste llamar `/login` o `/logout` vía `/proxy` (bloqueado).
- `404 La guía solicitada no fue encontrada` → orderNumber+orderCode no existen en Shalom.
- `504 Shalom API timeout` → reintentar.

## Prompt sugerido para tu LLM

```
Usá este archivo como referencia exclusiva de la API de Shalom Proxy: {api_base}/llms.txt?token={(client.get('magic_token', '<token>') if client else '<token>')}

Toda llamada a Shalom debe ir a POST /proxy con el wrapper {{method, path, body}}.
No inventes instanceId — el servidor lo inyecta. Si necesitás credenciales (api_key) están en la sección Credenciales arriba.
Devolvé los curl ya armados con headers y body completos.
```
"""


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt(token: Optional[str] = None):
    """Markdown contextual para LLMs siguiendo el estándar llmstxt.org.
    Si recibe ?token=<magic>, embebe las credenciales del cliente."""
    client = None
    if token:
        client = database.get_client_by_token(token)
    return PlainTextResponse(
        _render_llms_txt(client),
        media_type="text/markdown",
    )


@app.get("/")
def read_root():
    return {"message": "Shalom API Management Portal v2.0 — Proxy Seguro (Serverless)"}

# AWS Lambda Handler
handler = Mangum(app)
