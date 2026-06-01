import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

export default function MyBookingsPage() {
  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Confirmation modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelling, setCancelling] = useState(false);

  const fetchBookings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { ok, status, data } = await apiFetch('/bookings/me');
      if (!ok) {
        setError(data?.detail || `Error ${status}`);
        setBookings([]);
      } else {
        setBookings(data || []);
      }
    } catch (err) {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBookings();
  }, [fetchBookings]);

  const openCancelModal = (booking) => {
    setCancelTarget(booking);
    setModalOpen(true);
  };

  const closeCancelModal = () => {
    setCancelTarget(null);
    setModalOpen(false);
  };

  const handleCancel = async () => {
    if (!cancelTarget) return;
    setCancelling(true);
    try {
      const { ok, status, data } = await apiFetch(`/bookings/${cancelTarget.id}`, {
        method: 'DELETE',
      });
      if (ok) {
        // Refresh bookings list
        fetchBookings();
        closeCancelModal();
      } else if (status === 404) {
        setError('La reserva ya no existe.');
        closeCancelModal();
      } else {
        setError(data?.detail || `Error al cancelar: ${status}`);
        closeCancelModal();
      }
    } catch (err) {
      setError('Error de red al cancelar.');
    } finally {
      setCancelling(false);
    }
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

  const statusBadge = (status) => {
    switch (status) {
      case 'confirmed':
        return (
          <span className="bg-green-100 text-green-800 text-xs font-semibold px-3 py-1 rounded-full">
            Confirmada
          </span>
        );
      case 'waitlist':
        return (
          <span className="bg-yellow-100 text-yellow-800 text-xs font-semibold px-3 py-1 rounded-full">
            Lista de espera
          </span>
        );
      case 'cancelled':
        return (
          <span className="bg-red-100 text-red-800 text-xs font-semibold px-3 py-1 rounded-full">
            Cancelada
          </span>
        );
      default:
        return (
          <span className="bg-gray-100 text-gray-700 text-xs font-semibold px-3 py-1 rounded-full">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-3xl font-bold text-indigo-600 mb-6">Mis Reservas</h1>

      {/* Error banner */}
      {error && (
        <div className="mb-6 p-4 rounded-lg text-sm font-medium bg-red-100 text-red-800 border border-red-300">
          {error}
          <button
            onClick={() => setError('')}
            className="ml-4 underline hover:no-underline"
          >
            Cerrar
          </button>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="flex justify-center items-center py-20">
          <div className="animate-spin h-10 w-10 border-4 border-indigo-600 border-t-transparent rounded-full" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && bookings.length === 0 && (
        <div className="text-center py-20 text-gray-500">
          <p className="text-lg">No tienes reservas activas.</p>
          <p className="text-sm mt-1">Ve a la agenda para reservar una sesión.</p>
        </div>
      )}

      {/* Bookings table */}
      {!loading && !error && bookings.length > 0 && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-6 py-3 font-semibold text-gray-600">Sesión</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Instructor</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Estilo</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Fecha</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Estado</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {bookings.map((booking) => (
                  <tr key={booking.id} className="hover:bg-gray-50 transition">
                    <td className="px-6 py-4 font-medium text-gray-800">
                      {booking.session?.title || booking.session_title || `Sesión #${booking.session_id}`}
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {booking.session?.instructor || booking.instructor || '—'}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-block bg-indigo-100 text-indigo-700 text-xs font-semibold px-2 py-1 rounded-full">
                        {booking.session?.style || booking.style || '—'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {(booking.session?.starts_at || booking.starts_at) ? formatDateTime(booking.session?.starts_at || booking.starts_at) : '—'}
                    </td>
                    <td className="px-6 py-4">
                      {statusBadge(booking.status)}
                    </td>
                    <td className="px-6 py-4">
                      {booking.status !== 'cancelled' && (
                        <button
                          onClick={() => openCancelModal(booking)}
                          className="text-red-600 hover:text-red-800 underline font-medium text-sm"
                        >
                          Cancelar
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Cancel Confirmation Modal */}
      {modalOpen && cancelTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Overlay */}
          <div
            className="absolute inset-0 bg-black bg-opacity-50"
            onClick={closeCancelModal}
          />

          {/* Modal card */}
          <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h2 className="text-xl font-bold text-gray-800 mb-2">
              Confirmar Cancelación
            </h2>
            <p className="text-gray-600 mb-4">
              ¿Estás seguro de que deseas cancelar tu reserva para{' '}
              <strong>{cancelTarget.session?.title || cancelTarget.session_title || `Sesión #${cancelTarget.session_id}`}</strong>?
            </p>
            <p className="text-sm text-gray-500 mb-6">
              Esta acción no se puede deshacer.
            </p>

            <div className="flex justify-end gap-3">
              <button
                onClick={closeCancelModal}
                disabled={cancelling}
                className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition disabled:opacity-50"
              >
                Volver
              </button>
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2"
              >
                {cancelling && (
                  <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                )}
                Confirmar Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
