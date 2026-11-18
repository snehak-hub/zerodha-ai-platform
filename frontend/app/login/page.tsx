'use client';

import React, { useEffect, useState } from 'react';
import { getApiBase } from '../../lib/api';
import { getToken, saveSession } from '../../lib/auth';

export default function LoginPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (getToken()) window.location.href = '/';
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const res = await fetch(`${getApiBase()}/api/auth/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mode === 'register' ? { email, password, full_name: fullName } : { email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data.detail;
        setError(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join('. ') : 'Something went wrong');
        return;
      }
      saveSession(data.access_token, data.user);
      window.location.href = '/';
    } catch {
      setError('Cannot reach the server. Make sure the backend is running.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-orange-600 text-lg font-black text-white">K</div>
          <div>
            <h1 className="text-lg font-extrabold text-slate-900">
              {mode === 'login' ? 'Sign in' : 'Create your account'}
            </h1>
            <p className="text-xs text-slate-500">AI Financial Intelligence Platform</p>
          </div>
        </div>

        {mode === 'register' && (
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Full name (optional)"
            maxLength={120}
            className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-orange-500"
          />
        )}
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          autoComplete="email"
          className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-orange-500"
        />
        <input
          type="password"
          required
          minLength={mode === 'register' ? 8 : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mode === 'register' ? 'Password (min 8 characters)' : 'Password'}
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-orange-500"
        />

        {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-orange-600 py-2 text-sm font-semibold text-white hover:bg-orange-700 disabled:opacity-60"
        >
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>

        <button
          type="button"
          onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); }}
          className="mt-3 w-full text-center text-xs text-slate-500 hover:text-orange-700"
        >
          {mode === 'login' ? "New here? Create an account" : 'Already have an account? Sign in'}
        </button>
        <p className="mt-4 text-center text-[10px] text-slate-400">
          Educational analytics only — not SEBI-registered investment advice. We never store your Zerodha password or TOTP.
        </p>
      </form>
    </main>
  );
}
