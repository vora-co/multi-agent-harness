import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './hooks/useAuth';
import NavBar from './components/NavBar';
import PrivateRoute from './components/PrivateRoute';
import AdminRoute from './components/AdminRoute';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import Home from './pages/Home';
import SchedulePage from './pages/SchedulePage';
import MyBookingsPage from './pages/MyBookingsPage';
import AdminSessionsPage from './pages/AdminSessionsPage';
import AdminUsersPage from './pages/AdminUsersPage';
import AdminAttendeesPage from './pages/AdminAttendeesPage';

export default function App() {
  return (
    <AuthProvider>
      <NavBar />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <PrivateRoute>
              <Home />
            </PrivateRoute>
          }
        />
        <Route
          path="/schedule"
          element={
            <PrivateRoute>
              <SchedulePage />
            </PrivateRoute>
          }
        />
        <Route
          path="/my-bookings"
          element={
            <PrivateRoute>
              <MyBookingsPage />
            </PrivateRoute>
          }
        />

        {/* Admin routes */}
        <Route
          path="/admin/sessions"
          element={
            <AdminRoute>
              <AdminSessionsPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/sessions/:id/attendees"
          element={
            <AdminRoute>
              <AdminAttendeesPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/users"
          element={
            <AdminRoute>
              <AdminUsersPage />
            </AdminRoute>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
