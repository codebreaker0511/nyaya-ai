const BASE = import.meta.env.VITE_API_URL || ''
const TOKEN_KEY = 'nyaya_session_token'

function rawFetch(path, options = {}) {
  const token = localStorage.getItem(TOKEN_KEY)
  return fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Session-Token': token } : {}),
      ...options.headers,
    },
  })
}

let sessionPromise = null

async function createSession(reset = false) {
  if (reset) {
    localStorage.removeItem(TOKEN_KEY)
    sessionPromise = null
  }
  if (!sessionPromise) {
    sessionPromise = rawFetch('/api/sessions', { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) throw new Error('Could not start a session.')
        const data = await res.json()
        localStorage.setItem(TOKEN_KEY, data.token)
        return data
      })
      .catch((e) => {
        sessionPromise = null
        throw e
      })
  }
  return sessionPromise
}

async function request(path, options = {}, retried = false) {
  await createSession()
  const res = await rawFetch(path, options)
  if (res.status === 401 && !retried) {
    await createSession(true)
    return request(path, options, true)
  }
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

export const initSession = () => createSession()
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
