/** The storage type */
export type StorageType = 'local' | 'session';

function isStorageRestricted(error: unknown) {
  return error instanceof DOMException && (error.name === 'SecurityError' || error.name === 'QuotaExceededError');
}

export function createStorage<T extends object>(type: StorageType, storagePrefix: string) {
  const memory = new Map<string, string>();
  let stg: Storage | undefined;
  if (typeof window !== 'undefined') {
    try { stg = type === 'session' ? window.sessionStorage : window.localStorage; }
    catch (error) {
      if (!isStorageRestricted(error)) throw new Error('读取浏览器存储失败', { cause: error });
    }
  }

  function access(action: (target: Storage) => void) {
    if (!stg) return;
    try { action(stg); } catch (error) {
      if (!isStorageRestricted(error)) throw new Error('访问浏览器存储失败', { cause: error });
      // Stay in memory until reload so recovered storage keeps its saved preferences.
      stg = undefined;
    }
  }

  const storage = {
    /**
     * Set session
     *
     * @param key Session key
     * @param value Session value
     */
    set<K extends keyof T>(key: K, value: T[K]) {
      const json = JSON.stringify(value);

      const name = `${storagePrefix}${key as string}`;
      memory.set(name, json);
      access(target => target.setItem(name, json));
    },
    /**
     * Get session
     *
     * @param key Session key
     */
    get<K extends keyof T>(key: K): T[K] | null {
      const name = `${storagePrefix}${key as string}`;
      access(target => {
        const value = target.getItem(name);
        if (value === null) memory.delete(name);
        else memory.set(name, value);
      });
      const json = memory.get(name);
      if (!json) return null;
      try {
        return JSON.parse(json) as T[K] | null;
      } catch (error) {
        if (error instanceof SyntaxError) return null;
        throw new Error('解析浏览器存储失败', { cause: error });
      }
    },
    remove(key: keyof T) {
      const name = `${storagePrefix}${key as string}`;
      memory.delete(name);
      access(target => target.removeItem(name));
    },
    clear() {
      memory.clear();
      access(target => target.clear());
    }
  };
  return storage;
}


const storagePrefix = "cutting:";
export const localStg = createStorage<StorageType.Local>("local", storagePrefix);
export const sessionStg = createStorage<StorageType.Session>("session", storagePrefix);

// Preserve the theme selected before the official store was installed.
if (typeof window !== 'undefined' && !localStg.get('themeSettings')) {
  try {
    const legacy: unknown = JSON.parse(window.localStorage.getItem('cutting-theme') || 'null');
    if (legacy && typeof legacy === 'object' && 'color' in legacy
        && typeof legacy.color === 'string' && /^#[0-9a-f]{6}$/i.test(legacy.color)) {
      localStg.set('themeSettings', { themeColor: legacy.color, themeScheme: 'dark' in legacy && legacy.dark === true ? 'dark' : 'light' });
    }
  } catch (error) {
    if (!(error instanceof SyntaxError) && !isStorageRestricted(error)) {
      throw new Error('迁移主题设置失败', { cause: error });
    }
  }
}
