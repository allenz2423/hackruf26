import { defineConfig } from "vite";
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));
const pyodideFiles = [
  "pyodide.mjs",
  "pyodide.asm.mjs",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json"
];

function selfHostPyodide() {
  const source = resolve(frontendDirectory, "node_modules/pyodide");
  return {
    name: "self-host-pyodide-runtime",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = new URL(request.url || "/", "http://localhost").pathname;
        const prefix = "/vendor/pyodide/";
        if (!path.startsWith(prefix)) return next();
        const filename = path.slice(prefix.length);
        if (!pyodideFiles.includes(filename)) {
          response.statusCode = 404;
          response.end("Not found");
          return;
        }
        void readFile(resolve(source, filename)).then((contents) => {
          const contentTypes = {
            "pyodide.mjs": "text/javascript",
            "pyodide.asm.mjs": "text/javascript",
            "pyodide.asm.wasm": "application/wasm",
            "python_stdlib.zip": "application/zip",
            "pyodide-lock.json": "application/json"
          };
          response.setHeader("Content-Type", contentTypes[filename]);
          response.setHeader("Cache-Control", "no-cache");
          response.end(contents);
        }).catch(next);
      });
    },
    async writeBundle() {
      const target = resolve(frontendDirectory, "dist/vendor/pyodide");
      await mkdir(target, { recursive: true });
      await Promise.all(pyodideFiles.map((filename) =>
        copyFile(resolve(source, filename), resolve(target, filename))
      ));
    }
  };
}

function fingerprintCssAssets() {
  return {
    name: "fingerprint-css-content",
    async writeBundle(outputOptions, bundle) {
      const outputDirectory = outputOptions.dir || resolve(frontendDirectory, "dist");
      for (const asset of Object.values(bundle)) {
        if (asset.type !== "asset" || !asset.fileName.endsWith(".css")) continue;

        const oldFileName = asset.fileName;
        const oldPath = resolve(outputDirectory, oldFileName);
        const contents = await readFile(oldPath);
        const digest = createHash("sha256").update(contents).digest("hex").slice(0, 12);
        const newFileName = oldFileName.replace(/\.css$/, `-${digest}.css`);
        const newPath = resolve(outputDirectory, newFileName);

        if (newFileName === oldFileName) continue;
        await rename(oldPath, newPath);

        const textOutputs = Object.values(bundle).filter((output) =>
          output.type === "chunk" || (output.type === "asset" && output.fileName.endsWith(".html"))
        );
        for (const output of textOutputs) {
          const outputPath = resolve(outputDirectory, output.fileName);
          const source = await readFile(outputPath, "utf8");
          const updated = source.replaceAll(oldFileName, newFileName);
          if (updated !== source) await writeFile(outputPath, updated);
        }
      }
    }
  };
}

export default defineConfig({
  plugins: [selfHostPyodide(), fingerprintCssAssets()],
  server: {
    port: 5173,
    proxy: {
      "/healthz": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      },
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true
      }
    }
  }
});
