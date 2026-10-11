export function createRouter(routes, fallback, { beforeRender = () => {} } = {}) {
  let currentRoute = null;

  function match(hash) {
    const path = hash.replace(/^#/, "");
    if (!path || path === "/") {
      return { route: fallback, params: {}, path: "/" };
    }

    for (const route of routes) {
      const keys = [];
      const pattern = route.path
        .replace(/:[^/]+/g, (segment) => {
          keys.push(segment.slice(1));
          return "([^/]+)";
        })
        .replace(/\//g, "\\/");

      const result = path.match(new RegExp(`^${pattern}$`));
      if (result) {
        const params = Object.fromEntries(keys.map((key, index) => [key, decodeURIComponent(result[index + 1])]));
        return { route, params, path };
      }
    }

    return { route: fallback, params: {}, path };
  }

  function render() {
    beforeRender();
    currentRoute = match(window.location.hash);
    currentRoute.route.render(currentRoute.params);
  }

  window.addEventListener("hashchange", render);
  return { render, get currentRoute() { return currentRoute; } };
}
