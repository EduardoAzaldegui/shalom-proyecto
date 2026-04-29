import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { Terminal, Key, Copy, CheckCircle2, Server, BookOpen, Play, AlertTriangle, FileJson, Loader2 } from 'lucide-react';
import Editor from '@monaco-editor/react';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://9lrgs4st13.execute-api.us-east-1.amazonaws.com/dev';
const SHALOM_API_URL = 'https://ecomapp.shalom-api.lat';

// Endpoints enrutados via nuestro proxy (inyectan Master Key internamente).
// El cliente llama a POST /proxy — el servidor inyecta lo que falta.
const MASTER_KEY_PATHS = new Set(['/quote', '/track', '/track-massive', '/ticket-image', '/ticket-pdf', '/label']);

const ENDPOINT_GROUPS = {
  'Autenticación (Proxy)': ['/auth/refresh-session'],
  'Envíos y Cotización': ['/register-individual', '/register', '/quote', '/pending-shipments'],
  'Rastreo y Etiquetas': ['/track', '/track-massive', '/ticket-image', '/ticket-pdf', '/label'],
  'Catálogo y Referencia': ['/terminals'],
  'Perfil de Usuario': ['/get-user', '/update-password', '/update-contact-1', '/update-contact-2']
};

const WARNINGS = {
  '/register': '⚠️ PRODUCCIÓN: Este endpoint registra envíos masivos REALES. Cada llamada exitosa genera guías y cargos en tu cuenta Shalom.',
  '/register-individual': '⚠️ PRODUCCIÓN: Este endpoint crea un envío REAL. La guía generada tiene costo. No modifiques el payload de ejemplo si solo quieres probar la conectividad.',
  '/track': '🔒 OWNERSHIP: El proxy valida automáticamente que la guía pertenezca a tu cuenta (por DNI del remitente). Intentar rastrear guías de otros usuarios retorna 403 Acceso Denegado. Además, Shalom NO ACTIVA el tracking hasta que el paquete es escaneado en mostrador.',
  '/track-massive': '🔒 OWNERSHIP: El proxy filtra automáticamente los resultados. Sólo se devuelven las guías que pertenecen a tu cuenta. Las guías de otros usuarios son excluidas silenciosamente.',
  '/ticket-image': '🔒 OWNERSHIP: Sólo puedes generar la imagen de tus propias guías. Intentar con una guía ajena retorna 403 Acceso Denegado.',
  '/ticket-pdf': '🔒 OWNERSHIP: Sólo puedes generar el PDF de tus propias guías. Además, este endpoint solo funciona después de que el envío es recepcionado físicamente en Shalom.',
  '/label': '🔒 OWNERSHIP: Sólo puedes generar la etiqueta de tus propias guías.',
};

const DYNAMIC_EXPLANATIONS = {
  '/auth/refresh-session': 'Renueva la sesión de Shalom automáticamente usando las credenciales seguras de tu instancia. Útil si recibes un error 401 (No Autorizado) de Shalom.',
  '/register-individual': '✅ VALIDADO: Crea una orden de envío individual. Usa campos en español: origen/destino con ter_id numérico. Retorna código de rastreo (4 letras) y número de guía.',
  '/register': 'Registra envíos en lote (múltiples shipments en un call). El campo origin del array espera el ter_id numérico de la terminal.',
  '/track': '🔒 OWNERSHIP ENFORCED: El proxy verifica que el DNI del remitente de la guía coincida con el DNI del usuario autenticado en tu cuenta Shalom. Si la guía no te pertenece, recibes 403. Requiere orderNumber (número de guía de 8 dígitos) y orderCode (código de 4 letras).',
  '/track-massive': '🔒 OWNERSHIP ENFORCED: Rastreo en lote de hasta 50 envíos. El proxy filtra automáticamente los resultados: solo retorna las guías cuyo remitente coincide con tu usuario. Las guías ajenas son excluidas sin error.',
  '/pending-shipments': '✅ VALIDADO: Retorna todos los envíos pendientes de tu instancia. Incluye ter_id de origen/destino útiles para otros endpoints.',
  '/get-user': '✅ VALIDADO: Retorna el perfil completo del usuario autenticado, incluyendo datos de persona (full_name, document/DNI), ubigeo y configuración. El DNI del campo person.document es el identificador de ownership usado por el proxy para validar guías.',
  '/quote': 'Calcula el costo estimado de un envío según origen, destino, peso y dimensiones. Procesado transparentemente por el servidor.',
  '/ticket-image': '🔒 OWNERSHIP ENFORCED: Genera imagen PNG del ticket de un envío. Solo funciona con tus propias guías — el proxy valida ownership antes de procesar.',
  '/ticket-pdf': '🔒 OWNERSHIP ENFORCED: Genera PDF del ticket. Solo funciona con tus propias guías, y solo después de que el envío es recepcionado físicamente en Shalom.',
  '/label': '🔒 OWNERSHIP ENFORCED: Genera la etiqueta de despacho de un envío. Solo funciona con tus propias guías.',
  '/terminals': '📍 Catálogo de terminales Shalom con sus ter_id numéricos. Úsalos en los campos origen y destino de /register-individual y /register. Puedes buscar por nombre o ubicación.'
};

