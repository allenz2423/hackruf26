const JSON_HEADERS = {
  "Content-Type": "application/json"
};

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};

  try {
    return JSON.parse(text);
  } catch {
    throw new Error("The server returned an unreadable response.");
  }
}

export async function requestLiveToken() {
  const response = await fetch("/api/live-token", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({})
  });
  const payload = await readJson(response);

  if (!response.ok) {
    throw new Error(payload.detail || payload.error || "Could not start a tutor session.");
  }

  return payload;
}
