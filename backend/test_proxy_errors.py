"""
Tests del manejo tipado de errores del proxy (/proxy).

Mockea `requests` y `database` para NO tocar AWS DynamoDB ni la API real de
Shalom (en particular, NO crea envíos reales con /register-individual).

Ejecutar:  python -m pytest backend/test_proxy_errors.py -v
       o:  python backend/test_proxy_errors.py
"""
import sys
import types
from unittest import mock

import requests  # real, para reusar requests.exceptions

# ── 1. Fake del módulo `database` ANTES de importar main (evita boto3/AWS) ──
fake_db = types.ModuleType("database")

_CLIENT = {
    "id": "client-1",
    "name": "Cliente Test",
    "email": "test@x.com",
    "instance_id": "inst-123",
    "api_key": "client-key-abc",
    "shalom_username": "user@empresa.com",
    "shalom_password": "secret",
    "status": "active",
    "person_document": "12345678",
}

fake_db.get_client_by_api_key = lambda key: _CLIENT if key == "client-key-abc" else None
fake_db.get_client_by_token = lambda t: None
fake_db.seed_admin = lambda: None
sys.modules["database"] = fake_db

# ── Stub de `mangum` (adaptador AWS Lambda, no necesario para tests) ──
if "mangum" not in sys.modules:
    fake_mangum = types.ModuleType("mangum")
    fake_mangum.Mangum = lambda app, **kw: app
    sys.modules["mangum"] = fake_mangum

import json  # noqa: E402
from fastapi import HTTPException  # noqa: E402
import main  # noqa: E402


class _Result:
    """Normaliza la salida de proxy_request (dict passthrough o JSONResponse)."""
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)


def call_proxy(path, body=None, api_key="client-key-abc", method="post"):
    """Invoca proxy_request directamente y normaliza la respuesta."""
    req = main.ProxyRequest(method=method, path=path, body=body or {})
    try:
        out = main.proxy_request(req, x_api_key=api_key)
    except HTTPException as e:
        return _Result(e.status_code, {"detail": e.detail})
    # JSONResponse → status real + body serializado; dict → passthrough 200
    if hasattr(out, "status_code") and hasattr(out, "body"):
        return _Result(out.status_code, json.loads(out.body))
    return _Result(200, out)


def _resp(status, json_body=None, text=None, content_type="application/json"):
    """Construye un mock de respuesta de requests."""
    m = mock.Mock()
    m.status_code = status
    m.ok = 200 <= status < 400
    m.headers = {"Content-Type": content_type}
    if json_body is not None:
        m.json.return_value = json_body
        m.text = str(json_body)
    else:
        m.json.side_effect = ValueError("no json")
        m.text = text or ""
    m.content = (text or "").encode()
    return m


AUTOLOGIN_BODY = {
    "statusCode": 500,
    "message": "Auto-login failed: Max retries reached. Please call /login with valid credentials.",
}


def _dispatch(login_result, register_results):
    """
    Devuelve un side_effect para requests.post que distingue /login del path real.
    register_results: lista de respuestas (una por intento al endpoint de negocio).
    """
    calls = {"register": 0}

    def _post(url, **kwargs):
        if url.endswith("/login"):
            if isinstance(login_result, Exception):
                raise login_result
            return login_result
        idx = min(calls["register"], len(register_results) - 1)
        calls["register"] += 1
        result = register_results[idx]
        if isinstance(result, Exception):
            raise result
        return result

    return _post, calls


# ─────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────
def test_session_error_auto_recovers_and_retries():
    """Sesión vencida → relogin OK → reintento OK → 200 transparente."""
    ok_body = {"message": "Orden de envío creada con exito!", "data": {"guia": 79417376}}
    post, calls = _dispatch(
        login_result=_resp(200, {"ok": True}),
        register_results=[_resp(500, AUTOLOGIN_BODY), _resp(200, ok_body)],
    )
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual", body={"x": 1})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["guia"] == 79417376
    assert calls["register"] == 2  # reintentó


def test_session_error_relogin_fails():
    """Sesión vencida → relogin FALLA → 401 tipado shalom_session."""
    post, _ = _dispatch(
        login_result=_resp(401, {"error": "bad creds"}),
        register_results=[_resp(500, AUTOLOGIN_BODY)],
    )
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual")
    assert r.status_code == 401, r.text
    err = r.json()["error"]
    assert err["source"] == "shalom_session"
    assert err["code"] == "AUTOLOGIN_FAILED"
    assert err["recovery_attempted"] is True
    assert err["upstream_status"] == 500


