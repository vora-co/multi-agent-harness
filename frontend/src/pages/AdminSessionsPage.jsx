import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  adminGetSessions,
  adminCreateSession,
  adminUpdateSession,
  adminDeleteSession,
} from '../api/admin';

const EMPTY_FORM = {
  title: '',
  instructor: '',
  style: '',
  starts_at: '',
  duration_minutes: 60,
  capacity: 10,
};

export default function AdminSessionsPage() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { ok, status, data } = await adminGetSessions();
      if (!ok) {
        setError(data?.detail || `Error ${status}`);
        setSessions([]);
      } else {
        setSessions(data || []);
      }
    } catch (err) {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  // --- Modal handlers ---

  const openCreateModal = () => {
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
    setFormError('');
    setModalOpen(true);
  };

  const openEditModal = (session) => {
    setEditingId(session.id);
    setForm({
      title: session.title || '',
      instructor: session.instructor || '',
      style: session.style || '',
      starts_at: session.starts_at ? session.starts_at.slice(0, 16) : '',
      duration_minutes: session.duration_minutes || 60,
      capacity: session.capacity || 10,
    });
    setFormError('');
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
    setFormError('');
  };

  const handleFormChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({
      ...prev,
      [name]: name === 'duration_minutes' || name === 'capacity' ? Number(value) : value,
    }));
  };

  const handleSave = async () => {
    setFormError('');
    // Basic validation
    if (!form.title.trim()) { setFormError('El título es requerido.'); return; }
    if (!form.instructor.trim()) { setFormError('El instructor es requerido.'); return; }
    if (!form.style.trim()) { setFormError('El estilo es requerido.'); return; }
    if (!form.starts_at) { setFormError('La fecha es requerida.'); return; }
    if (!form.duration_minutes || form.duration_minutes < 15) {
      setFormError('Duración mínima: 15 minutos.');
      return;
    }
    if (!form.capacity || form.capacity < 1) {
      setFormError('Capacidad mínima: 1.');
      return;
    }

    setSaving(true);

    const payload = {
      title: form.title.trim(),
      instructor: form.instructor.trim(),
      style: form.style.trim(),
      starts_at: form.starts_at + ':00',
      duration_minutes: form.duration_minutes,
      capacity: form.capacity,
    };

    let result;
    if (editingId) {
      result = await adminUpdateSession(editingId, payload);
    } else {
      result = await adminCreateSession(payload);
    }

    const { ok, status, data } = result;
    if (!ok) {
      setFormError(data?.detail || `Error ${status}`);
    } else {
      closeModal();
      fetchSessions();
    }
    setSaving(false);
  };

  // --- Delete handlers ---

  const openDeleteConfirm = (session) => {
    setDeleteTarget(session);
  };

  const closeDeleteConfirm = () => {
    setDeleteTarget(null);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setError('');
    const { ok, status, data } = await adminDeleteSession(deleteTarget.id);
    if (ok) {
      closeDeleteConfirm();
      fetchSessions();
    } else if (status === 409) {
      setError(data?.detail || 'No se puede eliminar una sesión con participantes inscritos.');
      closeDeleteConfirm();
    } else {
      setError(data?.detail || `Error ${status}`);
      closeDeleteConfirm();
    }
    setDeleting(false);
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

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6">
        <h1 className="text-3xl font-bold text-indigo-600">Administrar Sesiones</h1>
        <button
          onClick={openCreateModal}
          className="mt-4 sm:mt-0 inline-flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 transition font-medium"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Nueva sesión
        </button>
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
      {!loading && !error && sessions.length === 0 && (
        <div className="text-center py-20 text-gray-500">
          <p className="text-lg">No hay sesiones creadas.</p>
          <p className="text-sm mt-1">Haz clic en "Nueva sesión" para crear una.</p>
        </div>
      )}

      {/* Sessions table */}
      {!loading && !error && sessions.length > 0 && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-6 py-3 font-semibold text-gray-600">Título</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Instructor</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Estilo</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Fecha</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Capacidad</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Inscritos</th>
                  <th className="px-6 py-3 font-semibold text-gray-600">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {sessions.map((session) => (
                  <tr key={session.id} className="hover:bg-gray-50 transition">
                    <td className="px-6 py-4 font-medium text-gray-800">
                      {session.title}
                    </td>
                    <td className="px-6 py-4 text-gray-600">{session.instructor}</td>
                    <td className="px-6 py-4">
                      <span className="inline-block bg-indigo-100 text-indigo-700 text-xs font-semibold px-2 py-1 rounded-full">
                        {session.style}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {session.starts_at ? formatDateTime(session.starts_at) : '—'}
                    </td>
                    <td className="px-6 py-4 text-gray-600">{session.capacity}</td>
                    <td className="px-6 py-4 text-gray-600">{session.enrolled}</td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/admin/sessions/${session.id}/attendees`}
                          className="text-indigo-600 hover:text-indigo-800 underline font-medium text-sm"
                        >
                          Asistentes
                        </Link>
                        <button
                          onClick={() => openEditModal(session)}
                          className="text-blue-600 hover:text-blue-800 underline font-medium text-sm"
                        >
                          Editar
                        </button>
                        <button
                          onClick={() => openDeleteConfirm(session)}
                          className="text-red-600 hover:text-red-800 underline font-medium text-sm"
                        >
                          Eliminar
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Create / Edit Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Overlay */}
          <div className="absolute inset-0 bg-black bg-opacity-50" onClick={closeModal} />

          {/* Modal card */}
          <div className="relative bg-white rounded-lg shadow-xl max-w-lg w-full mx-4 p-6">
            <h2 className="text-xl font-bold text-gray-800 mb-4">
              {editingId ? 'Editar sesión' : 'Nueva sesión'}
            </h2>

            {/* Form error */}
            {formError && (
              <div className="mb-4 p-3 rounded bg-red-100 text-red-800 text-sm">
                {formError}
              </div>
            )}

            <div className="space-y-4">
              {/* Title */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Título</label>
                <input
                  name="title"
                  value={form.title}
                  onChange={handleFormChange}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ej: Vinyasa Flow"
                />
              </div>

              {/* Instructor */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Instructor</label>
                <input
                  name="instructor"
                  value={form.instructor}
                  onChange={handleFormChange}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ej: María García"
                />
              </div>

              {/* Style */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Estilo</label>
                <input
                  name="style"
                  value={form.style}
                  onChange={handleFormChange}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ej: Hatha, Vinyasa, Ashtanga"
                />
              </div>

              {/* Starts at */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Fecha y hora</label>
                <input
                  type="datetime-local"
                  name="starts_at"
                  value={form.starts_at}
                  onChange={handleFormChange}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              {/* Duration */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Duración (minutos)
                </label>
                <input
                  type="number"
                  name="duration_minutes"
                  value={form.duration_minutes}
                  onChange={handleFormChange}
                  min={15}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              {/* Capacity */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Capacidad</label>
                <input
                  type="number"
                  name="capacity"
                  value={form.capacity}
                  onChange={handleFormChange}
                  min={1}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>

            {/* Modal actions */}
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={closeModal}
                disabled={saving}
                className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition disabled:opacity-50"
              >
                Cancelar
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-2"
              >
                {saving && (
                  <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                )}
                {editingId ? 'Guardar cambios' : 'Crear sesión'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Overlay */}
          <div className="absolute inset-0 bg-black bg-opacity-50" onClick={closeDeleteConfirm} />

          {/* Modal card */}
          <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h2 className="text-xl font-bold text-gray-800 mb-2">Confirmar eliminación</h2>
            <p className="text-gray-600 mb-4">
              ¿Estás seguro de que deseas eliminar la sesión{' '}
              <strong>{deleteTarget.title}</strong>?
            </p>
            <p className="text-sm text-gray-500 mb-6">
              Esta acción no se puede deshacer. Solo se pueden eliminar sesiones sin participantes
              inscritos.
            </p>

            <div className="flex justify-end gap-3">
              <button
                onClick={closeDeleteConfirm}
                disabled={deleting}
                className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition disabled:opacity-50"
              >
                Cancelar
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2"
              >
                {deleting && (
                  <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                )}
                Eliminar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
