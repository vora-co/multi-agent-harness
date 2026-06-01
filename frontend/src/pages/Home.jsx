import { useAuth } from '../hooks/useAuth';

export default function Home() {
  const { user } = useAuth();

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-lg w-full bg-white rounded-xl shadow-lg p-8 text-center">
        <h1 className="text-3xl font-bold text-indigo-600 mb-2">Welcome</h1>
        <p className="text-gray-600 text-lg">
          Hello, <span className="font-semibold">{user?.name || user?.email}</span>!
        </p>
        <p className="text-gray-500 text-sm mt-1">Role: {user?.role}</p>
      </div>
    </div>
  );
}
