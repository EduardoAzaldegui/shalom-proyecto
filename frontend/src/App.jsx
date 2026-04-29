import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import AdminPanel from './pages/AdminPanel';
import AdminLogin from './pages/AdminLogin';
import ClientDocs from './pages/ClientDocs';

function ProtectedRoute({ children }) {
  const token = localStorage.getItem('admin_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

import { Toaster } from 'sonner';

function App() {
  return (
    <Router>
      <Toaster richColors position="top-right" />
      <div className="min-h-screen bg-slate-50">
        <Routes>
          <Route path="/login" element={<AdminLogin />} />
          <Route 
            path="/admin" 
            element={
              <ProtectedRoute>
                <AdminPanel />
              </ProtectedRoute>
            } 
          />
          <Route path="/docs" element={<ClientDocs />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
