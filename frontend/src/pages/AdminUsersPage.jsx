import { useState, useEffect, useCallback } from 'react';
import { adminGetUsers, adminAddCredits } from '../api/admin';

export default function AdminUsersPage() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Add credits modal
  const [modalOpen, setModalOpen] = useState(false);
  const [targetUser, setTargetUser] = useState(null);
  const [creditAmount, setCreditAmount] = useState(10);
  const [saving, setSaving] = useState(false);
  const [modalError, setModalError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { ok, status, data } = await adminGetUsers();
      if (!ok) {
        setError(data?.detail || `Error ${status}`);
        setUsers([]);
      } else {
        setUsers(data || []);
      }
    } catch (err) {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const openCreditsModal = (user) => {
    setTargetUser(user);
    setCreditAmount(10);
    setModalError('');
    setSuccessMsg('');
    setModalOpen(true);
  };

  const closeCreditsModal = () => {
    setTargetUser(null);
    setCreditAmount(10);
    setModalError('');
    setModalOpen(false);
  };

  const handleAddCredits = async () => {
    if (!targetUser) return;
    setModalError('');
    setSuccessMsg('');

    if (!creditAmount || creditAmount < 1 || creditAmount > 100) {
      setModalError('La cantidad debe estar entre 1 y 100.');
      return;
    }

    setSaving(true);
    const { ok, status, data } = await adminAddCredits(targetUser.id, creditAmount);
    if (!ok) {
      setModalError(data?.detail || `Error ${status}`);
    } else {
      setSuccessMsg(`Se agregaron ${creditAmount} créditos a ${targetUser.name}.`);
      fetchUsers();
    }
    setSaving(false);
  };

  const roleBadge = (role) => {
    if (role === 'admin') {
      return (
        <span className="bg-purple-100 text-purple-800 text-xs font-semibold px-3 py-1 rounded-full">
          Admin
        </span>
      );
    }
    return (
      <span className="bg-green-100 text-green-800 text-xs font-semibold px-3 py-1 rounded-full">
        Cliente
      </span>
    );
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-3xl font-bold text-indigo-600 mb-6">Administrar Usuarios</h1>

      {/* Success banner */}
      {successMsg && (
        <div className="mb-6 p-4 rounded-lg text-sm font-medium bg-green-100 text-green-800 border border-green-300">
          {successMsg}
          <button onClick={() => setSuccessMsg('')} className="ml-4 underline hover:no-underline">
            Cerrar
          </button>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="mb-6 p-4 rounded-lg text-sm font-medium bg-red-100 text-red-800 border border-red-300">
          {error}
          <button onClick={() => setError('')} className="ml-4 underline hover:no-underline">
            Cerrar
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex justify-center items-center py-20">
          <div className="animate-spin h-10 w-10 border-4 border-indigo-600 border-t-transparent rounded-full" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && users.length === 0 && (
        <div className="text-center py-20 text-gray-500">
          <p className="text-lg">No hay usuarios registrados.</p>
        </div>
      )}

      {/* Users table */}
      {!loading && !error && users.length > 0 && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-6 py-3 font-semibold text-gray-600">Nombre</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Email</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Rol</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Créditos</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {users.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50 transition">
                    <td className="px-6 py-4 font-medium text-gray-800">{user.name}</td>
                    <td className="px-6 py-4 text-gray-600">{user.email}</td>
                    <td className="px-6 py-4">{roleBadge(user.role)}</td>
                    <td className="px-6 py-4 text-gray-600 font-semibold">{user.credits}</td>
                    <td className="px-6 py-4">
                      <button
                        onClick={() => openCreditsModal(user)}
                        className="text-indigo-600 hover:text-indigo-800 underline font-medium text-sm"
                      >
                        Agregar créditos
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Add Credits Modal */}
      {modalOpen && targetUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Overlay */}
          <div className="absolute inset-0 bg-black bg-opacity-50" onClick={closeCreditsModal} />

          {/* Modal card */}
          <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h2 className="text-xl font-bold text-gray-800 mb-2">Agregar créditos</h2>
            <p className="text-gray-600 mb-4">
              Usuario: <strong>{targetUser.name}</strong> ({targetUser.email})
            </p>
            <p className="text-sm text-gray-500 mb-4">
              Créditos actuales: <strong>{targetUser.credits}</strong>
            </p>

            {/* Modal error */}
            {modalError && (
              <div className="mb-4 p-3 rounded bg-red-100 text-red-800 text-sm">
                {modalError}
              </div>
            )}

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Cantidad (1-100)
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={creditAmount}
                onChange={(e) => setCreditAmount(Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={closeCreditsModal}
                disabled={saving}
                className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition disabled:opacity-50"
              >
                Cancelar
              </button>
              <button
                onClick={handleAddCredits}
                disabled={saving}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-2"
              >
                {saving && (
                  <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                )}
                Agregar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
