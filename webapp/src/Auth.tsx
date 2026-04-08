import { createSignal } from 'solid-js';
import type { Component } from 'solid-js';
import { useAuth } from './useAuth';

const Auth: Component = () => {
  const { user, error, loading, login, register, logout } = useAuth();
  const [username, setUsername] = createSignal('');
  const [password, setPassword] = createSignal('');
  const [mode, setMode] = createSignal<'login' | 'register'>('login');

  const handleSubmit = async (e: Event) => {
    e.preventDefault();
    if (mode() === 'login') {
      await login(username(), password());
    } else {
      await register(username(), password());
    }
  };

  return (
    <div class="auth-container">
      {user() ? (
        <div>
          <p>Welcome, {user()}!</p>
          <button onClick={logout}>Logout</button>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <h2>{mode() === 'login' ? 'Login' : 'Register'}</h2>
          <input
            type="text"
            placeholder="Username"
            value={username()}
            onInput={e => setUsername(e.currentTarget.value)}
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={password()}
            onInput={e => setPassword(e.currentTarget.value)}
            required
          />
          {error() && <div class="error">{error()}</div>}
          <button type="submit" disabled={loading()}>
            {loading() ? 'Loading...' : mode() === 'login' ? 'Login' : 'Register'}
          </button>
          <button
            type="button"
            onClick={() => setMode(mode() === 'login' ? 'register' : 'login')}
          >
            {mode() === 'login' ? 'Need an account? Register' : 'Have an account? Login'}
          </button>
        </form>
      )}
    </div>
  );
};

export default Auth;
