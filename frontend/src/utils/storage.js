/**
 * localStorage 封装。
 * 自动 JSON 序列化/反序列化，异常时返回默认值。
 */

export function load(key, defaultValue = null) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : defaultValue
  } catch {
    return defaultValue
  }
}

export function save(key, value) {
  localStorage.setItem(key, JSON.stringify(value))
}

export function remove(key) {
  localStorage.removeItem(key)
}
