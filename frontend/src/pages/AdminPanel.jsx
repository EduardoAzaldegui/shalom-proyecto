import { useState, useEffect } from 'react';
import axios from 'axios';
import { Copy, RefreshCw, Key, UserPlus, LogOut, Trash2, Power, PowerOff } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://9lrgs4st13.execute-api.us-east-1.amazonaws.com/dev';

export default function AdminPanel() {
  const [clients, setClients] = useState([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [shalomUsername, setShalomUsername] = useState('');
  const [shalomPassword, setShalomPassword] = useState('');
  const [loading, setLoading] = useState(false);
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
      console.error(e);
    }
  };

  useEffect(() => {
    fetchClients();
  }, []);

  const createClient = async (e) => {
    e.preventDefault();
    setLoading(true);
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
      fetchClients();
    } catch (e) {
      alert('Error creating client');
    }
    setLoading(false);
  };

  const regenerateToken = async (clientId) => {
    if (!confirm('Are you sure? The old magic link will stop working.')) return;
    try {
      await axios.post(`${API_BASE}/admin/clients/${clientId}/regenerate-token`, {}, getHeaders());
      fetchClients();
    } catch (e) {
      alert('Error regenerating token');
    }
  };

  const toggleStatus = async (clientId, currentStatus) => {
    const newStatus = currentStatus === 'active' ? 'inactive' : 'active';
    const action = currentStatus === 'active' ? 'deshabilitar' : 'habilitar';
    if (!confirm(`¿Estás seguro de que deseas ${action} a este cliente? Esto modificará su instancia en Shalom.`)) return;
    try {
      await axios.put(`${API_BASE}/admin/clients/${clientId}/status`, { status: newStatus }, getHeaders());
      fetchClients();
    } catch (e) {
      alert(`Error al ${action} el cliente: ` + (e.response?.data?.detail || e.message));
    }
  };

  const deleteClient = async (clientId) => {
    if (!confirm('¡PELIGRO! ¿Estás totalmente seguro de eliminar este cliente? Se borrará su instancia de Shalom y perderá todo el acceso.')) return;
    try {
      await axios.delete(`${API_BASE}/admin/clients/${clientId}`, getHeaders());
      fetchClients();
    } catch (e) {
      alert('Error eliminando cliente: ' + (e.response?.data?.detail || e.message));
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    alert('Copied to clipboard!');
  };

  const handleLogout = () => {
    localStorage.removeItem('admin_token');
    navigate('/login');
  };

  return (
    <div className="max-w-6xl mx-auto p-8">
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
              className="bg-indigo-600 text-white px-6 py-2 rounded-md font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors h-[42px]"
            >
              {loading ? 'Creando...' : 'Crear Instancia'}
            </button>
          </div>
        </form>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 text-sm">
              <th className="px-6 py-3 font-medium">Cliente</th>
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
                      onClick={() => regenerateToken(c.id)}
                      disabled={c.status === 'inactive'}
                      className="p-2 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors disabled:opacity-50"
                      title="Regenerar Link Mágico"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                    <button 
                      onClick={() => toggleStatus(c.id, c.status)}
                      className={`p-2 rounded-lg transition-colors ${c.status === 'active' ? 'text-slate-500 hover:text-amber-600 hover:bg-amber-50' : 'text-emerald-600 hover:bg-emerald-50'}`}
                      title={c.status === 'active' ? 'Deshabilitar Cliente' : 'Habilitar Cliente'}
                    >
                      {c.status === 'active' ? <PowerOff className="w-4 h-4" /> : <Power className="w-4 h-4" />}
                    </button>
                    <button 
                      onClick={() => deleteClient(c.id)}
                      className="p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                      title="Eliminar Cliente Permanentemente"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              )
            })}
            {clients.length === 0 && (
              <tr>
                <td colSpan="4" className="px-6 py-8 text-center text-slate-500">
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
