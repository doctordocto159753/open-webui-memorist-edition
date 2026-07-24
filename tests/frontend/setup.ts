const values = new Map<string, string>();
const browserStorage: Storage = {
  get length(): number {
    return values.size;
  },
  clear(): void {
    values.clear();
  },
  getItem(key: string): string | null {
    return values.get(key) ?? null;
  },
  key(index: number): string | null {
    return Array.from(values.keys())[index] ?? null;
  },
  removeItem(key: string): void {
    values.delete(key);
  },
  setItem(key: string, value: string): void {
    values.set(key, value);
  },
};

// Node 25 exposes an experimental process-level localStorage without a
// configured backing file. Use a deterministic browser-compatible store in
// jsdom so frontend tests match Open WebUI instead of the Node process global.
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: browserStorage,
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: browserStorage,
});
