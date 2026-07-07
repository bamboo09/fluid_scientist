const terminalStates = new Set(["succeeded", "failed", "cancelled"]);

export async function cancelPlanningBeforeReset({
  operationId,
  operationActive,
  cancelOperation,
  resumePolling,
  clearSession,
  setRequestActive = () => {},
  setActionDisabled = () => {},
  onError = () => {},
}) {
  if (!operationActive) {
    clearSession();
    return { cleared: true };
  }
  setRequestActive(true);
  setActionDisabled(true);
  try {
    await cancelOperation(operationId);
  } catch (error) {
    onError(error);
    resumePolling(operationId);
    return { cleared: false, error };
  } finally {
    setRequestActive(false);
    setActionDisabled(false);
  }
  clearSession();
  return { cleared: true };
}

export class OperationPoller {
  constructor({
    fetchOperation,
    onStatus = () => {},
    onMissing = () => {},
    onNetwork = () => {},
    setTimeout = globalThis.setTimeout.bind(globalThis),
    clearTimeout = globalThis.clearTimeout.bind(globalThis),
    createAbortController = () => new AbortController(),
    initialDelay = 1000,
    maxDelay = 10000,
    maxNetworkFailures = 5,
  }) {
    this.fetchOperation = fetchOperation;
    this.onStatus = onStatus;
    this.onMissing = onMissing;
    this.onNetwork = onNetwork;
    this.setTimeout = setTimeout;
    this.clearTimeout = clearTimeout;
    this.createAbortController = createAbortController;
    this.initialDelay = initialDelay;
    this.maxDelay = maxDelay;
    this.maxNetworkFailures = maxNetworkFailures;
    this.generation = 0;
    this.timer = null;
    this.controller = null;
    this.operationId = "";
    this.delay = initialDelay;
    this.networkFailures = 0;
    this.paused = false;
  }

  stop() {
    this.generation += 1;
    if (this.timer !== null) this.clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
  }

  start(operationId, { immediate = true } = {}) {
    this.stop();
    this.operationId = operationId;
    this.delay = this.initialDelay;
    this.networkFailures = 0;
    this.paused = false;
    const generation = this.generation;
    if (!immediate) {
      this.#schedule(generation);
      return Promise.resolve();
    }
    return this.#poll(generation);
  }

  resume() {
    if (!this.operationId) return Promise.resolve();
    return this.start(this.operationId);
  }

  #schedule(generation) {
    if (generation !== this.generation) return;
    if (this.timer !== null) this.clearTimeout(this.timer);
    this.timer = this.setTimeout(() => {
      this.timer = null;
      void this.#poll(generation);
    }, this.delay);
    this.delay = Math.min(Math.round(this.delay * 1.4), this.maxDelay);
  }

  async #poll(generation) {
    if (generation !== this.generation || !this.operationId) return;
    this.controller?.abort();
    const controller = this.createAbortController();
    this.controller = controller;
    const operationId = this.operationId;
    try {
      const operation = await this.fetchOperation(operationId, { signal: controller.signal });
      if (generation !== this.generation) return;
      this.controller = null;
      await this.onStatus(operation, {
        isCurrent: () => (
          generation === this.generation && operationId === this.operationId
        ),
      });
      if (generation !== this.generation) return;
      this.networkFailures = 0;
      this.paused = false;
      if (terminalStates.has(operation.state)) {
        this.stop();
      } else {
        this.#schedule(generation);
      }
    } catch (error) {
      if (error?.name === "AbortError" || generation !== this.generation) return;
      this.controller = null;
      if (error?.status === 404) {
        this.stop();
        await this.onMissing(operationId);
        return;
      }
      this.networkFailures += 1;
      this.paused = this.networkFailures >= this.maxNetworkFailures;
      await this.onNetwork({
        error,
        failures: this.networkFailures,
        paused: this.paused,
        operationId,
      });
      if (!this.paused && generation === this.generation) this.#schedule(generation);
    }
  }
}

export function createResultLoader({ fetchPlan, onPlan }) {
  const completed = new Set();
  const pending = new Map();
  const applying = new Map();
  return async function loadResult(resultRef, { shouldApply = () => true } = {}) {
    if (!resultRef || completed.has(resultRef)) return;
    if (!pending.has(resultRef)) {
      const request = fetchPlan(resultRef).catch((error) => {
        pending.delete(resultRef);
        throw error;
      });
      pending.set(resultRef, request);
    }
    const plan = await pending.get(resultRef);
    if (completed.has(resultRef) || !shouldApply()) return;
    if (!applying.has(resultRef)) {
      const application = (async () => {
        try {
          await onPlan(plan);
          completed.add(resultRef);
        } finally {
          applying.delete(resultRef);
        }
      })();
      applying.set(resultRef, application);
    }
    await applying.get(resultRef);
  };
}
