'use client';

import React, { useEffect, useRef, useState } from 'react';
import { authFetch } from '../lib/auth';
import { getApiBase } from '../lib/api';

type Msg = { role: 'user' | 'assistant'; content: string; mode?: string; news?: any[]; note?: string };

interface ChatBotProps {
  portfolioId: string;
  timeframe: string;
}

const SUGGESTIONS = [
  'Summarize my portfolio',
  'Which sector am I most exposed to?',
  'Any latest news on my holdings?',
  'What does HHI mean?',
];

export function ChatBot({ portfolioId, timeframe }: ChatBotProps) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loadedHistory, setLoadedHistory] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: 'assistant',
      content:
        "Hi! I'm your portfolio assistant. Ask me about your holdings, risk, sector exposure, latest news, or any market concept.",
    },
  ]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || loadedHistory) return;
    setLoadedHistory(true);
    authFetch(`${getApiBase()}/api/chat/history`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.messages?.length) setMessages((m) => [m[0], ...d.messages]);
      })
      .catch(() => {});
  }, [open, loadedHistory]);

  const clearChat = async () => {
    await authFetch(`${getApiBase()}/api/chat/history`, { method: 'DELETE' }).catch(() => {});
    setMessages((m) => [m[0]]);
  };

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, open]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || sending) return;
    setMessages((m) => [...m, { role: 'user', content: question }]);
    setInput('');
    setSending(true);
    try {
      const res = await authFetch(`${getApiBase()}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: question, portfolio_id: portfolioId, timeframe }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: data.answer,
          mode: data.mode,
          news: data.news,
          note: data.ai_status && !data.ai_status.ok ? data.ai_status.message : undefined,
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: 'Sorry, I could not reach the server. Make sure the backend is running on port 8000.' },
      ]);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Open AI assistant"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full bg-orange-600 px-5 py-3 text-sm font-semibold text-white shadow-lg hover:bg-orange-700"
        >
          💬 Ask AI
        </button>
      )}

      {open && (
        <div className="fixed bottom-6 right-6 z-50 flex h-[32rem] w-[22rem] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl sm:w-[26rem]">
          <div className="flex items-center justify-between bg-orange-600 px-4 py-3 text-white">
            <div>
              <div className="text-sm font-bold">Portfolio AI Assistant</div>
              <div className="text-[11px] text-orange-100">Portfolio-aware · Live news · General Q&amp;A</div>
            </div>
            <div className="flex items-center">
            <button onClick={clearChat} className="mr-3 text-[11px] text-orange-100 hover:text-white" title="Delete saved chat history">
              Clear
            </button>
            <button onClick={() => setOpen(false)} aria-label="Close chat" className="text-xl leading-none hover:text-orange-200">
              ×
            </button>
            </div>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto bg-slate-50 p-3">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                    m.role === 'user' ? 'bg-orange-600 text-white' : 'border border-slate-200 bg-white text-slate-800'
                  }`}
                >
                  {m.content}
                  {m.role === 'assistant' && m.note && (
                    <div className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-[11px] text-amber-800">
                      ⚠ Basic mode: {m.note}
                    </div>
                  )}
                  {m.role === 'assistant' && m.news && m.news.length > 0 && (
                    <div className="mt-2 border-t border-slate-100 pt-2 text-[11px] text-slate-500">
                      <div className="mb-1 font-semibold uppercase tracking-wide">Sources</div>
                      {m.news.slice(0, 3).map((n, j) => (
                        <div key={j} className="truncate">• {n.headline} — {n.source}</div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {sending && <div className="text-xs text-slate-400">Thinking…</div>}
            <div ref={endRef} />
          </div>

          {messages.length <= 1 && (
            <div className="flex flex-wrap gap-2 border-t border-slate-100 bg-white px-3 pt-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-full border border-orange-200 px-3 py-1 text-xs text-orange-700 hover:bg-orange-50"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          <div className="border-t border-slate-200 bg-white p-3">
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && send(input)}
                placeholder="Ask about your portfolio, news, or markets…"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-orange-500"
              />
              <button
                onClick={() => send(input)}
                disabled={sending || !input.trim()}
                className="rounded-lg bg-orange-600 px-4 text-sm font-semibold text-white hover:bg-orange-700 disabled:opacity-50"
              >
                Send
              </button>
            </div>
            <p className="mt-2 text-[10px] text-slate-400">Educational information only — not SEBI-registered investment advice.</p>
          </div>
        </div>
      )}
    </>
  );
}
