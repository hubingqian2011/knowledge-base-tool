const PAGE_CACHE_PREFIX = 'admin_ui_page_cache:';

function buildStorageKey(prefix) {
  return `${PAGE_CACHE_PREFIX}${prefix}`;
}

export function readPageCache(prefix, ttlMs) {
  try {
    const raw = sessionStorage.getItem(buildStorageKey(prefix));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    if (!parsed.savedAt || Date.now() - parsed.savedAt > ttlMs) {
      sessionStorage.removeItem(buildStorageKey(prefix));
      return null;
    }
    return parsed.data ?? null;
  } catch {
    return null;
  }
}

export function writePageCache(prefix, data) {
  try {
    sessionStorage.setItem(
      buildStorageKey(prefix),
      JSON.stringify({
        savedAt: Date.now(),
        data,
      }),
    );
  } catch {
    // ignore quota or serialization errors
  }
}

export function clearPageCache(prefix) {
  try {
    sessionStorage.removeItem(buildStorageKey(prefix));
  } catch {
    // ignore
  }
}

export function clearPageCacheByPrefix(prefix) {
  try {
    const fullPrefix = buildStorageKey(prefix);
    const keys = [];
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (key && key.startsWith(fullPrefix)) {
        keys.push(key);
      }
    }
    keys.forEach((key) => sessionStorage.removeItem(key));
  } catch {
    // ignore
  }
}
