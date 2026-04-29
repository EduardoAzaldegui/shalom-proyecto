import { useState, useEffect } from 'react';
import axios from 'axios';
import { Copy, RefreshCw, Key, UserPlus, LogOut, Trash2, Power, PowerOff, Loader2, User, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://9lrgs4st13.execute-api.us-east-1.amazonaws.com/dev';

export default function AdminPanel() {
  const [clients, setClients] = useState([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [shalomUsername, setShalomUsername] = useState('');
  const [shalomPassword, setShalomPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetchingClients, setFetchingClients] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [personModal, setPersonModal] = useState(null); // { clientName, data }
  const navigate = useNavigate();

  const getHeaders = () => {
    const token = localStorage.getItem('admin_token');
    return { headers: { Authorization: `Bearer ${token}` } };
  };

  const fetchClients = async () => {
    try {
      const res = await axios.get(`${API_BASE}/admin/clients`, getHeaders());
      setClients(res.data);
    } catch (e) {
      if (e.response?.status === 401) {
        handleLogout();
      }
      toast.error('Error cargando la lista de clientes');
    } finally {
      setFetchingClients(false);
    }
  };

  useEffect(() => {
    fetchClients();
  }, []);

  const createClient = async (e) => {
    e.preventDefault();
    setLoading(true);
    const toastId = toast.loading('Creando instancia en Shalom...');
    try {
      await axios.post(`${API_BASE}/admin/clients`, { 
        name, 
        email, 
        shalom_username: shalomUsername, 
        shalom_password: shalomPassword 
      }, getHeaders());
      setName('');
      setEmail('');
      setShalomUsername('');
      setShalomPassword('');
      toast.success('¡Cliente creado e instanciado en Shalom exitosamente!', { id: toastId });
      fetchClients();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || 'Error al crear el cliente', { id: toastId });
    } finally {
      setLoading(false);
    }
  };

  const regenerateToken = async (clientId) => {
    setActionLoading({ id: clientId, action: 'regenerate' });
    const toastId = toast.loading('Regenerando link mágico...');
    try {
      await axios.post(`${API_BASE}/admin/clients/${clientId}/regenerate-token`, {}, getHeaders());
      toast.success('Token regenerado exitosamente.', { id: toastId });
      fetchClients();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || 'Error al regenerar token', { id: toastId });
    } finally {
      setActionLoading(null);
    }
  };

  const toggleStatus = async (clientId, currentStatus) => {
    const newStatus = currentStatus === 'active' ? 'inactive' : 'active';
    const actionName = currentStatus === 'active' ? 'deshabilitar' : 'habilitar';
    setActionLoading({ id: clientId, action: 'toggle' });
    const toastId = toast.loading(`Procediendo a ${actionName} cliente...`);
    try {
      await axios.put(`${API_BASE}/admin/clients/${clientId}/status`, { status: newStatus }, getHeaders());
      toast.success(`Cliente ${actionName}do exitosamente.`, { id: toastId });
      fetchClients();
    } catch (e) {
      toast.error(`Error al ${actionName} el cliente: ` + (e.response?.data?.detail || e.message), { id: toastId });
    } finally {
      setActionLoading(null);
    }
  };

  const deleteClient = async (clientId) => {
    setActionLoading({ id: clientId, action: 'delete' });
    const toastId = toast.loading('Eliminando cliente en Shalom...');
    try {
      await axios.delete(`${API_BASE}/admin/clients/${clientId}`, getHeaders());
      toast.success('Cliente eliminado permanentemente.', { id: toastId });
      fetchClients();
    } catch (e) {
      toast.error('Error eliminando cliente: ' + (e.response?.data?.detail || e.message), { id: toastId });
    } finally {
      setActionLoading(null);
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    toast.success('Copiado al portapapeles');
  };

  const viewClientPerson = async (clientId, clientName) => {
    setActionLoading({ id: clientId, action: 'person' });
    try {
      const res = await axios.get(`${API_BASE}/admin/clients/${clientId}/user`, getHeaders());
      setPersonModal({ clientName, data: res.data });
    } catch (e) {
      toast.error('No se pudo obtener los datos del remitente: ' + (e.response?.data?.detail || e.message));
    } finally {
      setActionLoading(null);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('admin_token');
    navigate('/login');
  };

  return (
    <div className="max-w-6xl mx-auto p-8">

      {/* ── Modal de Persona Shalom ── */}
      {personModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md border border-slate-200">
            <div className="flex items-center justify-between p-5 border-b border-slate-200">
              <div className="flex items-center gap-2">
                <User className="w-5 h-5 text-indigo-600" />
                <h3 className="font-bold text-slate-800">Remitente Shalom — {personModal.clientName}</h3>
              </div>
              <button onClick={() => setPersonModal(null)} className="text-slate-400 hover:text-slate-700">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 space-y-3">
              {personModal.data.live ? (
                <>
                  <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
                    <div className="text-xs text-slate-400 uppercase tracking-wider mb-2 font-bold">Datos en Vivo (Shalom)</div>
                    <div className="space-y-2">
                      <div>
                        <span className="text-xs text-slate-500">Nombre completo</span>
                        <div className="font-semibold text-slate-800">{personModal.data.live.full_name || '—'}</div>
                      </div>
                      <div className="flex gap-6">
                        <div>
                          <span className="text-xs text-slate-500">DNI / Documento</span>
                          <div className="font-mono font-bold text-indigo-700">{personModal.data.live.document || '—'}</div>
                        </div>
                        <div>
                          <span className="text-xs text-slate-500">Teléfono</span>
                          <div className="font-mono text-slate-700">{personModal.data.live.phone || '—'}</div>
                        </div>
                        <div>
                          <span className="text-xs text-slate-500">Person ID</span>
                          <div className="font-mono text-slate-500 text-sm">{personModal.data.live.person_id || '—'}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                  {personModal.data.cached?.person_name && (
                    <div className="bg-amber-50 rounded-xl p-3 border border-amber-200">
                      <div className="text-xs text-amber-600 font-bold mb-1">Dato al momento de registro</div>
                      <div className="text-sm text-amber-800">{personModal.data.cached.person_name} — DNI: {personModal.data.cached.person_document}</div>
                    </div>
                  )}
                </>
              ) : (
                <div className="text-center py-6 text-slate-500">
                  No se pudo obtener información en vivo de Shalom.
                  {personModal.data.cached?.person_name && (
                    <div className="mt-2 font-medium text-slate-700">
                      Dato guardado: {personModal.data.cached.person_name}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="px-5 pb-5">
              <button onClick={() => setPersonModal(null)} className="w-full bg-indigo-600 text-white py-2.5 rounded-xl font-medium hover:bg-indigo-700 transition-colors">Cerrar</button>
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Key className="w-8 h-8 text-indigo-600" />
          <h1 className="text-3xl font-bold text-slate-800">Shalom Admin Portal</h1>
        </div>
        <button 
          onClick={handleLogout}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
        >
          <LogOut className="w-4 h-4" /> Cerrar Sesión
        </button>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-8">
        <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
          <UserPlus className="w-5 h-5 text-slate-500" /> Nuevo Cliente
        </h2>
        <form onSubmit={createClient} className="flex flex-col gap-4">
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <label className="block text-sm font-medium text-slate-700 mb-1">Nombre Comercial</label>
              <input 
                required
                type="text" 
                value={name} onChange={e => setName(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-4 py-2 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                placeholder="Empresa S.A."
              />
            </div>
            <div className="flex-1">
              <label className="block text-sm font-medium text-slate-700 mb-1">Email de Contacto</label>
              <input 
                required
                type="email" 
                value={email} onChange={e => setEmail(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-4 py-2 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                placeholder="contacto@empresa.com"
              />
            </div>
          </div>
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <label className="block text-sm font-medium text-slate-700 mb-1">Usuario Shalom Pro</label>
              <input 
                required
                type="text" 
                value={shalomUsername} onChange={e => setShalomUsername(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-4 py-2 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                placeholder="usuario@empresa.com"
              />
            </div>
            <div className="flex-1">
              <label className="block text-sm font-medium text-slate-700 mb-1">Contraseña Shalom Pro</label>
              <input 
                required
                type="password" 
                value={shalomPassword} onChange={e => setShalomPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-4 py-2 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                placeholder="••••••••"
              />
            </div>
            <button 
              disabled={loading}
              className="bg-indigo-600 flex items-center justify-center min-w-[160px] text-white px-6 py-2 rounded-md font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors h-[42px]"
            >
              {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : 'Crear Instancia'}
            </button>
          </div>
        </form>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 text-sm">
              <th className="px-6 py-3 font-medium">Cliente</th>
              <th className="px-6 py-3 font-medium">Remitente Shalom</th>
              <th className="px-6 py-3 font-medium">Credenciales Shalom Pro</th>
              <th className="px-6 py-3 font-medium">Link Mágico de Docs</th>
              <th className="px-6 py-3 font-medium text-right">Acciones</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {clients.map(c => {
              const magicLink = `${window.location.origin}/docs?token=${c.magic_token}`;
              return (
                <tr key={c.id} className={`hover:bg-slate-50/50 ${c.status === 'inactive' ? 'opacity-60 bg-slate-50' : ''}`}>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${c.status === 'active' ? 'bg-emerald-500' : 'bg-slate-400'}`}></span>
                      <div className="font-medium text-slate-800">{c.name}</div>
                    </div>
                    <div className="text-sm text-slate-500 ml-4">{c.email}</div>
                  </td>
                  <td className="px-6 py-4">
                    {c.person_name ? (
                      <div>
                        <div className="font-semibold text-slate-800 text-sm">{c.person_name}</div>
                        <div className="text-xs font-mono text-indigo-600 mt-0.5">DNI: {c.person_document || '—'}</div>
                      </div>
                    ) : (
                      <span className="text-slate-400 italic text-sm">No registrado</span>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <div className="font-semibold text-slate-700 text-sm">
                      {c.shalom_username || <span className="text-slate-400 italic">Sin usuario asignado</span>}
                    </div>
                    <div className="text-xs font-mono text-slate-500 mt-1">
                      ID: {c.instance_id ? c.instance_id.substring(0,8) + '...' : 'N/A'}
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <input 
                        type="text" 
                        readOnly 
                        value={c.status === 'active' ? magicLink : 'Deshabilitado'}
                        className="text-sm bg-slate-100 border border-transparent rounded px-2 py-1 w-48 text-slate-500 outline-none"
                      />
                      {c.status === 'active' && (
                        <button onClick={() => copyToClipboard(magicLink)} className="p-1.5 text-slate-400 hover:text-indigo-600 rounded">
                          <Copy className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right flex items-center justify-end gap-3">
                    <button
                      onClick={() => viewClientPerson(c.id, c.name)}
                      disabled={c.status === 'inactive' || (actionLoading?.id === c.id)}
                      className="p-2 text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
                      title="Ver datos del remitente en Shalom"
                    >
                      {actionLoading?.id === c.id && actionLoading?.action === 'person' ? <Loader2 className="w-4 h-4 animate-spin" /> : <User className="w-4 h-4" />}
                    </button>
                    <button 
                      onClick={() => regenerateToken(c.id)}
                      disabled={c.status === 'inactive' || (actionLoading?.id === c.id)}
                      className="p-2 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors disabled:opacity-50"
                      title="Regenerar Link Mágico"
                    >
                      {actionLoading?.id === c.id && actionLoading?.action === 'regenerate' ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    </button>
                    <button 
                      onClick={() => toggleStatus(c.id, c.status)}
                      disabled={actionLoading?.id === c.id}
                      className={`p-2 rounded-lg transition-colors disabled:opacity-50 ${c.status === 'active' ? 'text-slate-500 hover:text-amber-600 hover:bg-amber-50' : 'text-emerald-600 hover:bg-emerald-50'}`}
                      title={c.status === 'active' ? 'Deshabilitar Cliente' : 'Habilitar Cliente'}
                    >
                      {actionLoading?.id === c.id && actionLoading?.action === 'toggle' ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        c.status === 'active' ? <PowerOff className="w-4 h-4" /> : <Power className="w-4 h-4" />
                      )}
                    </button>
                    <button 
                      onClick={() => deleteClient(c.id)}
                      disabled={actionLoading?.id === c.id}
                      className="p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50"
                      title="Eliminar Cliente Permanentemente"
                    >
                      {actionLoading?.id === c.id && actionLoading?.action === 'delete' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                  </td>
                </tr>
              )
            })}
            {fetchingClients && (
              <tr>
                <td colSpan="5" className="px-6 py-12 text-center text-slate-500">
                  <Loader2 className="w-8 h-8 animate-spin mx-auto text-indigo-600 mb-2" />
                  Cargando clientes...
                </td>
              </tr>
            )}
            {!fetchingClients && clients.length === 0 && (
              <tr>
                <td colSpan="5" className="px-6 py-8 text-center text-slate-500">
                  No hay clientes registrados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
