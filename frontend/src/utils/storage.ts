/** The storage type */
export type StorageType = 'local' | 'session';

export function createStorage<T extends object>(type: StorageType, storagePrefix: string) {
  const stg = type === 'session' ? window.sessionStorage : window.localStorage;

  const storage = {
    /**
     * Set session
     *
     * @param key Session key
     * @param value Session value
     */
    set<K extends keyof T>(key: K, value: T[K]) {
      const json = JSON.stringify(value);

      stg.setItem(`${storagePrefix}${key as string}`, json);
    },
    /**
     * Get session
     *
     * @param key Session key
     */
    get<K extends keyof T>(key: K): T[K] | null {
      const json = stg.getItem(`${storagePrefix}${key as string}`);
      if (json) {
        let storageData: T[K] | null = null;

        try {
          storageData = JSON.parse(json);
        } catch {}

        // storageData may be `false` if it is boolean type
        if (storageData !== null) {
          return storageData as T[K];
        }
      }

      stg.removeItem(`${storagePrefix}${key as string}`);

      return null;
    },
    remove(key: keyof T) {
      stg.removeItem(`${storagePrefix}${key as string}`);
    },
    clear() {
      stg.clear();
    }
  };
  return storage;
}


const storagePrefix = "cutting:";
export const localStg = createStorage<StorageType.Local>("local", storagePrefix);
export const sessionStg = createStorage<StorageType.Session>("session", storagePrefix);

// Preserve the theme selected before the official store was installed.
if (!localStg.get('themeSettings')) {
  try {
    const legacy = JSON.parse(window.localStorage.getItem('cutting-theme') || 'null');
    if (legacy && /^#[0-9a-f]{6}$/i.test(legacy.color)) {
      localStg.set('themeSettings', { themeColor: legacy.color, themeScheme: legacy.dark === true ? 'dark' : 'light' });
    }
  } catch {}
}
