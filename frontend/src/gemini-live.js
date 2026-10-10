import { requestLiveToken, calculateExpression } from "./api.js";

export class GeminiLiveController {
  constructor({ classId = null, onMessage, onAnnotationRequest, onStatusChange } = {}) {
    this.classId = classId;
    this.onMessage = onMessage || (() => {});
    this.onAnnotationRequest = onAnnotationRequest || (() => {});
    this.onStatusChange = onStatusChange || (() => {});
    this.session = null;
    this.client = null;
  }

  async connect() {
    this.onStatusChange("connecting");
    const [token, { GoogleGenAI }] = await Promise.all([
      requestLiveToken(this.classId),
      import("@google/genai")
    ]);
    this.client = new GoogleGenAI({ apiKey: token.access_token });

    this.session = await this.client.live.connect({
      model: token.model,
      callbacks: {
        onopen: () => this.onStatusChange("connected"),
        onmessage: (message) => this.handleMessage(message),
        onerror: (error) => {
          console.error("Gemini Live session error:", error);
          this.onStatusChange("error");
        },
        onclose: () => this.onStatusChange("closed")
      }
    });

    return token;
  }

  async handleMessage(message) {
    const parts = message?.serverContent?.modelTurn?.parts || [];

    for (const part of parts) {
      if (part.text) {
        this.onMessage({ role: "tutor", text: part.text });
      }
    }

    // Collect all tool/function calls across message structure
    const functionCalls = [...(message?.toolCall?.functionCalls || [])];
    for (const part of parts) {
      if (part.functionCall) {
        functionCalls.push(part.functionCall);
      }
    }

    if (functionCalls.length > 0) {
      await this.handleFunctionCalls(functionCalls);
    }
  }

  async handleFunctionCalls(functionCalls) {
    const functionResponses = [];

    for (const call of functionCalls) {
      const { id, name, args } = call;

      if (name === "calculate_expression") {
        try {
          const expression = args?.expression;
          const res = await calculateExpression(expression);
          functionResponses.push({
            id,
            name,
            response: { result: res.result }
          });
        } catch (error) {
          functionResponses.push({
            id,
            name,
            response: {
              error: {
                code: error.code || "calculator_error",
                message: error.message || "Could not calculate expression."
              }
            }
          });
        }
      } else if (name === "add_annotations") {
        try {
          this.onAnnotationRequest(args);
          functionResponses.push({
            id,
            name,
            response: { status: "rendered" }
          });
        } catch (error) {
          functionResponses.push({
            id,
            name,
            response: {
              error: {
                code: "annotation_error",
                message: error.message || "Could not render annotation."
              }
            }
          });
        }
      } else {
        functionResponses.push({
          id,
          name,
          response: {
            error: {
              code: "unsupported_tool",
              message: `Tool ${name} is not supported.`
            }
          }
        });
      }
    }

    if (functionResponses.length > 0 && this.session) {
      try {
        this.session.sendToolResponse({ functionResponses });
      } catch (err) {
        console.warn("Failed to send tool response to Gemini Live:", err);
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
