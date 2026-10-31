// Thin wrapper around the FastAPI backend. The session lives in an HttpOnly cookie,
// so every request must send credentials.
const BASE = import.meta.env.VITE_API_URL || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = 'Something went wrong. Please try again.'
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (res.status === 422) detail = 'Please enter a valid message (up to 2000 characters).'
    } catch {
      /* keep default message */
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}

export const initSession = () => request('/api/sessions', { method: 'POST' })
export const listConversations = () => request('/api/conversations')
export const createConversation = () =>
  request('/api/conversations', { method: 'POST', body: JSON.stringify({}) })
export const deleteConversation = (id) => request(`/api/conversations/${id}`, { method: 'DELETE' })
export const getMessages = (id) => request(`/api/conversations/${id}/messages`)
export const sendMessage = (id, content) =>
  request(`/api/conversations/${id}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
