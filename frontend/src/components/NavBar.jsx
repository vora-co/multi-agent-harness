import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function NavBar() {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
    setMobileOpen(false);
  };

  const isAdmin = user?.role === 'admin';

  return (
    <nav className="bg-white shadow">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          {/* Left: brand */}
          <div className="flex items-center">
            <Link to="/" className="text-xl font-bold text-indigo-600">
              Yoga Studio
            </Link>
          </div>

          {/* Right: desktop links */}
          <div className="hidden sm:flex sm:items-center sm:gap-4">
            {token ? (
              <>
                <Link to="/schedule" className="text-gray-700 hover:text-indigo-600 transition">
                  Agenda
                </Link>
                <Link to="/my-bookings" className="text-gray-700 hover:text-indigo-600 transition">
                  Mis Reservas
                </Link>
                {isAdmin && (
                  <Link to="/admin/sessions" className="text-purple-700 hover:text-purple-900 font-semibold transition">
                    Admin
                  </Link>
                )}
                <span className="text-gray-700">Hello, {user?.name || 'user'}</span>
                <button
                  onClick={handleLogout}
                  className="bg-red-500 text-white px-3 py-1 rounded hover:bg-red-600"
                >
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="text-indigo-600 hover:underline">
                  Login
                </Link>
                <Link to="/register" className="text-indigo-600 hover:underline">
                  Register
                </Link>
              </>
            )}
          </div>

          {/* Hamburger button — mobile only */}
          <div className="sm:hidden flex items-center">
            <button
              onClick={() => setMobileOpen(!mobileOpen)}
              className="text-gray-700 hover:text-indigo-600 focus:outline-none"
              aria-label="Toggle menu"
            >
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                {mobileOpen ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                )}
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="sm:hidden border-t border-gray-200">
          <div className="px-4 py-3 space-y-3">
            {token ? (
              <>
                <Link
                  to="/schedule"
                  onClick={() => setMobileOpen(false)}
                  className="block text-gray-700 hover:text-indigo-600"
                >
                  Agenda
                </Link>
                <Link
                  to="/my-bookings"
                  onClick={() => setMobileOpen(false)}
                  className="block text-gray-700 hover:text-indigo-600"
                >
                  Mis Reservas
                </Link>
                {isAdmin && (
                  <Link
                    to="/admin/sessions"
                    onClick={() => setMobileOpen(false)}
                    className="block text-purple-700 hover:text-purple-900 font-semibold"
                  >
                    Admin
                  </Link>
                )}
                <span className="block text-gray-700">Hello, {user?.name || 'user'}</span>
                <button
                  onClick={handleLogout}
                  className="block w-full text-left bg-red-500 text-white px-3 py-1 rounded hover:bg-red-600"
                >
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="block text-indigo-600 hover:underline"
                >
                  Login
                </Link>
                <Link
                  to="/register"
                  onClick={() => setMobileOpen(false)}
                  className="block text-indigo-600 hover:underline"
                >
                  Register
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </nav>
  );
}
