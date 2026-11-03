import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import logo from './assets/logo.png'
import {
  createConversation,
  deleteConversation,
  getMessages,
  initSession,
  listConversations,
  sendMessage,
} from './api'

const ACTIVE_KEY = 'nyaya_active_conversation'

const SUGGESTIONS = [
  'What does Article 21 say?',
  'Can police arrest me without telling me the reason?',
  'Someone hit my car and ran away. What can I do?',
]

function Sources({ citations }) {
  if (!citations?.length) return null
  return (
    <div className="sources">
      <span className="sources-label">Sources</span>
      {citations.map((c) => (
        <a
          key={c.passage_id}
          href={c.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="chip"
        >
          {c.provision}
          <span className="chip-doc">{c.document_title}</span>
        </a>
      ))}
    </div>
  )
}

// Friendly text for limit errors from the backend (see services/limits.py); other errors keep their message.
function errorText(e) {
  if (e.code === 'daily_quota_exceeded' || e.code === 'service_budget_exhausted') {
    const resetAt = e.resetsAt ? new Date(e.resetsAt) : null
    const when =
      resetAt && !Number.isNaN(resetAt.getTime())
        ? resetAt.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })
        : 'tomorrow'
    return e.code === 'daily_quota_exceeded'
      ? `You've used today's free allowance. You can ask again after ${when}.`
      : `Nyaya-AI has reached its usage limit for today. Please try again after ${when}.`
  }
  if (e.code === 'rate_limited' && e.retryAfter) {
    return `${e.message} (You can try again in about ${e.retryAfter} seconds.)`
  }
  return e.message
}

function Message({ m }) {
  if (m.role === 'user') {
    return (
      <div className="row user">
        <div className="bubble">{m.content}</div>
      </div>
    )
  }
  return (
    <div className="row assistant">
      <img src={logo} alt="" className="avatar" />
      <div className="reply">
        <div className="reply-text">
          <ReactMarkdown
            components={{
              // Links from the model open in a new tab, safely.
              a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
            }}
          >
            {m.content}
          </ReactMarkdown>
        </div>
        <Sources citations={m.citations} />
      </div>
    </div>
  )
}

function Typing() {
  return (
    <div className="row assistant">
      <img src={logo} alt="" className="avatar" />
      <div className="typing" aria-label="Nyaya-AI is thinking">
        <span />
        <span />
        <span />
      </div>
    </div>
  )
}