const CUSTOM_DESCRIPTIONS = {
  'origin': 'ter_id numérico de la terminal de origen. Consulta /pending-shipments para ver los ter_id disponibles en tus envíos.',
  'destination': 'ter_id numérico de la terminal de destino. Usa string con "0" al inicio para envío aéreo (ej: "052").',
  'origen': 'ter_id numérico de la terminal de origen (ej: 71 = Santa Anita, Lima). Ver catálogo abajo.',
  'destino': 'ter_id numérico o string con "0" inicial para aéreo. (ej: 293 = Camaná, Arequipa).',
  'shipments[].origin': 'ter_id numérico de la terminal de origen. NO usar nombre en texto.',
  'shipments[].destination': 'ter_id numérico de la terminal de destino.',
  'orderNumber': 'Número de guía de 8 dígitos. Lo obtienes en la respuesta de /register-individual como campo "guia".',
  'orderCode': 'Código corto de 4 letras. Lo obtienes en la respuesta de /register-individual como campo "codigo".',
  'orders[].orderNumber': 'Número de guía (8 dígitos). Campo "guia" de la respuesta de registro.',
  'orders[].orderCode': 'Código corto (4 letras). Campo "codigo" de la respuesta de registro.',
  'instanceId': 'Tu identificador único de instancia. Se inyecta automáticamente desde tu sesión.',
  'documento': 'DNI del destinatario (8 dígitos). La API de Shalom buscará o creará el contacto automáticamente.',
  'clave': 'Código de seguridad de 4 dígitos de tu cuenta Shalom Pro. Se configura en tu perfil de Shalom.'
};

const REAL_RESPONSES = {
  '/register-individual': {
    "success": true,
    "message": "Orden de envío creada con exito!",
    "data": { "codigo": "9WWH", "guia": 79417376, "serie": "V267", "ose_id": 82891360, "precio": 8 }
  },
  '/auth/refresh-session': {
    "success": true, "message": "Sesión de Shalom refrescada correctamente."
  },
  '/pending-shipments': {
    "0": {
      "id": 979398,
      "origin_station": { "ter_id": 71, "name": "SANTA ANITA", "ubigeo": "LIMA - LIMA - SANTA ANITA", "abbreviation": "STA" },
      "destination_station": { "ter_id": 293, "name": "CAMANA", "ubigeo": "AREQUIPA - CAMANA - CAMANA", "abbreviation": "CAM" },
      "service_order_guia_empresarial": "79401580",
      "code_service_order_empresarial": "WHPM"
    }
  },
  '/track': {
    "search": {
      "success": true,
      "data": {
        "guia": "79401580",
        "codigo": "WHPM",
        "remitente": { "nombre": "JERRY RODRIGO CCOLLANA SALAZAR", "documento": "47676522" },
        "destinatario": { "nombre": "VEGA MORE JESSICA JULIANA", "documento": "41868458" },
        "origen": "LIMA / ATE-VITARTE / URB SANTA ELVIRA",
        "destino": "PIURA / PIURA / AV. GRAU - AEREO"
      }
    },
    "statuses": { "success": true, "message": "En tránsito" }
  },
  '/get-user': {
    "id": 80139,
    "person_id": 721878,
    "name": "HAMIR ZAVALA SANDOVAL",
    "email": "logipackperu@gmail.com",
    "person": {
      "full_name": "JERRY RODRIGO CCOLLANA SALAZAR",
      "document": "47676522",
      "phone": 955890830,
      "ubigeo": { "district": "SANTA ANITA", "province": "LIMA", "department": "LIMA" }
    }
  }
};

// Strip HTML tags from Shalom API error messages (they embed <ul><li> in JSON strings)
const stripHtml = (str) => {
  if (typeof str !== 'string') return str;
  return str.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
};

const cleanResponse = (data) => {
  if (!data) return data;
  const str = JSON.stringify(data);
  const cleaned = stripHtml(str);
  try { return JSON.parse(cleaned); } catch { return data; }
};

