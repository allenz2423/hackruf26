const DB_NAME = "screen-tutor-uploads";
const STORE_NAME = "files";
const DB_VERSION = 1;
const fallbackFiles = new Map();
let databasePromise;

function openDatabase() {
  if (!("indexedDB" in window)) return Promise.resolve(null);
  if (!databasePromise) {
    databasePromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME, { keyPath: "id" });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Could not open local file storage."));
    });
  }
  return databasePromise;
}

function transact(database, mode, run) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    const request = run(transaction.objectStore(STORE_NAME));
    let result;
    request.onsuccess = () => { result = request.result; };
    request.onerror = () => reject(request.error || new Error("Could not access a saved file."));
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error || new Error("Could not access a saved file."));
    transaction.onabort = () => reject(transaction.error || new Error("Local file storage was interrupted."));
  });
}

export async function saveUploadedFile(id, file) {
  const database = await openDatabase();
  if (!database) {
    fallbackFiles.set(id, file);
    return "session";
  }
  await transact(database, "readwrite", (store) => store.put({ id, file }));
  return "persistent";
}

export async function getUploadedFile(id) {
  const database = await openDatabase();
  if (!database) return fallbackFiles.get(id) || null;
  const record = await transact(database, "readonly", (store) => store.get(id));
  return record?.file || null;
}

export async function deleteUploadedFile(id) {
  const database = await openDatabase();
  fallbackFiles.delete(id);
  if (database) await transact(database, "readwrite", (store) => store.delete(id));
}

export async function deleteUploadedFiles(ids) {
  await Promise.all(ids.map((id) => deleteUploadedFile(id)));
}

export async function clearUploadedFiles() {
  const database = await openDatabase();
  fallbackFiles.clear();
  if (database) await transact(database, "readwrite", (store) => store.clear());
}
