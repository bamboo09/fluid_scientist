/**
 * V5 Pipeline State Machine — frontend state management
 * Displays real pipeline stages, capability gaps, and submission gating.
 *
 * This module polls the backend for pipeline state and renders:
 *   - Stage progression (completed / active / pending)
 *   - Capability gap analysis with color-coded status
 *   - Clarification questions for multi-round dialog
 *   - Validation errors blocking submission
 *   - Change summaries for incremental modifications
 *
 * It complements v5-pipeline.js (which handles the one-shot run/modify flow)
 * by providing continuous state polling and interactive clarification UI.
 */

const V5StateMachine = {
    currentView: null,
    pollInterval: null,
    pollIntervalMs: 2000,

    /**
     * Initialise the state machine with a default empty view and render it.
     * Safe to call even if the target DOM elements are not yet present.
     */
    init() {
        this.currentView = {
            session_id: '',
            study_id: '',
            case_id: '',
            case_ir_version: 0,
            current_stage: '正在提取研究条件',
            stages: [],
            capabilities: [],
            clarifications: [],
            change_summary: null,
            validation_errors: [],
            can_submit: false,
            evidence_valid: true,
        };
        this.render();
    },

    /**
     * Fetch the current pipeline state from the API and re-render.
     * @param {string} sessionId - The pipeline session identifier.
     */
    async fetchState(sessionId) {
        if (!sessionId) {
            console.warn('[V5StateMachine] fetchState called without sessionId');
            return;
        }
        try {
            const response = await fetch(`/api/v5/pipeline/${sessionId}/state`);
            if (!response.ok) {
                throw new Error(`Failed to fetch state (${response.status})`);
            }
            const data = await response.json();
            this.currentView = data;
            this.render();
        } catch (error) {
            console.error('[V5StateMachine] State fetch error:', error);
        }
    },

    /**
     * Start polling the backend for pipeline state at a fixed interval.
     * @param {string} sessionId - The pipeline session identifier.
     * @param {number} [intervalMs=2000] - Polling interval in milliseconds.
     */
    startPolling(sessionId, intervalMs = 2000) {
        this.stopPolling();
        this.pollIntervalMs = intervalMs;
        // Fetch immediately, then on interval
        this.fetchState(sessionId);
        this.pollInterval = setInterval(() => this.fetchState(sessionId), intervalMs);
    },

    /**
     * Stop the polling timer if one is active.
     */
    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    },

    /**
     * Re-render all UI sections from the current view.
     */
    render() {
        this.renderStages();
        this.renderCapabilities();
        this.renderClarifications();
        this.renderSubmitButton();
        this.renderValidationErrors();
    },

    /**
     * Render the pipeline stage progression list.
     * Each stage shows an icon (checkmark, spinner, or circle) and optional details.
     */
    renderStages() {
        const container = document.getElementById('v5-pipeline-stages');
        if (!container) return;
        if (!this.currentView.stages || this.currentView.stages.length === 0) {
            container.innerHTML = `<div class="v5-stage-item active">
                <span class="v5-stage-icon">⟳</span>
                <span class="v5-stage-name">${this.currentView.current_stage || '正在提取研究条件'}</span>
            </div>`;
            return;
        }
        container.innerHTML = this.currentView.stages.map(stage => {
            const cls = stage.completed ? 'completed' : (stage.in_progress ? 'active' : 'pending');
            const icon = stage.completed ? '✓' : (stage.in_progress ? '⟳' : '○');
            return `<div class="v5-stage-item ${cls}">
                <span class="v5-stage-icon">${icon}</span>
                <span class="v5-stage-name">${stage.stage}</span>
                ${stage.details ? `<span class="v5-stage-details">${stage.details}</span>` : ''}
            </div>`;
        }).join('');
    },

    /**
     * Render the capability gap analysis list.
     * Each capability shows its description and a color-coded status badge.
     */
    renderCapabilities() {
        const container = document.getElementById('v5-capabilities');
        if (!container) return;
        if (!this.currentView.capabilities || this.currentView.capabilities.length === 0) {
            container.innerHTML = '';
            return;
        }
        const statusColors = {
            '已支持': '#4caf50',
            '可组合': '#2196f3',
            '待扩展': '#ff9800',
            '需要新物理': '#f44336',
            '需要确认': '#9c27b0',
            '环境受阻': '#795548',
        };
        container.innerHTML = this.currentView.capabilities.map(cap => {
            const color = statusColors[cap.status] || '#757575';
            return `<div class="v5-capability-item" style="border-left: 3px solid ${color}">
                <span class="v5-capability-desc">${cap.description}</span>
                <span class="v5-capability-status" style="color: ${color}">${cap.status}</span>
            </div>`;
        }).join('');
    },

    /**
     * Render clarification questions for multi-round dialog.
     * Each question provides radio options, with the recommended option marked.
     */
    renderClarifications() {
        const container = document.getElementById('v5-clarifications');
        if (!container) return;
        if (!this.currentView.clarifications || this.currentView.clarifications.length === 0) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = this.currentView.clarifications.map(q => {
            const options = (q.options || []).map(opt => {
                const recommended = opt === q.recommended_option ? ' (推荐)' : '';
                return `<label><input type="radio" name="clarify_${q.question_id}" value="${opt}"> ${opt}${recommended}</label>`;
            }).join('<br>');
            return `<div class="v5-clarification-item">
                <p class="v5-clarification-question">${q.question}</p>
                <div class="v5-clarification-options">${options}</div>
                ${q.impact ? `<p class="v5-clarification-impact">${q.impact}</p>` : ''}
                <button onclick="V5StateMachine.submitClarification('${q.question_id}')">确认</button>
            </div>`;
        }).join('');
    },

    /**
     * Submit a user's clarification answer to the backend, then refresh state.
     * @param {string} questionId - The clarification question identifier.
     */
    async submitClarification(questionId) {
        const selected = document.querySelector(`input[name="clarify_${questionId}"]:checked`);
        if (!selected) {
            alert('请选择一个选项');
            return;
        }
        const sessionId = this.currentView.session_id;
        if (!sessionId) {
            console.error('[V5StateMachine] No session_id available for clarification');
            return;
        }
        try {
            const response = await fetch(`/api/v5/pipeline/${sessionId}/clarify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question_id: questionId, answer: selected.value }),
            });
            if (!response.ok) {
                throw new Error(`Failed to submit clarification (${response.status})`);
            }
            this.fetchState(sessionId);
        } catch (error) {
            console.error('[V5StateMachine] Clarification submit error:', error);
        }
    },

    /**
     * Update the submit button's disabled state and tooltip based on can_submit.
     */
    renderSubmitButton() {
        const btn = document.getElementById('v5-submit-btn');
        if (!btn) return;
        btn.disabled = !this.currentView.can_submit;
        btn.title = this.currentView.can_submit ? '提交任务' : 'Case 尚未通过验证';
    },

    /**
     * Render validation errors that are blocking submission.
     */
    renderValidationErrors() {
        const container = document.getElementById('v5-validation-errors');
        if (!container) return;
        if (!this.currentView.validation_errors || this.currentView.validation_errors.length === 0) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = this.currentView.validation_errors.map(err =>
            `<div class="v5-validation-error-item">⚠ ${err}</div>`
        ).join('');
    },

    /**
     * Submit a parameter modification to the backend and display the change summary.
     * @param {string} sessionId - The pipeline session identifier.
     * @param {string} modification - Natural-language modification description.
     */
    async submitModification(sessionId, modification) {
        if (!sessionId) {
            console.error('[V5StateMachine] No sessionId for modification');
            return;
        }
        try {
            const response = await fetch(`/api/v5/pipeline/${sessionId}/modify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ modification }),
            });
            if (!response.ok) {
                throw new Error(`Failed to submit modification (${response.status})`);
            }
            const data = await response.json();
            this.renderChangeSummary(data.change_summary);
            this.fetchState(sessionId);
        } catch (error) {
            console.error('[V5StateMachine] Modification submit error:', error);
        }
    },

    /**
     * Render a change summary showing what was modified and whether revalidation is needed.
     * @param {object} summary - The ChangeSummary object from the backend.
     */
    renderChangeSummary(summary) {
        if (!summary) return;
        const container = document.getElementById('v5-change-summary');
        if (!container) return;
        const paths = (summary.changed_paths || []).map(p => `<li>${p}</li>`).join('');
        container.innerHTML = `
            <div class="v5-change-summary">
                <h4>变更摘要</h4>
                <p>${summary.description || ''}</p>
                <ul>${paths}</ul>
                ${summary.requires_revalidation ? '<p class="warning">需要重新验证</p>' : ''}
            </div>`;
    },
};

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => V5StateMachine.init());
} else {
    V5StateMachine.init();
}

window.V5StateMachine = V5StateMachine;