const generateFallbackExample = (schema) => {
  let example = {};
  if (schema.example) {
    example = { ...schema.example };
    // Patch incorrect values from Shalom's swagger examples
    if ('origen' in example && example.origen < 10) example.origen = 71;
    if ('origin' in example && typeof example.origin === 'string') example.origin = 71;
    if ('destino' in example && (example.destino === '052' || example.destino < 10)) example.destino = 293;
    if ('destination' in example && typeof example.destination === 'string') example.destination = 293;
    if ('clave' in example) example.clave = '5858';
  } else if (schema.properties) {
    for (const [key, val] of Object.entries(schema.properties)) {
      if (key === 'username' || key === 'email') example[key] = 'usuario@empresa.com';
      else if (key === 'password') example[key] = 'tu_contraseña_segura';
      else if (key === 'orderNumber') example[key] = '79401580';
      else if (key === 'orderCode') example[key] = 'WHPM';
      else if (key === 'orders') example[key] = [{ orderNumber: '79401580', orderCode: 'WHPM' }];
      else if (key === 'origen' || key === 'origin') example[key] = 71;
      else if (key === 'destino' || key === 'destination') example[key] = 293;
      else if (key === 'documento') example[key] = '71333169';
      else if (key === 'name') example[key] = 'Juan';
      else if (key === 'firstname') example[key] = 'Perez';
      else if (key === 'lastname') example[key] = 'Lopez';
      else if (key === 'phone') example[key] = 994941751;
      else if (key === 'clave') example[key] = '5858';
      else if (key === 'content') example[key] = 'SOBRE';
      else if (key === 'cantidad' || key === 'quantity') example[key] = 1;
      else if (key === 'peso' || key === 'weight') example[key] = 1;
      else if (key === 'alto' || key === 'height') example[key] = 20;
      else if (key === 'ancho' || key === 'width') example[key] = 20;
      else if (key === 'largo' || key === 'length') example[key] = 20;
      else if (val.type === 'string') example[key] = val.example || '';
      else if (val.type === 'number' || val.type === 'integer') example[key] = val.example || 0;
      else if (val.type === 'boolean') example[key] = true;
      else if (val.type === 'array') example[key] = [];
      else if (val.type === 'object') example[key] = {};
    }
  }

  // Usar ter_id reales para shipments en /register
  if (example.shipments && Array.isArray(example.shipments)) {
    example.shipments = example.shipments.map(s => ({
      ...s,
      origin: 71,
      destination: 293
    }));
  }
  return example;
};