export default function App() {
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true) // initial session + history load
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [conversations, setConversations] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 820)
  const endRef = useRef(null)
  const textareaRef = useRef(null)
  // Guards against double submits (e.g. Enter pressed twice) before the `sending` state re-renders.
  const sendingRef = useRef(false)

  // On first load: start/refresh the anonymous session and restore the latest conversation.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await initSession()
        const convs = await listConversations()
        if (!cancelled) setConversations(convs)
        // Restore the chat the user was last in. If they clicked "New chat" (stored as ''),
        // stay on the landing page; with nothing stored yet, open the most recent chat.
        const saved = localStorage.getItem(ACTIVE_KEY)
        const target = saved === null ? convs[0] : convs.find((c) => c.id === saved)
        if (target && !cancelled) {
          setConversationId(target.id)
          setMessages(await getMessages(target.id))
        }
      } catch {
        if (!cancelled) setError('Could not reach the server. Is the backend running?')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [])

  useEffect(resizeTextarea, [input, resizeTextarea])

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await listConversations())
    } catch {
      /* the sidebar list is non-critical */
    }
  }, [])

  const closeSidebarOnMobile = () => {
    if (window.innerWidth <= 820) setSidebarOpen(false)
  }

  async function openConversation(id) {
    if (sending || id === conversationId) return closeSidebarOnMobile()
    setError('')
    try {
      const msgs = await getMessages(id)
      setConversationId(id)
      setMessages(msgs)
      localStorage.setItem(ACTIVE_KEY, id)
      closeSidebarOnMobile()
    } catch {
      setError('Could not open that chat.')
      refreshConversations()
    }
  }

  async function removeConversation(e, id) {
    e.stopPropagation()
    if (sending || !window.confirm('Delete this chat? This cannot be undone.')) return
    try {
      await deleteConversation(id)
    } catch {
      setError('Could not delete that chat.')
    }
    if (id === conversationId) newChat()
    refreshConversations()
  }

  async function send(text) {
    const content = text.trim()
    if (!content || sending || sendingRef.current) return
    sendingRef.current = true
    setError('')
    setInput('')
    setSending(true)

    // Show the user's message immediately; swap in the saved version when the server replies.
    const tempId = `tmp-${Date.now()}`
    setMessages((prev) => [...prev, { id: tempId, role: 'user', content }])

    try {
      let id = conversationId
      if (!id) {
        id = (await createConversation()).id
        setConversationId(id)
        localStorage.setItem(ACTIVE_KEY, id)
      }
      const res = await sendMessage(id, content)
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tempId),
        res.user_message,
        res.assistant_message,
      ])
      refreshConversations() // picks up the new chat / its title / new ordering
    } catch (e) {
      setMessages((prev) => prev.filter((m) => m.id !== tempId))
      setInput(content)
      if (e.status === 401) {
        // Session expired: start a fresh one; the user can resend.
        await initSession().catch(() => {})
        setConversationId(null)
        setMessages([])
        setError('Your session expired, so the chat was reset. Please send your question again.')
      } else {
        setError(errorText(e))
      }
    } finally {
      sendingRef.current = false
      setSending(false)
      textareaRef.current?.focus()
    }
  }

  function newChat() {
    localStorage.setItem(ACTIVE_KEY, '')
    setConversationId(null)
    setMessages([])
    setError('')
    closeSidebarOnMobile()
    textareaRef.current?.focus()
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  const empty = !loading && messages.length === 0

  return (
    <div className={`app ${sidebarOpen ? 'with-sidebar' : ''}`}>
      <aside className="sidebar" aria-label="Chat history">
        <div className="side-head">
          <button
            className="icon-btn"
            onClick={() => setSidebarOpen((o) => !o)}
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>
        <button className="new-chat" onClick={newChat} disabled={sending} title="New chat">
          <span className="plus" aria-hidden="true">+</span>
          <span className="new-chat-label">New chat</span>
        </button>
        <div className="history-wrap">
          <div className="history-label">Recent</div>
          <nav className="history">
            {conversations.length === 0 && <p className="history-empty">Your chats will appear here.</p>}
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`history-item ${c.id === conversationId ? 'active' : ''}`}
                onClick={() => openConversation(c.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && openConversation(c.id)}
              >
                <span className="history-title">{c.title}</span>
                <button
                  className="history-delete"
                  onClick={(e) => removeConversation(e, c.id)}
                  aria-label={`Delete chat ${c.title}`}
                  title="Delete chat"
                >
                  ×
                </button>
              </div>
            ))}
          </nav>
        </div>
      </aside>
      <div className="scrim" onClick={() => setSidebarOpen(false)} />

      <div className="main">
      <header className="topbar">
        <div className="brand">
          <img src={logo} alt="" className="brand-logo" />
          <span className="brand-name">Nyaya-AI</span>
        </div>
      </header>

      <main className="stage">
        {empty ? (
          <section className="hero">
            <img src={logo} alt="Nyaya-AI" className="hero-logo" />
            <h1>
              Know <em>your rights</em>,<br />in plain language.
            </h1>
            <p>
              Ask about Indian law in everyday words and get clear, practical guidance.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <section className="thread">
            {messages.map((m) => (
              <Message key={m.id} m={m} />
            ))}
            {sending && <Typing />}
            <div ref={endRef} />
          </section>
        )}
      </main>

      <footer className="composer-wrap">
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <div className="composer">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            maxLength={2000}
            placeholder="Ask a legal question…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={loading}
          />
          <button
            className="send"
            onClick={() => send(input)}
            disabled={!input.trim() || sending || loading}
            aria-label="Send message"
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </div>
        <p className="disclaimer">
          General legal information, not legal advice. Please consult a lawyer for your situation.
        </p>
      </footer>
      </div>
    </div>
  )
}
