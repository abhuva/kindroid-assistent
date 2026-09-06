"use strict";

(function exposeRelayApiClient(globalScope) {
  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  class RelayApiClient {
    constructor({ csrf, fetchImpl = globalScope.fetch.bind(globalScope), createId = () => globalScope.crypto.randomUUID() }) {
      this.csrf = csrf;
      this.fetchImpl = fetchImpl;
      this.createId = createId;
      this.readControllers = new Map();
      this.pendingSubmissions = new Map();
      this.etags = new Map();
    }

    async request(path, options = {}) {
      const headers = { Accept: "application/json", ...(options.headers || {}) };
      if (options.method && options.method !== "GET") {
        headers["Content-Type"] = "application/json";
        headers["X-Relay-CSRF"] = this.csrf;
      }
      const controller = new AbortController();
      const abort = () => controller.abort();
      options.signal?.addEventListener("abort", abort, { once: true });
      if (options.signal?.aborted) abort();
      let timedOut = false;
      const timeout = setTimeout(() => { timedOut = true; abort(); }, 10000);
      try {
        const response = await this.fetchImpl(path, {
          cache: "no-store",
          ...options,
          headers,
          signal: controller.signal,
        });
        if (response.status === 304) return null;
        if (response.headers?.get("etag")) this.etags.set(path, response.headers.get("etag"));
        const payload = await response.json();
        if (!response.ok) {
          throw new ApiError(
            payload.message || payload.detail || `Request failed (${response.status})`,
            response.status,
          );
        }
        return payload;
      } catch (error) {
        if (timedOut && !options.signal?.aborted) {
          throw new DOMException("Dashboard request timed out after 10 seconds.", "TimeoutError");
        }
        throw error;
      } finally {
        clearTimeout(timeout);
        options.signal?.removeEventListener("abort", abort);
      }
    }

    async replaceRead(name, operation) {
      this.readControllers.get(name)?.abort();
      const controller = new AbortController();
      this.readControllers.set(name, controller);
      try {
        const result = await operation(controller.signal);
        if (controller.signal.aborted) throw new DOMException("Obsolete read", "AbortError");
        return result;
      } finally {
        if (this.readControllers.get(name) === controller) {
          this.readControllers.delete(name);
        }
      }
    }

    abortReads() {
      for (const controller of this.readControllers.values()) controller.abort();
      this.readControllers.clear();
    }

    async submit(path, body = {}) {
      const action = `${path}:${JSON.stringify(body)}`;
      let pending = this.pendingSubmissions.get(action);
      if (!pending) {
        pending = { idempotencyKey: this.createId(), body };
        this.pendingSubmissions.set(action, pending);
      }
      if (pending.promise) return pending.promise;
      pending.promise = (async () => { try {
        const result = await this.request(path, {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: pending.idempotencyKey,
            ...pending.body,
          }),
        });
        this.pendingSubmissions.delete(action);
        return result;
      } catch (error) {
        if (error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status)) {
          this.pendingSubmissions.delete(action);
        }
        throw error;
      } finally { pending.promise = null; } })();
      return pending.promise;
    }
  }

  globalScope.RelayApiClient = RelayApiClient;
  globalScope.RelayApiError = ApiError;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { ApiError, RelayApiClient };
  }
})(globalThis);