export default function ClientDocs() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  
  const [clientData, setClientData] = useState(null);
  const [swagger, setSwagger] = useState(null);
  const [error, setError] = useState('');
  const [activePath, setActivePath] = useState('/auth/refresh-session');
  const [copied, setCopied] = useState(null);
  const [viewMode, setViewMode] = useState('reference');
  const [liveResponse, setLiveResponse] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [requestBody, setRequestBody] = useState('');
  const [terminals, setTerminals] = useState([]);
  const [terminalsSearch, setTerminalsSearch] = useState('');

  useEffect(() => {
    if (token) {
      axios.post(`${API_BASE}/auth/magic`, { token })
        .then(res => setClientData(res.data.client))
        .catch(err => setError('Token inválido o expirado. Solicita uno nuevo a tu administrador.'));
    } else {
      setError('No se proporcionó token de acceso.');
    }

    axios.get('/swagger.json')
      .then(res => setSwagger(res.data))
      .catch(err => console.error('Error loading swagger', err));

    // Cargar catálogo de terminales desde nuestro backend
    axios.get(`${API_BASE}/terminals`)
      .then(res => setTerminals(res.data.terminals || []))
      .catch(() => console.warn('Could not load terminals catalog'));
  }, [token]);

  useEffect(() => {
    if (activePath === '/terminals') {
      setRequestBody('');
      setLiveResponse(null);
      return;
    }
    if (swagger && clientData && activePath) {
      const endpoint = swagger.paths[activePath];
      if (!endpoint) return;
      const method = Object.keys(endpoint)[0];
      const details = endpoint[method];
      
      let initialBody = '';
      if (details?.requestBody?.content?.['application/json']?.schema) {
        const schema = details.requestBody.content['application/json'].schema;
        let example = generateFallbackExample(schema);
        // instanceId no va en el editor: el proxy lo inyecta desde la DB automáticamente
        delete example.instanceId;
        initialBody = JSON.stringify(example, null, 2);
      }
      setRequestBody(initialBody);
      setLiveResponse(null);
    }
  }, [activePath, swagger, clientData]);

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  const executeRequest = async () => {
    // /terminals is served directly from our backend
    if (activePath === '/terminals') {
      setIsExecuting(true);
      setLiveResponse(null);
      try {
        const res = await axios.get(`${API_BASE}/terminals${terminalsSearch ? '?search=' + terminalsSearch : ''}`);
        setLiveResponse(res.data);
      } catch (e) {
        setLiveResponse({ error: 'Error cargando catálogo.' });
      }
      setIsExecuting(false);
      return;
    }

    setIsExecuting(true);
    setLiveResponse(null);

    const method = Object.keys(swagger.paths[activePath])[0];
    
    let parsedBody = null;
    if (requestBody && method !== 'get') {
      try {
        parsedBody = JSON.parse(requestBody);
      } catch (e) {
        setLiveResponse({ error: "El formato JSON proporcionado es inválido." });
        setIsExecuting(false);
        return;
      }
    }

    try {
      if (activePath.startsWith('/auth/')) {
        // Ejecutar contra el backend propio, no por proxy
        const res = await axios.post(`${API_BASE}${activePath}`, { token });
        setLiveResponse(cleanResponse(res.data));
      } else {
        const res = await axios.post(`${API_BASE}/proxy`, {
          method: method,
          path: activePath,
          body: parsedBody
        }, {
          headers: {
            'x-api-key': clientData.apiKey
          }
        });
        setLiveResponse(cleanResponse(res.data));
      }
    } catch (e) {
      const errData = e.response?.data || { error: 'Network Error o timeout.' };
      setLiveResponse(cleanResponse(errData));
    }
    setIsExecuting(false);
  };

  if (error) return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="bg-red-50 text-red-600 p-6 rounded-xl max-w-md text-center font-medium shadow-sm border border-red-100">
        {error}
      </div>
    </div>
  );

  if (!clientData || !swagger) return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="animate-pulse flex items-center gap-3 text-indigo-600 font-medium">
        <Loader2 className="w-6 h-6 animate-spin" /> 
        Preparando portal interactivo...
      </div>
    </div>
  );

  let currentEndpoint = swagger.paths[activePath];
  let currentMethod = currentEndpoint ? Object.keys(currentEndpoint)[0] : 'post';
  let endpointDetails = currentEndpoint ? currentEndpoint[currentMethod] : {};

  // Inyectar schema manual para endpoints de la API que no están en el Swagger de Shalom
  if (activePath === '/terminals') {
    currentMethod = 'get';
    endpointDetails = {
      summary: 'Catálogo de Agencias (Terminales)',
      description: 'Obtiene la lista completa de todas las agencias/terminales de Shalom a nivel nacional. Útil para rellenar campos origin y destination.',
      parameters: [
        {
          name: "search",
          in: "query",
          description: "Término de búsqueda opcional para filtrar terminales",
          schema: { type: "string" }
        }
      ],
      responses: {
        '200': {
          content: {
            'application/json': {
              schema: {
                example: {
                  count: 547,
                  terminals: [
                    { ter_id: 3, name: "CHACHAPOYAS CO DOS DE MAYO", ubigeo: "AMAZONAS - CHACHAPOYAS", abbr: "CHH" }
                  ]
                }
              }
            }
          }
        }
      }
    };
  }

  // /auth/refresh-session are our own backend endpoints, not in swagger
  if (!currentEndpoint && activePath !== '/terminals' && !activePath.startsWith('/auth/')) return null;

  const getFlatProperties = (schemaProperties, requiredList = [], parentKey = '') => {
    let flat = {};
    for (const [key, val] of Object.entries(schemaProperties || {})) {
      const fullKey = parentKey ? `${parentKey}.${key}` : key;
      flat[fullKey] = { ...val, isRequired: (requiredList || []).includes(key) };
      if (val.type === 'array' && val.items && val.items.properties) {
        flat = { ...flat, ...getFlatProperties(val.items.properties, val.items.required, `${fullKey}[]`) };
      } else if (val.type === 'object' && val.properties) {
        flat = { ...flat, ...getFlatProperties(val.properties, val.required, fullKey) };
      }
    }
    return flat;
  };

  let properties = getFlatProperties(
    endpointDetails?.requestBody?.content?.['application/json']?.schema?.properties,
    endpointDetails?.requestBody?.content?.['application/json']?.schema?.required
  );

  // Mapear parámetros de Query si existen (como en GET /terminals)
  if (endpointDetails?.parameters && endpointDetails.parameters.length > 0) {
    endpointDetails.parameters.forEach(p => {
      properties[p.name] = {
        description: p.description,
        type: p.schema?.type || 'string',
        isRequired: p.required || false
      };
    });
  }
  
  const expectedResponse = REAL_RESPONSES[activePath] || endpointDetails?.responses?.['200']?.content?.['application/json']?.schema?.example || { success: true };

  const generateCurl = () => {
    const isMasterPath = MASTER_KEY_PATHS.has(activePath);

    if (activePath === '/terminals') {
      return `curl -X GET ${API_BASE}/terminals${terminalsSearch ? '?search=' + terminalsSearch : ''} \\\n  -H "Accept: application/json"`;
    }

    let bodyExample = {};
    if (endpointDetails?.requestBody?.content?.['application/json']?.schema) {
      const schema = endpointDetails.requestBody.content['application/json'].schema;
      bodyExample = generateFallbackExample(schema);
    }

    // Para endpoints MASTER (necesitan la Admin API Key de Shalom):
    // Forzamos que pasen por nuestro proxy, porque no les podemos dar la llave.
    if (isMasterPath) {
      // Eliminar instanceId del body para el proxy (lo inyecta el backend)
      delete bodyExample.instanceId;
      
      const proxyBody = {
        method: currentMethod,
        path: activePath,
        body: Object.keys(bodyExample).length > 0 ? bodyExample : undefined
      };

      return `# 🔒 REQUIERE MASTER API KEY\n# Este endpoint está restringido por Shalom. Para usarlo, debes\n# enrutar la petición a través de nuestro proxy de integración.\n\ncurl -X POST ${API_BASE}/proxy \\\n  -H "Content-Type: application/json" \\\n  -H "x-api-key: ${clientData.apiKey}" \\\n  -d '${JSON.stringify(proxyBody, null, 2)}'`;
    }

    // Para el resto de endpoints (Opción B):
    // Mostramos la petición DIRECTA a la API de Shalom usando su apiKey.
    
    const INSTANCE_PATHS = new Set(["/register-individual", "/register", "/pending-shipments", "/get-user", "/update-password", "/update-contact-1", "/update-contact-2"]);
    if (INSTANCE_PATHS.has(activePath)) {
      bodyExample.instanceId = clientData.instanceId;
    }

    // Si es nuestro propio endpoint (auth/refresh-session)
    if (activePath.startsWith('/auth/')) {
      return `# 🔐 AUTENTICACIÓN PROXY\n# Refresca la sesión de Shalom en el servidor proxy.\n\ncurl -X POST ${API_BASE}${activePath} \\\n  -H "Content-Type: application/json" \\\n  -d '{\n  "token": "${token}"\n}'`;
    }

    let curlCmd = `curl -X ${currentMethod.toUpperCase()} ${SHALOM_API_URL}${activePath} \\\n  -H "x-api-key: ${clientData.apiKey}" \\\n  -H "Content-Type: application/json"`;
    
    if (currentMethod !== 'get' && Object.keys(bodyExample).length > 0) {
      curlCmd += ` \\\n  -d '${JSON.stringify(bodyExample, null, 2)}'`;
    }

    return `# 🌐 CONEXIÓN DIRECTA A SHALOM\n# Puedes llamar a este endpoint directamente desde tus servidores.\n\n${curlCmd}`;
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col font-sans">
      <header className="bg-slate-900 text-white p-4 sticky top-0 z-10 shadow-md">
        <div className="max-w-7xl mx-auto flex justify-between items-center">
          <div className="flex items-center gap-3">
            <BookOpen className="w-6 h-6 text-indigo-400" />
            <h1 className="text-xl font-bold">Shalom API Explorer</h1>
          </div>
          <div className="flex items-center gap-2 text-sm bg-slate-800 py-1.5 px-4 rounded-full border border-slate-700">
            <span className="text-slate-400">Portal:</span>
            <span className="font-semibold text-indigo-300">{clientData.name}</span>
            <span className="text-slate-600">|</span>
            <span className="text-slate-400 text-xs font-mono">{token?.slice(0, 8)}...</span>
          </div>
        </div>
      </header>

      <div className="flex-1 max-w-7xl mx-auto w-full flex overflow-hidden">
        
        {/* Sidebar grouped by category */}
        <aside className="w-72 bg-white border-r border-slate-200 overflow-y-auto h-[calc(100vh-68px)] sticky top-[68px]">
          <div className="py-5">
            {Object.entries(ENDPOINT_GROUPS).map(([groupName, paths]) => {
              // Include paths that are in swagger OR are our own backend endpoints
              const OWN_PATHS = ['/terminals', '/auth/refresh-session'];
              const availablePaths = paths.filter(p => swagger.paths[p] || OWN_PATHS.includes(p));
              if (availablePaths.length === 0) return null;
              
              return (
                <div key={groupName} className="mb-6 px-4">
                  <h2 className="text-[11px] font-extrabold text-slate-400 uppercase tracking-widest mb-3">{groupName}</h2>
                  <nav className="space-y-1">
                    {availablePaths.map(path => {
                      const method = swagger.paths[path] ? Object.keys(swagger.paths[path])[0] : 'get';
                      const isActive = activePath === path;
                      const methodColor = method === 'get' ? 'bg-blue-50 text-blue-700' : 'bg-emerald-50 text-emerald-700';
                      
                      return (
                        <button
                          key={path}
                          onClick={() => setActivePath(path)}
                          className={`w-full text-left px-3 py-2.5 rounded-xl text-sm flex items-center gap-3 transition-all ${
                            isActive ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-100'
                          }`}
                        >
                          <span className={`text-[9px] uppercase font-bold px-1.5 py-0.5 rounded ${isActive ? 'bg-white/20 text-white' : methodColor}`}>
                            {method}
                          </span>
                          <span className="truncate font-medium">{path}</span>
                        </button>
                      );
                    })}
                  </nav>
                </div>
              );
            })}
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 overflow-y-auto p-8 h-[calc(100vh-68px)] relative pb-32">
          
          {/* Warning Banner */}
          {WARNINGS[activePath] && (
            <div className="mb-6 bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3 shadow-sm">
              <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <h4 className="text-amber-800 font-bold text-sm">Advertencia de Ejecución</h4>
                <p className="text-amber-700 text-sm mt-1">{WARNINGS[activePath]}</p>
              </div>
            </div>
          )}

          <div className="mb-8 border-b border-slate-200 pb-6">
            <div className="flex items-baseline gap-3 mb-3">
              <h2 className="text-3xl font-extrabold text-slate-800 tracking-tight">{activePath}</h2>
              <span className={`uppercase font-bold px-3 py-1 rounded-md text-sm border ${
                currentMethod === 'get' ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'
              }`}>
                {currentMethod}
              </span>
            </div>
            <p className="text-slate-600 text-lg">
              {DYNAMIC_EXPLANATIONS[activePath] || endpointDetails?.description || endpointDetails?.summary || 'Utiliza este endpoint para interactuar con la plataforma logística.'}
            </p>
          </div>

          {/* ──────── TERMINALS SPECIAL VIEW ──────── */}
          {activePath === '/terminals' ? (
            <div>
              <div className="flex items-center gap-4 mb-6">
                <input
                  type="text"
                  value={terminalsSearch}
                  onChange={e => setTerminalsSearch(e.target.value)}
                  placeholder="Buscar por ciudad o terminal..."
                  className="flex-1 px-4 py-2.5 rounded-xl border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white shadow-sm"
                />
                <button
                  onClick={executeRequest}
                  disabled={isExecuting}
                  className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold px-5 py-2.5 rounded-xl flex items-center gap-2 transition-colors disabled:opacity-50"
                >
                  <Server className="w-4 h-4" /> Buscar en Vivo
                </button>
              </div>
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div className="px-5 py-4 border-b border-slate-100 bg-slate-50 flex items-center gap-2">
                  <Server className="w-4 h-4 text-indigo-500" />
                  <h3 className="text-sm font-bold text-slate-700">Terminales Shalom — Catálogo de ter_id</h3>
                  <span className="ml-auto text-xs text-slate-400">{terminals.filter(t => !terminalsSearch || (t.name || '').toLowerCase().includes(terminalsSearch.toLowerCase()) || (t.ubigeo || '').toLowerCase().includes(terminalsSearch.toLowerCase())).length} terminales</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr className="text-[11px] text-slate-500 uppercase tracking-widest">
                        <th className="px-5 py-3 text-left font-semibold w-20">ter_id</th>
                        <th className="px-5 py-3 text-left font-semibold">Terminal</th>
                        <th className="px-5 py-3 text-left font-semibold">Ubicación (Departamento - Provincia - Distrito)</th>
                        <th className="px-5 py-3 text-left font-semibold w-16">Abrev.</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {terminals
                        .filter(t => !terminalsSearch || (t.name || '').toLowerCase().includes(terminalsSearch.toLowerCase()) || (t.ubigeo || '').toLowerCase().includes(terminalsSearch.toLowerCase()))
                        .map(t => (
                          <tr key={t.ter_id} className="hover:bg-indigo-50/40 transition-colors">
                            <td className="px-5 py-3 font-mono font-bold text-indigo-700 text-base">{t.ter_id}</td>
                            <td className="px-5 py-3 font-semibold text-slate-800">{t.name}</td>
                            <td className="px-5 py-3 text-slate-500">{t.ubigeo}</td>
                            <td className="px-5 py-3 font-mono text-slate-400 text-xs">{t.abbr}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-5 py-3 bg-amber-50 border-t border-amber-100">
                  <p className="text-xs text-amber-700">
                    <strong>¿Cómo usar este catálogo?</strong> Copia el <code className="font-mono bg-amber-200 px-1 rounded">ter_id</code> de la terminal que necesitas y úsalo en el campo <code className="font-mono bg-amber-200 px-1 rounded">origen</code> o <code className="font-mono bg-amber-200 px-1 rounded">destino</code> de <code className="font-mono bg-amber-200 px-1 rounded">/register-individual</code>.
                    El catálogo mostrado proviene del backend de tu instancia. Para ver terminales adicionales, consulta a tu ejecutivo Shalom.
                  </p>
                </div>
              </div>
            </div>
          ) : (
          <div className="grid grid-cols-12 gap-8">
            {/* Left Column: Docs */}
            <div className="col-span-6 space-y-6">
              
              <div className="bg-slate-800 rounded-2xl p-5 text-white shadow-lg border border-slate-700">
                <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
                  <Key className="w-4 h-4 text-indigo-400" /> Credenciales de Shalom
                </h3>
                <div className="flex flex-col gap-3">
                  <div className="bg-black/30 p-3 rounded-lg border border-white/10 flex justify-between items-center group">
                    <div>
                      <div className="text-xs text-emerald-400 font-bold mb-1">x-api-key (Para el Header)</div>
                      <code className="text-sm text-slate-200">{clientData.apiKey}</code>
                    </div>
                    <button onClick={() => copyToClipboard(clientData.apiKey, 'apikey')} className="text-slate-400 hover:text-white transition-colors">
                      {copied === 'apikey' ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                  <div className="bg-black/30 p-3 rounded-lg border border-white/10 flex justify-between items-center group">
                    <div>
                      <div className="text-xs text-blue-400 font-bold mb-1">instanceId (Para el JSON Body)</div>
                      <code className="text-sm text-slate-200">{clientData.instanceId}</code>
                    </div>
                    <button onClick={() => copyToClipboard(clientData.instanceId, 'instanceid')} className="text-slate-400 hover:text-white transition-colors">
                      {copied === 'instanceid' ? <CheckCircle2 className="w-4 h-4 text-blue-400" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
              </div>

              {Object.keys(properties).length > 0 && (
                <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                  <div className="px-5 py-4 border-b border-slate-100 bg-slate-50">
                    <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider">Diccionario de Datos</h3>
                  </div>
                  <div className="max-h-[500px] overflow-y-auto custom-scrollbar">
                    <table className="w-full text-left border-collapse text-sm">
                      <thead className="bg-slate-50 sticky top-0 z-10 backdrop-blur-sm border-b border-slate-100">
                        <tr className="text-[10px] text-slate-500 uppercase tracking-widest">
                          <th className="px-5 py-3 font-semibold">Parámetro</th>
                          <th className="px-5 py-3 font-semibold">Reglas</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {Object.entries(properties).map(([key, val]) => (
                          <tr key={key} className="hover:bg-slate-50/50">
                            <td className="px-5 py-4 align-top w-1/3">
                              <div className="font-mono font-semibold text-slate-800 mb-1 pl-1 border-l-2 border-slate-200 ml-[2px]">
                                {key}
                              </div>
                              <span className="text-xs text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded border border-indigo-100 font-mono ml-2">
                                {Array.isArray(val.type) ? val.type[0] : val.type}
                              </span>
                            </td>
                            <td className="px-5 py-4 align-top">
                              <div className="flex items-center gap-2 mb-1">
                                {val.isRequired && (
                                  <span className="text-[10px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded font-bold uppercase">Req</span>
                                )}
                              </div>
                              <span className="text-slate-600 font-medium">{CUSTOM_DESCRIPTIONS[key] || val.description || 'Sin descripción'}</span>
                              {val.enum && <div className="mt-1 text-xs text-slate-400 font-mono">Valores: {val.enum.join(' | ')}</div>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {(['/register-individual', '/register', '/quote'].includes(activePath)) && terminals.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-2xl overflow-hidden">
                  <div className="px-5 py-3 border-b border-amber-200 bg-amber-100 flex items-center gap-2">
                    <Server className="w-4 h-4 text-amber-700" />
                    <h3 className="text-sm font-bold text-amber-800 uppercase tracking-wider">Catálogo de Terminales (ter_id)</h3>
                  </div>
                  <div className="px-5 py-3">
                    <p className="text-xs text-amber-700 mb-3">Los campos <code className="font-mono bg-amber-200 px-1 rounded">origen</code> y <code className="font-mono bg-amber-200 px-1 rounded">destino</code> requieren el <strong>ter_id numérico</strong>. Consulta <code className="font-mono bg-amber-200 px-1 rounded">/terminals</code> para el catálogo completo.</p>
                    <table className="w-full text-xs border-collapse">
                      <thead>
                        <tr className="text-[10px] text-amber-700 uppercase">
                          <th className="py-1 text-left pr-3">ter_id</th>
                          <th className="py-1 text-left pr-3">Terminal</th>
                          <th className="py-1 text-left">Ubicación</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-amber-200">
                        {terminals.slice(0, 8).map(t => (
                          <tr key={t.ter_id}>
                            <td className="py-1.5 pr-3 font-mono font-bold text-amber-900">{t.ter_id}</td>
                            <td className="py-1.5 pr-3 font-semibold text-amber-800">{t.name}</td>
                            <td className="py-1.5 text-amber-700">{t.ubigeo}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-[10px] text-amber-600 mt-2 italic">* Mostrando 8 de {terminals.length}. Ver catálogo completo en <strong>/terminals</strong>.</p>
                  </div>
                </div>
              )}
            </div>


            {/* Right Column: Split Mode (Reference vs Playground) */}
            <div className="col-span-6 space-y-5">
              
              {/* Toggle Switch */}
              <div className="bg-slate-200/80 p-1.5 rounded-xl border border-slate-300 flex relative w-full mb-2">
                <button 
                  onClick={() => setViewMode('reference')}
                  className={`flex-1 text-sm font-bold py-3 px-4 rounded-lg transition-all flex items-center justify-center gap-2 z-10 ${viewMode === 'reference' ? 'text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                >
                  <BookOpen className="w-4 h-4" /> Referencia API
                </button>
                <button 
                  onClick={() => setViewMode('live')}
                  className={`flex-1 text-sm font-bold py-3 px-4 rounded-lg transition-all flex items-center justify-center gap-2 z-10 ${viewMode === 'live' ? 'text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                >
                  <Play className="w-4 h-4" /> Entorno de Pruebas
                </button>
                <div 
                  className={`absolute top-1.5 bottom-1.5 w-[calc(50%-6px)] bg-white rounded-lg shadow transition-transform duration-300 ease-in-out ${viewMode === 'reference' ? 'translate-x-0' : 'translate-x-[calc(100%+6px)]'}`}
                ></div>
              </div>

              {/* Mode 1: Reference (cURL + Expected Response) */}
              {viewMode === 'reference' && (
                <div className="flex flex-col gap-5">
                  <div className="bg-[#1e1e2e] rounded-2xl overflow-hidden shadow-xl border border-slate-700 flex flex-col h-[280px]">
                    <div className="flex justify-between items-center p-3 bg-[#181825] border-b border-white/10">
                      <span className="text-xs font-bold text-slate-400 uppercase flex items-center gap-2">
                        <Terminal className="w-4 h-4" /> Ejemplo cURL
                      </span>
                      <button onClick={() => copyToClipboard(generateCurl(), 'curl')} className="text-slate-400 hover:text-white">
                        {copied === 'curl' ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                      </button>
                    </div>
                    <div className="p-5 overflow-auto flex-1 custom-scrollbar">
                      <pre className="text-sm text-[#cba6f7] font-mono whitespace-pre-wrap leading-relaxed">
                        {generateCurl()}
                      </pre>
                    </div>
                  </div>

                  <div className="bg-[#1e1e2e] rounded-2xl overflow-hidden shadow-xl border border-blue-900/50 flex flex-col h-[280px]">
                    <div className="flex justify-between items-center p-3 bg-[#181825] border-b border-white/10">
                      <span className="text-xs font-bold text-blue-400 uppercase flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-blue-400"></span> Respuesta Modelo (200 OK)
                      </span>
                      <button onClick={() => copyToClipboard(JSON.stringify(expectedResponse, null, 2), 'example')} className="text-slate-400 hover:text-white">
                        {copied === 'example' ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                      </button>
                    </div>
                    <div className="p-5 overflow-auto flex-1 custom-scrollbar">
                      <pre className="text-sm text-[#89b4fa] font-mono whitespace-pre-wrap leading-relaxed">
                        {JSON.stringify(expectedResponse, null, 2)}
                      </pre>
                    </div>
                  </div>
                </div>
              )}

              {/* Mode 2: Live Playground */}
              {viewMode === 'live' && (
                <div className="flex flex-col gap-5 h-full">
                  
                  {/* Editor */}
                  <div className="bg-[#1e1e2e] rounded-2xl overflow-hidden shadow-lg border border-indigo-500/30 flex flex-col h-[280px] relative">
                    {/* Loading Overlay */}
                    {isExecuting && (
                      <div className="absolute inset-0 bg-[#1e1e2e]/80 backdrop-blur-sm flex flex-col items-center justify-center z-20">
                        <Loader2 className="w-10 h-10 text-indigo-500 animate-spin mb-3" />
                        <span className="text-indigo-300 font-bold tracking-widest text-sm uppercase">Enviando a Shalom API...</span>
                      </div>
                    )}
                    
                    <div className="flex justify-between items-center p-3 bg-[#181825] border-b border-indigo-500/30">
                      <span className="text-xs font-bold text-indigo-400 uppercase flex items-center gap-2">
                        <FileJson className="w-4 h-4" /> Petición JSON (Editable)
                      </span>
                      <button 
                        onClick={executeRequest}
                        disabled={isExecuting}
                        className="bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold px-4 py-1.5 rounded-lg flex items-center gap-2 transition-colors disabled:opacity-50 z-10"
                      >
                        <Play className="w-3 h-3" /> Lanzar Petición en Vivo
                      </button>
                    </div>
                    {currentMethod === 'get' ? (
                      <div className="flex-1 flex flex-col items-center justify-center text-slate-500 bg-[#181825] p-5 text-center text-sm gap-4">
                        <p>Los endpoints GET no envían cuerpo en la petición.</p>
                        {activePath === '/terminals' && (
                          <div className="flex flex-col items-start gap-1 w-full max-w-sm">
                            <label className="text-[10px] uppercase font-bold text-slate-400">Parámetros Query</label>
                            <div className="flex items-center gap-2 w-full bg-[#1e1e2e] border border-slate-700/50 rounded-lg p-2 hover:border-indigo-500/50 transition-colors">
                              <span className="text-xs font-mono text-indigo-400 bg-indigo-500/10 px-2 py-1 rounded">?search=</span>
                              <input 
                                type="text" 
                                value={terminalsSearch} 
                                onChange={(e) => setTerminalsSearch(e.target.value)} 
                                placeholder="Buscar terminal por nombre..." 
                                className="flex-1 bg-transparent border-none outline-none text-slate-300 text-sm font-mono placeholder:text-slate-600"
                              />
                            </div>
                          </div>
                        )}
                        <p className="mt-2 text-xs">Presiona el botón "Lanzar Petición en Vivo" para ejecutar la consulta.</p>
                      </div>
                    ) : (
                      <div className="flex-1 w-full bg-[#1e1e2e]">
                        <Editor
                          height="100%"
                          defaultLanguage="json"
                          theme="vs-dark"
                          value={requestBody}
                          onChange={(value) => setRequestBody(value || '')}
                          options={{
                            minimap: { enabled: false },
                            scrollBeyondLastLine: false,
                            fontSize: 13,
                            fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
                            padding: { top: 16, bottom: 16 },
                            formatOnPaste: true,
                            tabSize: 2,
                            wordWrap: "on"
                          }}
                        />
                      </div>
                    )}
                  </div>

                  {/* Result */}
                  <div className="bg-[#1e1e2e] rounded-2xl overflow-hidden shadow-lg border border-emerald-900/50 flex flex-col flex-1 min-h-[280px]">
                    <div className="flex items-center p-3 bg-[#181825] border-b border-white/10">
                      <span className="text-xs font-bold text-emerald-400 uppercase flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${liveResponse ? 'bg-emerald-400' : 'bg-slate-600'}`}></span>
                        Respuesta de Shalom API (En Vivo)
                      </span>
                    </div>
                    <div className="p-4 overflow-y-auto flex-1 custom-scrollbar">
                      {liveResponse ? (
                        <pre className="text-xs text-[#a6e3a1] font-mono whitespace-pre-wrap leading-relaxed">
                          {JSON.stringify(liveResponse, null, 2)}
                        </pre>
                      ) : (
                        <div className="h-full flex flex-col items-center justify-center text-slate-600 text-xs font-mono gap-3">
                          <Terminal className="w-8 h-8 opacity-40" />
                          <span>Esperando ejecución...</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

            </div>
          </div>
          )} {/* end terminals ternary */}
        </main>
      </div>
    </div>
  );
}
