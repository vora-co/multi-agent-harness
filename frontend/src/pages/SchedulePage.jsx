import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

export default function SchedulePage() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Filters
  const [filterStyle, setFilterStyle] = useState('');
  const [filterDate, setFilterDate] = useState('');

  // Available styles (extracted from sessions for the dropdown)
  const [styles, setStyles] = useState([]);

  // Feedback message after booking
  const [feedback, setFeedback] = useState(null);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (filterStyle) params.set('style', filterStyle);
      if (filterDate) params.set('date', filterDate);
      const query = params.toString();
      const { ok, status, data } = await apiFetch(`/sessions${query ? '?' + query : ''}`);
      if (!ok) {
        setError(data?.detail || `Error ${status}`);
        setSessions([]);
      } else {
        setSessions(data || []);
        // Extract unique styles from all sessions (we fetch all if no style filter)
        if (!filterStyle) {
          const uniqueStyles = [...new Set((data || []).map((s) => s.style))].sort();
          setStyles(uniqueStyles);
        }
      }
    } catch (err) {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, [filterStyle, filterDate]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleReserve = async (sessionId, sessionTitle) => {
    setFeedback(null);
    const { ok, status, data } = await apiFetch('/bookings', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (ok && status === 201) {
      const statusLabel = data.status === 'confirmed' ? 'Confirmada' : 'Lista de espera';
      setFeedback({
        type: 'success',
        message: `Reserva "${statusLabel}" para "${sessionTitle}" creada exitosamente.`,
      });
      // Refresh sessions to update enrolled counts
      fetchSessions();
    } else if (status === 402) {
      setFeedback({
        type: 'error',
        message: 'No tienes créditos suficientes para reservar.',
      });
    } else if (status === 400) {
      setFeedback({
        type: 'error',
        message: data?.detail || 'Ya tienes una reserva activa para esta sesión.',
      });
    } else {
      setFeedback({
        type: 'error',
        message: data?.detail || `Error ${status}`,
      });
    }
  };

  const clearFilters = () => {
    setFilterStyle('');
    setFilterDate('');
  };

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

  const hasFilters = filterStyle || filterDate;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-3xl font-bold text-indigo-600 mb-6">Agenda de Sesiones</h1>

      {/* Feedback banner */}
      {feedback && (
        <div
          className={`mb-6 p-4 rounded-lg text-sm font-medium ${
            feedback.type === 'success'
              ? 'bg-green-100 text-green-800 border border-green-300'
              : 'bg-red-100 text-red-800 border border-red-300'
          }`}
        >
          {feedback.message}
          <button
            onClick={() => setFeedback(null)}
            className="ml-4 underline hover:no-underline"
          >
            Cerrar
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex flex-col sm:flex-row gap-4 items-end">
          {/* Style filter */}
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Estilo
            </label>
            <select
              value={filterStyle}
              onChange={(e) => setFilterStyle(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">Todos los estilos</option>
              {styles.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          {/* Date filter */}
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Fecha
            </label>
            <input
              type="date"
              value={filterDate}
              onChange={(e) => setFilterDate(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Clear filters */}
          {hasFilters && (
            <button
              onClick={clearFilters}
              className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition"
            >
              Limpiar filtros
            </button>
          )}
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex justify-center items-center py-20">
          <div className="animate-spin h-10 w-10 border-4 border-indigo-600 border-t-transparent rounded-full" />
        </div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div className="bg-red-100 text-red-800 p-6 rounded-lg text-center">
          <p>{error}</p>
          <button
            onClick={fetchSessions}
            className="mt-3 text-indigo-600 underline hover:no-underline"
          >
            Reintentar
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && sessions.length === 0 && (
        <div className="text-center py-20 text-gray-500">
          <p className="text-lg">No se encontraron sesiones.</p>
          {hasFilters && (
            <p className="text-sm mt-1">Intenta con otros filtros.</p>
          )}
        </div>
      )}

      {/* Session grid - responsive */}
      {!loading && !error && sessions.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {sessions.map((session) => {
            const isFull = session.enrolled >= session.capacity;
            return (
              <div
                key={session.id}
                className="bg-white rounded-lg shadow-md hover:shadow-lg transition p-6 flex flex-col"
              >
                {/* Title */}
                <h2 className="text-xl font-bold text-gray-800 mb-2">
                  {session.title}
                </h2>

                {/* Instructor */}
                <p className="text-sm text-gray-500 mb-1">
                  Instructor: <span className="font-medium text-gray-700">{session.instructor}</span>
                </p>

                {/* Style */}
                <span className="inline-block bg-indigo-100 text-indigo-700 text-xs font-semibold px-2 py-1 rounded-full w-fit mb-2">
                  {session.style}
                </span>

                {/* Date/Time */}
                <p className="text-sm text-gray-500 mb-3">
                  {formatDateTime(session.starts_at)}
                </p>

                {/* Capacity info */}
                <div className="flex items-center justify-between mb-4">
                  <span className="text-sm text-gray-600">
                    Cupo: {session.enrolled}/{session.capacity}
                  </span>
                  {isFull && (
                    <span className="bg-yellow-100 text-yellow-800 text-xs font-semibold px-3 py-1 rounded-full">
                      Lista de espera
                    </span>
                  )}
                </div>

                {/* Reserve button */}
                <button
                  onClick={() => handleReserve(session.id, session.title)}
                  className="mt-auto w-full bg-indigo-600 text-white py-2 px-4 rounded-md hover:bg-indigo-700 transition font-medium"
                >
                  Reservar
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
