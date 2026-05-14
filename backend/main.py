from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any
import requests
import os
import time
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
    try:
        if req.method.upper() == "GET":
            resp = requests.get(url, headers=forward_headers, timeout=15)
        elif req.method.upper() == "POST":
            resp = requests.post(url, headers=forward_headers, json=body, timeout=15)
        else:
            raise HTTPException(status_code=400, detail="Método no soportado.")

        # Detectar binarios (PDF/PNG/JPG) y devolver base64 antes de intentar JSON.
        # resp.text fuerza UTF-8 y corrompe el binario.
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type in BINARY_CONTENT_TYPES:
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
            return {"raw_text": resp.text, "status_code": resp.status_code}

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
        raise HTTPException(status_code=504, detail="Shalom API timeout. Intentá de nuevo.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def read_root():
    return {"message": "Shalom API Management Portal v2.0 — Proxy Seguro (Serverless)"}

# AWS Lambda Handler
handler = Mangum(app)
