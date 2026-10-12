/**
 * PitchIQ API Frontend Contract
 *
 * Types for all request/response bodies, plus a framework-agnostic helper
 * for streaming the Chat API via Server-Sent Events (SSE) using fetch().
 */

// ---------------------------------------------------------------------------
// Error Types
// ---------------------------------------------------------------------------
export interface ApiErrorDetail {
  code: string;
  message: string;
  request_id: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}

// ---------------------------------------------------------------------------
// Chat Models
// ---------------------------------------------------------------------------
export interface ChatRequest {
  question: string;
  thread_id?: string | null;
}

export interface ToolCallRecord {
  tool: string;
  args: Record<string, any>;
  ok: boolean;
  elapsed_ms: number | null;
}

export interface SourceRecord {
  source: string | null;
  url: string | null;
  date: string | null;
  score: number | null;
}

export interface UsageInfo {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ChatResponse {
  request_id: string;
  thread_id: string;
  answer: string;
  tool_calls: ToolCallRecord[];
  sources: SourceRecord[];
  prediction: Record<string, any> | null;
  usage: UsageInfo;
  elapsed_s: number;
  revisions: number;
}

// ---------------------------------------------------------------------------
// Prediction Models
// ---------------------------------------------------------------------------
export interface PredictRequest {
  home_team: string;
  away_team: string;
  match_date?: string | null;
}

export interface WhyFactor {
  factor: string;
  value: number | null;
  pushes: string;
  strength: number;
}

export interface PredictionContext {
  elo_home: number | null;
  elo_away: number | null;
  home_points_per_game_last5: number | null;
  away_points_per_game_last5: number | null;
  home_rest_days: number | null;
  away_rest_days: number | null;
}

export interface PredictionProbabilities {
  home_win: number;
  draw: number;
  away_win: number;
}

export interface PredictionModel {
  trained_through: string;
  test_log_loss: number;
  note: string;
}

export interface PredictionResponse {
  home: string;
  away: string;
  match_date: string;
  probabilities: PredictionProbabilities;
  most_likely: string;
  why: WhyFactor[];
  context: PredictionContext;
  warnings: string[];
  model: PredictionModel;
}

// ---------------------------------------------------------------------------
// Data Models
// ---------------------------------------------------------------------------
export interface TeamItem {
  name: string;
  aliases: string[];
}

export interface TeamsResponse {
  teams: TeamItem[];
}

export interface FormMatch {
  date: string;
  venue: string;
  opponent: string;
  score: string;
  result: string;
}

export interface FormSummary {
  wins: number;
  draws: number;
  losses: number;
  points: number;
  goals_for: number;
  goals_against: number;
}

export interface FormResponse {
  team: string;
  matches: number;
  form: FormMatch[];
  summary: FormSummary;
}

export interface PlayerAvailability {
  name: string;
  position: string;
  status: string;
  news: string | null;
  chance_next_round: number | null;
}

export interface AvailabilityResponse {
  team: string;
  players: PlayerAvailability[];
}

export interface FixtureItem {
  kickoff_utc: string;
  home: string;
  away: string;
  gameweek: number | null;
  match_date: string;
}

export interface FixturesResponse {
  fixtures: FixtureItem[];
}

export interface ModelCardResponse {
  trained_through: string;
  n_matches: number;
  test_matches_scored: number;
  metrics_on_test: Record<string, any>;
  calibration_ece: number;
  top_feature_importance: Record<string, number>;
}

// ---------------------------------------------------------------------------
// SSE Event Payloads
// ---------------------------------------------------------------------------
export interface SseStartEvent {
  request_id: string;
  thread_id: string;
}

export interface SseToolCallEvent {
  id: string;
  name: string;
  args: Record<string, any>;
}

export interface SseToolResultEvent {
  id: string;
  name: string;
  ok: boolean;
  elapsed_ms: number | null;
  summary: string;
}

export interface SseRevisionEvent {
  detail: string;
}

export interface SseAnswerEvent {
  text: string;
  sources: SourceRecord[];
  prediction: Record<string, any> | null;
  usage: UsageInfo;
  elapsed_s: number;
}

export interface SseErrorEvent {
  code: string;
  message: string;
}

export interface SseDoneEvent {}

// Handlers interface for the streaming client
export interface StreamHandlers {
  onStart?: (data: SseStartEvent) => void;
  onToolCall?: (data: SseToolCallEvent) => void;
  onToolResult?: (data: SseToolResultEvent) => void;
  onRevision?: (data: SseRevisionEvent) => void;
  onAnswer?: (data: SseAnswerEvent) => void;
  onError?: (data: SseErrorEvent) => void;
  onDone?: () => void;
  onNetworkError?: (error: Error) => void;
}

// ---------------------------------------------------------------------------
// Stream Client Helper
// ---------------------------------------------------------------------------
/**
 * Framework-free helper to stream chat events from the backend.
 * Uses fetch() and ReadableStream, correctly re-assembling chunked SSE frames.
 *
 * @param url Full URL to the `/api/v1/chat/stream` endpoint.
 * @param question The user's question.
 * @param threadId The thread ID (pass null for a new conversation).
 * @param handlers Callback functions for each event type.
 * @param signal Optional AbortSignal to cancel the request.
 * @param apiKey Optional API key if the backend requires it.
 */
export async function streamChat(
  url: string,
  question: string,
  threadId: string | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  apiKey?: string
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (apiKey) headers["X-API-Key"] = apiKey;

  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ question, thread_id: threadId }),
      signal,
    });

    if (!response.ok) {
      // Could be 429, 422, etc. Parse as JSON if possible.
      try {
        const errJson = (await response.json()) as ApiErrorResponse;
        if (handlers.onError) handlers.onError(errJson.error);
      } catch {
        if (handlers.onError) {
          handlers.onError({
            code: "http_error",
            message: `HTTP ${response.status}`,
            request_id: "",
          });
        }
      }
      if (handlers.onDone) handlers.onDone();
      return;
    }

    if (!response.body) throw new Error("No response body.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Split buffer by double newline (SSE frame separator)
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");

        // Parse lines within the frame
        const lines = frame.split("\n");
        let eventName = "message";
        let dataStr = "";

        for (const line of lines) {
          if (line.startsWith(":")) continue; // Comment (e.g. ping)
          if (line.startsWith("event: ")) {
            eventName = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            dataStr += line.slice(6);
          }
        }

        if (!dataStr) continue;

        let dataObj: any;
        try {
          dataObj = JSON.parse(dataStr);
        } catch (e) {
          console.warn("Failed to parse SSE JSON data:", dataStr);
          continue;
        }

        switch (eventName) {
          case "start":
            if (handlers.onStart) handlers.onStart(dataObj as SseStartEvent);
            break;
          case "tool_call":
            if (handlers.onToolCall) handlers.onToolCall(dataObj as SseToolCallEvent);
            break;
          case "tool_result":
            if (handlers.onToolResult) handlers.onToolResult(dataObj as SseToolResultEvent);
            break;
          case "revision":
            if (handlers.onRevision) handlers.onRevision(dataObj as SseRevisionEvent);
            break;
          case "answer":
            if (handlers.onAnswer) handlers.onAnswer(dataObj as SseAnswerEvent);
            break;
          case "error":
            if (handlers.onError) handlers.onError(dataObj as SseErrorEvent);
            break;
          case "done":
            if (handlers.onDone) handlers.onDone();
            // Server closes after done, but we can exit early.
            return;
        }
      }
    }
    // Stream ended gracefully
    if (handlers.onDone) handlers.onDone();

  } catch (error: any) {
    if (error.name === "AbortError") {
      // User cancelled
      return;
    }
    if (handlers.onNetworkError) {
      handlers.onNetworkError(error);
    } else if (handlers.onError) {
      handlers.onError({
        code: "network_error",
        message: error.message || "A network error occurred",
        request_id: "",
      });
    }
    if (handlers.onDone) handlers.onDone();
  }
}

// ---------------------------------------------------------------------------
// React Hook Example (Commented)
// ---------------------------------------------------------------------------
/*
import { useState, useRef, useCallback } from "react";

export function useChat(url: string) {
  const [messages, setMessages] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback((question: string) => {
    setIsLoading(true);
    abortControllerRef.current = new AbortController();

    streamChat(
      url,
      question,
      threadId,
      {
        onStart: (data) => setThreadId(data.thread_id),
        onToolCall: (data) => console.log("Agent is calling tool:", data.name),
        onAnswer: (data) => {
          setMessages((prev) => [...prev, { type: "agent", text: data.text }]);
        },
        onError: (err) => console.error("API Error:", err.message),
        onDone: () => setIsLoading(false),
      },
      abortControllerRef.current.signal
    );
  }, [url, threadId]);

  const cancelRequest = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setIsLoading(false);
  }, []);

  return { messages, isLoading, sendMessage, cancelRequest };
}
*/
