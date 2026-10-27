/** The storage type */
export type StorageType = 'local' | 'session';

export function createStorage<T extends object>(type: StorageType, storagePrefix: string) {
  const memory = new Map<string, string>();
  let stg: Storage | undefined;
  try { stg = type === 'session' ? window.sessionStorage : window.localStorage; } catch {}

  function access(action: (target: Storage) => void) {
    if (!stg) return;
    try { action(stg); } catch {
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

      return null;
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
if (!localStg.get('themeSettings')) {
  try {
    const legacy = JSON.parse(window.localStorage.getItem('cutting-theme') || 'null');
    if (legacy && /^#[0-9a-f]{6}$/i.test(legacy.color)) {
      localStg.set('themeSettings', { themeColor: legacy.color, themeScheme: legacy.dark === true ? 'dark' : 'light' });
    }
  } catch {}
}
