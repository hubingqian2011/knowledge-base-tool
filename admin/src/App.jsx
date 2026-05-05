import { Suspense, lazy } from 'react';
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import theme from './theme';
import {
  isLoggedIn,
  getUser,
  getUserRole,
  getDefaultAdminPath,
  canAccessAdminPath,
} from './utils/auth';

import Login from './pages/login';
const AdminLayout = lazy(() => import('./layouts/AdminLayout'));
const Dashboard = lazy(() => import('./pages/dashboard'));
const Knowledge = lazy(() => import('./pages/knowledge'));
const Chat = lazy(() => import('./pages/chat'));
const Users = lazy(() => import('./pages/users'));

function PrivateRoute({ children }) {
  return isLoggedIn() ? children : <Navigate to="/login" replace />;
}

function AdminRoute({ children }) {
  const location = useLocation();
  const user = getUser();
  const role = getUserRole(user);

  if (!isLoggedIn()) {
    return <Navigate to="/login" replace />;
  }

  if (!role) {
    return <Navigate to="/login" replace />;
  }

  const currentPath = '/' + (location.pathname.split('/')[1] || '');
  if (currentPath && !canAccessAdminPath(currentPath, user)) {
    return <Navigate to={getDefaultAdminPath(user)} replace />;
  }

  return children;
}

function DefaultIndexRedirect() {
  return <Navigate to={getDefaultAdminPath()} replace />;
}

export default function App() {
  const loadingFallback = (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      正在加载页面...
    </div>
  );

  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <HashRouter>
        <Suspense fallback={loadingFallback}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <PrivateRoute>
                  <AdminLayout />
                </PrivateRoute>
              }
            >
              <Route index element={<DefaultIndexRedirect />} />
              <Route path="dashboard" element={<AdminRoute><Dashboard /></AdminRoute>} />
              <Route path="knowledge" element={<AdminRoute><Knowledge /></AdminRoute>} />
              <Route path="chat" element={<AdminRoute><Chat /></AdminRoute>} />
              <Route path="users" element={<AdminRoute><Users /></AdminRoute>} />
            </Route>
          </Routes>
        </Suspense>
      </HashRouter>
    </ConfigProvider>
  );
}