def test_session_error_persists_after_relogin():
    """Relogin OK pero el reintento SIGUE dando error de sesión → 401 tipado."""
    post, calls = _dispatch(
        login_result=_resp(200, {"ok": True}),
        register_results=[_resp(500, AUTOLOGIN_BODY), _resp(500, AUTOLOGIN_BODY)],
    )
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual")
    assert r.status_code == 401
    assert r.json()["error"]["source"] == "shalom_session"
    assert calls["register"] == 2


def test_shalom_business_error_propagates_status():
    """Error de negocio 400 → se propaga status real con source shalom_upstream."""
    post, _ = _dispatch(
        login_result=_resp(200, {}),
        register_results=[_resp(400, {"message": "ter_id de destino inválido"})],
    )
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual")
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["source"] == "shalom_upstream"
    assert "ter_id" in err["message"]


def test_shalom_gateway_error_is_down():
    """502/503/504 de Shalom → source shalom_down, HTTP 503."""
    post, _ = _dispatch(
        login_result=_resp(200, {}),
        register_results=[_resp(503, {"message": "service unavailable"})],
    )
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual")
    assert r.status_code == 503
    assert r.json()["error"]["source"] == "shalom_down"


def test_timeout_is_shalom_down():
    """Timeout → 504 source shalom_down."""
    with mock.patch.object(main.requests, "post", side_effect=requests.exceptions.Timeout()):
        r = call_proxy("/register-individual")
    assert r.status_code == 504
    body = r.json()["error"]
    assert body["source"] == "shalom_down"
    assert body["code"] == "UPSTREAM_TIMEOUT"


def test_connection_error_is_shalom_down():
    """ConnectionError → 503 source shalom_down."""
    with mock.patch.object(main.requests, "post", side_effect=requests.exceptions.ConnectionError()):
        r = call_proxy("/register-individual")
    assert r.status_code == 503
    assert r.json()["error"]["source"] == "shalom_down"


def test_happy_path_passes_through():
    """200 normal → body de Shalom tal cual."""
    ok_body = {"message": "Orden de envío creada con exito!", "data": {"guia": 1}}
    post, _ = _dispatch(login_result=_resp(200, {}), register_results=[_resp(200, ok_body)])
    with mock.patch.object(main.requests, "post", side_effect=post):
        r = call_proxy("/register-individual")
    assert r.status_code == 200
    assert r.json()["data"]["guia"] == 1


def test_invalid_api_key_still_401():
    """API key inválida → 401 (validación interna, sin tocar Shalom)."""
    r = call_proxy("/register-individual", api_key="nope")
    assert r.status_code == 401


# ─────────────────────────────────────────────
#  Health-check de Shalom (botón del panel admin)
# ─────────────────────────────────────────────
def test_health_up():
    """Shalom responde 200 → ok=True, status=up, con latencia."""
    with mock.patch.object(main.requests, "get", return_value=_resp(200, {"data": []})):
        out = main.shalom_health(is_admin=True)
    assert out["ok"] is True
    assert out["status"] == "up"
    assert out["upstream_status"] == 200
    assert isinstance(out["latency_ms"], int)


def test_health_down_on_gateway_error():
    """Shalom responde 503 → ok=False, status=down."""
    with mock.patch.object(main.requests, "get", return_value=_resp(503, {"m": "x"})):
        out = main.shalom_health(is_admin=True)
    assert out["ok"] is False
    assert out["status"] == "down"
    assert out["upstream_status"] == 503


def test_health_degraded_on_4xx():
    """Shalom responde 401 (no gateway) → status=degraded."""
    with mock.patch.object(main.requests, "get", return_value=_resp(401, {"m": "x"})):
        out = main.shalom_health(is_admin=True)
    assert out["ok"] is False
    assert out["status"] == "degraded"


def test_health_down_on_timeout():
    """Timeout al pingear → status=down."""
    with mock.patch.object(main.requests, "get", side_effect=requests.exceptions.Timeout()):
        out = main.shalom_health(is_admin=True)
    assert out["ok"] is False
    assert out["status"] == "down"
    assert out["upstream_status"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
