import { createSignal } from 'solid-js';

export function useAuth() {
  const [user, setUser] = createSignal<string | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  const [loading, setLoading] = createSignal(false);

  async function login(username: string, password: string) {
    setLoading(true);
    setError(null);
    // TODO: Replace with real API call
    await new Promise((r) => setTimeout(r, 500));
    if (username === 'demo' && password === 'demo') {
      setUser(username);
    } else {
      setError('Invalid credentials');
      setUser(null);
    }
    setLoading(false);
  }

  async function register(username: string, password: string) {
    setLoading(true);
    setError(null);
    // TODO: Replace with real API call
    await new Promise((r) => setTimeout(r, 500));
    if (username.length > 2 && password.length > 2) {
      setUser(username);
    } else {
      setError('Username and password must be at least 3 characters');
      setUser(null);
    }
    setLoading(false);
  }

  function logout() {
    setUser(null);
  }

  return { user, error, loading, login, register, logout };
}
