import { GoogleGenAI } from "@google/genai";
import { requestLiveToken } from "./api.js";

export class GeminiLiveController {
  constructor({ onMessage, onAnnotationRequest, onStatusChange } = {}) {
    this.onMessage = onMessage || (() => {});
    this.onAnnotationRequest = onAnnotationRequest || (() => {});
    this.onStatusChange = onStatusChange || (() => {});
    this.session = null;
    this.client = null;
  }

  async connect() {
    this.onStatusChange("connecting");
    const token = await requestLiveToken();
    this.client = new GoogleGenAI({ apiKey: token.access_token });

    this.session = await this.client.live.connect({
      model: token.model,
      callbacks: {
        onopen: () => this.onStatusChange("connected"),
        onmessage: (message) => this.handleMessage(message),
        onerror: () => this.onStatusChange("error"),
        onclose: () => this.onStatusChange("closed")
      }
    });

    return token;
  }

  handleMessage(message) {
    const parts = message?.serverContent?.modelTurn?.parts || [];

    for (const part of parts) {
      if (part.text) {
        this.onMessage({ role: "tutor", text: part.text });
      }

      if (part.functionCall?.name === "add_annotations") {
        this.onAnnotationRequest(part.functionCall.args);
      }
    }
  }

  sendText(text) {
    if (!this.session || !text.trim()) return;

    this.session.sendClientContent({
      turns: [{ role: "user", parts: [{ text }] }],
      turnComplete: true
    });
  }

  sendFrame(frame) {
    if (!this.session) return;

    this.session.sendRealtimeInput({
      media: {
        mimeType: "image/jpeg",
        data: frame.jpeg.split(",")[1]
      }
    });
  }

  close() {
    if (this.session?.close) {
      this.session.close();
    }

    this.session = null;
    this.client = null;
    this.onStatusChange("closed");
  }
}
