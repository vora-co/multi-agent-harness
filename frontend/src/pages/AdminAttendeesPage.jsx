import { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { adminGetAttendees } from '../api/admin';

export default function AdminAttendeesPage() {
  const { id: sessionId } = useParams();
  const [attendees, setAttendees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchAttendees = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { ok, status, data } = await adminGetAttendees(sessionId);
      if (!ok) {
        setError(data?.detail || `Error ${status}`);
        setAttendees([]);
      } else {
        setAttendees(data || []);
      }
    } catch (err) {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    fetchAttendees();
  }, [fetchAttendees]);

  const formatDateTime = (isoString) => {
    const d = new Date(isoString);
    return d.toLocaleString('es-MX', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6">
        <h1 className="text-3xl font-bold text-indigo-600">Asistentes — Sesión #{sessionId}</h1>
        <Link
          to="/admin/sessions"
          className="mt-4 sm:mt-0 inline-flex items-center gap-2 bg-gray-200 text-gray-700 px-4 py-2 rounded-md hover:bg-gray-300 transition font-medium"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Volver a sesiones
        </Link>
      </div>

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
      {!loading && !error && attendees.length === 0 && (
        <div className="text-center py-20 text-gray-500">
          <p className="text-lg">Esta sesión no tiene asistentes confirmados.</p>
          <p className="text-sm mt-1">
            Solo se listan los usuarios con reserva confirmada (no en lista de espera).
          </p>
        </div>
      )}

      {/* Attendees table */}
      {!loading && !error && attendees.length > 0 && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-6 py-3 font-semibold text-gray-600">Nombre</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Email</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Fecha de reserva</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {attendees.map((att) => (
                  <tr key={att.user_id} className="hover:bg-gray-50 transition">
                    <td className="px-6 py-4 font-medium text-gray-800">{att.name}</td>
                    <td className="px-6 py-4 text-gray-600">{att.email}</td>
                    <td className="px-6 py-4 text-gray-600">
                      {att.created_at ? formatDateTime(att.created_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
