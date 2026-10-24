// Soybean Admin v2.3.0 tab identity: query parameters only create tabs when multiTab is enabled.
export function getTabIdByRoute(route) {
  const { path, query = {}, meta } = route;

  let id = path;

  if (meta.multiTab) {
    const queryKeys = Object.keys(query).sort();
    const qs = queryKeys.map(key => `${key}=${query[key]}`).join('&');

    id = `${path}?${qs}`;
  }

  return id;
}

