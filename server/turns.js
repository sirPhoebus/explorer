// Grok can emit several function calls in one response. Their arguments are
// complete at response.done, and response.create must wait until every output
// for that response has been sent.
export function createTurnTracker() {
  let calls = [];
  const seen = new Set();

  return {
    onEvent(event) {
      if (event.type === "response.created") {
        calls = [];
        seen.clear();
        return { action: "none" };
      }
      if (event.type === "response.function_call_arguments.done") {
        const callId = event.call_id || event.callId;
        if (!callId || seen.has(callId)) return { action: "none" };
        seen.add(callId);
        calls.push({
          name: event.name,
          callId,
          arguments: event.arguments ?? "{}",
        });
        return { action: "none" };
      }
      if (event.type === "response.done") {
        const pending = calls;
        calls = [];
        seen.clear();
        if (!pending.length) return { action: "none" };
        const status = event.response?.status;
        if (status === "cancelled" || status === "failed") {
          return { action: "drop", calls: pending, status };
        }
        return { action: "execute", calls: pending, status };
      }
      return { action: "none" };
    },
  };
}
