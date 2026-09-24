#!/usr/bin/env python3
"""
Dashboard App - Web interface with chat sidebar for the orchestrator.

This Flask app provides:
- Main dashboard page with sidebar chat
- /api/chat endpoint that runs queries through DashboardAgent
- /api/live endpoint for live data polling
- Widget data APIs for dynamic content

Usage:
    python scripts/dashboard_v2/app.py
    python scripts/dashboard_v2/app.py --port 8080
"""

import json
import logging
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, send_from_directory, render_template_string
from flask_cors import CORS

from src.agents.dashboard_agent import DashboardAgent
from src.dashboard import DashboardConfig, create_pre_event_dashboard, create_post_event_dashboard

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# APP CONFIGURATION
# =============================================================================

app = Flask(__name__, 
    static_folder="static",
    template_folder="templates"
)
CORS(app)

# Global state
dashboard_agent = None
conversation_history = []  # Simple in-memory history


def get_dashboard_agent():
    """Lazy-load dashboard agent."""
    global dashboard_agent
    if dashboard_agent is None:
        dashboard_agent = DashboardAgent()
    return dashboard_agent


# =============================================================================
# ROUTES - MAIN PAGES
# =============================================================================

@app.route("/")
def index():
    """Serve the main dashboard page."""
    return render_template_string(DASHBOARD_HTML)


# =============================================================================
# ROUTES - CHAT API
# =============================================================================

@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Process a chat message and return dashboard configuration.
    
    Request body:
        {
            "query": "Show pre-event analysis for NFP",
            "conversation_id": "optional-session-id"
        }
        
    Response:
        {
            "dashboard_config": {...},
            "answer_text": "...",
            "run_id": "...",
            "metadata": {...}
        }
    """
    try:
        data = request.get_json()
        query = data.get("query", "").strip()
        
        if not query:
            return jsonify({"error": "Query is required"}), 400
        
        logger.info(f"Chat request: {query[:100]}...")
        
        # Add to conversation history
        conversation_history.append({
            "role": "user",
            "content": query,
            "timestamp": datetime.now().isoformat(),
        })
        
        # Check for preset dashboard commands
        query_lower = query.lower()
        
        if query_lower.startswith("/pre_event") or "pre-event" in query_lower:
            # Quick preset - skip full orchestrator
            event_type = _extract_event_from_query(query)
            dashboard = create_pre_event_dashboard(event_type=event_type)
            response = {
                "dashboard_config": dashboard.to_dict(),
                "answer_text": f"Loading {event_type} pre-event analysis dashboard...",
                "run_id": None,
                "metadata": {"preset": "pre_event", "event_type": event_type},
            }
        
        elif query_lower.startswith("/post_event") or "post-event" in query_lower:
            # Quick preset - skip full orchestrator
            event_type = _extract_event_from_query(query)
            dashboard = create_post_event_dashboard(event_type=event_type)
            response = {
                "dashboard_config": dashboard.to_dict(),
                "answer_text": f"Loading {event_type} post-event analysis dashboard...",
                "run_id": None,
                "metadata": {"preset": "post_event", "event_type": event_type},
            }
        
        else:
            # Full orchestrator pipeline
            agent = get_dashboard_agent()
            response = agent.run(query=query)
        
        # Add assistant response to history
        conversation_history.append({
            "role": "assistant",
            "content": response.get("answer_text", ""),
            "timestamp": datetime.now().isoformat(),
            "run_id": response.get("run_id"),
        })
        
        return jsonify(response)
    
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        return jsonify({
            "error": str(e),
            "dashboard_config": None,
            "answer_text": f"Error: {str(e)}",
        }), 500


@app.route("/api/chat/history", methods=["GET"])
def get_chat_history():
    """Get conversation history."""
    return jsonify({
        "history": conversation_history[-50:],  # Last 50 messages
        "count": len(conversation_history),
    })


@app.route("/api/chat/clear", methods=["POST"])
def clear_chat():
    """Clear conversation history."""
    global conversation_history
    conversation_history = []
    return jsonify({"status": "cleared"})


# =============================================================================
# ROUTES - WIDGET DATA APIs
# =============================================================================

@app.route("/api/live")
def get_live_data():
    """
    Get live market data for live_stats_panel widget.
    
    This is a placeholder - integrate with your existing live data server.
    """
    # TODO: Integrate with existing LiveDataServer from live_unified_dashboard.py
    return jsonify({
        "ty_price": None,
        "timestamp": datetime.now().isoformat(),
        "status": "not_connected",
        "message": "Live data server not configured",
    })


@app.route("/api/widget/<widget_type>")
def get_widget_data(widget_type: str):
    """
    Get data for a specific widget type.
    
    Query params vary by widget type.
    """
    try:
        params = request.args.to_dict()
        
        # Placeholder - implement data fetching per widget type
        data = {
            "widget_type": widget_type,
            "params": params,
            "data": None,
            "message": f"Widget data API for {widget_type} not yet implemented",
        }
        
        return jsonify(data)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _extract_event_from_query(query: str) -> str:
    """Extract event type from query string."""
    query_lower = query.lower()
    
    if "nfp" in query_lower or "payroll" in query_lower:
        return "NFP"
    elif "cpi" in query_lower or "inflation" in query_lower:
        return "CPI"
    elif "ppi" in query_lower:
        return "PPI"
    elif "jolts" in query_lower:
        return "JOLTS"
    elif "ism" in query_lower or "pmi" in query_lower:
        return "ISM"
    elif "fomc" in query_lower or "fed" in query_lower:
        return "FOMC"
    
    return "NFP"  # Default


# =============================================================================
# HTML TEMPLATE - DASHBOARD WITH CHAT SIDEBAR
# =============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Market Data Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --border-color: #30363d;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-orange: #d29922;
            --accent-red: #f85149;
            --accent-purple: #a371f7;
            --sidebar-width: 380px;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            display: flex;
            overflow: hidden;
        }
        
        /* ===== SIDEBAR ===== */
        .sidebar {
            width: var(--sidebar-width);
            background: var(--bg-secondary);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            height: 100vh;
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border-color);
        }
        
        .sidebar-header h1 {
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .sidebar-header h1::before {
            content: "📊";
        }
        
        /* Chat messages area */
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .message {
            max-width: 90%;
            padding: 10px 14px;
            border-radius: 12px;
            font-size: 0.9rem;
            line-height: 1.4;
        }
        
        .message.user {
            background: var(--accent-blue);
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }
        
        .message.assistant {
            background: var(--bg-tertiary);
            color: var(--text-primary);
            align-self: flex-start;
            border-bottom-left-radius: 4px;
        }
        
        .message.system {
            background: transparent;
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-align: center;
            align-self: center;
        }
        
        .message.error {
            background: rgba(248, 81, 73, 0.2);
            border: 1px solid var(--accent-red);
            color: var(--accent-red);
        }
        
        .message-time {
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }
        
        /* Chat input area */
        .chat-input-area {
            padding: 16px;
            border-top: 1px solid var(--border-color);
            background: var(--bg-secondary);
        }
        
        .chat-input-wrapper {
            display: flex;
            gap: 8px;
        }
        
        .chat-input {
            flex: 1;
            padding: 12px 16px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.2s;
        }
        
        .chat-input:focus {
            border-color: var(--accent-blue);
        }
        
        .chat-input::placeholder {
            color: var(--text-secondary);
        }
        
        .send-btn {
            padding: 12px 20px;
            background: var(--accent-blue);
            border: none;
            border-radius: 8px;
            color: white;
            font-weight: 500;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        
        .send-btn:hover {
            opacity: 0.9;
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        /* Quick actions */
        .quick-actions {
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }
        
        .quick-btn {
            padding: 6px 12px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            color: var(--text-secondary);
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .quick-btn:hover {
            border-color: var(--accent-blue);
            color: var(--accent-blue);
        }
        
        /* ===== MAIN CONTENT ===== */
        .main-content {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
        }
        
        .dashboard-title {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 20px;
            color: var(--text-primary);
        }
        
        .widget-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }
        
        .widget {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            min-height: 200px;
        }
        
        .widget.full-width {
            grid-column: span 2;
        }
        
        .widget-header {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--text-primary);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .widget-content {
            color: var(--text-secondary);
        }
        
        /* Empty state */
        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 400px;
            color: var(--text-secondary);
            text-align: center;
        }
        
        .empty-state svg {
            width: 80px;
            height: 80px;
            margin-bottom: 20px;
            opacity: 0.5;
        }
        
        .empty-state h2 {
            font-size: 1.25rem;
            margin-bottom: 8px;
            color: var(--text-primary);
        }
        
        .empty-state p {
            max-width: 400px;
            line-height: 1.5;
        }
        
        /* Loading spinner */
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid var(--border-color);
            border-top-color: var(--accent-blue);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* Text answer widget */
        .text-answer {
            line-height: 1.6;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <!-- Sidebar with chat -->
    <aside class="sidebar">
        <header class="sidebar-header">
            <h1>Market Data Agent</h1>
        </header>
        
        <div class="chat-messages" id="chatMessages">
            <div class="message system">
                Ask me about market data, economic events, or request analysis dashboards.
            </div>
        </div>
        
        <div class="chat-input-area">
            <div class="quick-actions">
                <button class="quick-btn" onclick="sendQuickQuery('/pre_event NFP')">Pre-Event NFP</button>
                <button class="quick-btn" onclick="sendQuickQuery('/post_event NFP')">Post-Event NFP</button>
                <button class="quick-btn" onclick="sendQuickQuery('Show TY chart')">TY Chart</button>
            </div>
            <div class="chat-input-wrapper">
                <input type="text" class="chat-input" id="chatInput" 
                       placeholder="Ask about market data..." 
                       onkeypress="handleKeyPress(event)">
                <button class="send-btn" id="sendBtn" onclick="sendMessage()">Send</button>
            </div>
        </div>
    </aside>
    
    <!-- Main dashboard area -->
    <main class="main-content">
        <div id="dashboardContent">
            <div class="empty-state">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" 
                          d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                <h2>Start a conversation</h2>
                <p>Ask about market data, economic events, or request pre/post event analysis dashboards. Your dashboard will appear here.</p>
            </div>
        </div>
    </main>
    
    <script>
        // ===== STATE =====
        let isLoading = false;
        let currentDashboard = null;
        
        // ===== CHAT FUNCTIONS =====
        function handleKeyPress(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }
        
        function sendQuickQuery(query) {
            document.getElementById('chatInput').value = query;
            sendMessage();
        }
        
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const query = input.value.trim();
            
            if (!query || isLoading) return;
            
            // Add user message to chat
            addMessage(query, 'user');
            input.value = '';
            
            // Show loading state
            isLoading = true;
            document.getElementById('sendBtn').disabled = true;
            const loadingMsg = addMessage('<span class="loading"></span> Processing...', 'assistant');
            
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query }),
                });
                
                const data = await response.json();
                
                // Remove loading message
                loadingMsg.remove();
                
                if (data.error && !data.dashboard_config) {
                    addMessage(data.error, 'error');
                } else {
                    // Add assistant response
                    if (data.answer_text) {
                        addMessage(data.answer_text, 'assistant');
                    }
                    
                    // Render dashboard
                    if (data.dashboard_config) {
                        renderDashboard(data.dashboard_config);
                    }
                }
            } catch (error) {
                loadingMsg.remove();
                addMessage(`Error: ${error.message}`, 'error');
            } finally {
                isLoading = false;
                document.getElementById('sendBtn').disabled = false;
            }
        }
        
        function addMessage(content, type) {
            const messagesDiv = document.getElementById('chatMessages');
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${type}`;
            msgDiv.innerHTML = content;
            
            // Add timestamp for user/assistant messages
            if (type === 'user' || type === 'assistant') {
                const timeDiv = document.createElement('div');
                timeDiv.className = 'message-time';
                timeDiv.textContent = new Date().toLocaleTimeString();
                msgDiv.appendChild(timeDiv);
            }
            
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            
            return msgDiv;
        }
        
        // ===== DASHBOARD RENDERING =====
        function renderDashboard(config) {
            currentDashboard = config;
            const container = document.getElementById('dashboardContent');
            
            // Build HTML
            let html = '';
            
            if (config.title) {
                html += `<h1 class="dashboard-title">${escapeHtml(config.title)}</h1>`;
            }
            
            html += '<div class="widget-grid">';
            
            for (const widget of config.widgets || []) {
                const isFullWidth = widget.position?.colspan === 2;
                html += `
                    <div class="widget ${isFullWidth ? 'full-width' : ''}" id="widget-${widget.id}">
                        <div class="widget-header">
                            ${escapeHtml(widget.title || widget.type.replace(/_/g, ' ').toUpperCase())}
                        </div>
                        <div class="widget-content" id="widget-content-${widget.id}">
                            ${renderWidgetContent(widget)}
                        </div>
                    </div>
                `;
            }
            
            html += '</div>';
            container.innerHTML = html;
            
            // Initialize Plotly charts if any
            initializeCharts(config.widgets);
        }
        
        function renderWidgetContent(widget) {
            const type = widget.type;
            const config = widget.config || {};
            
            switch (type) {
                case 'text_answer':
                    return `<div class="text-answer">${escapeHtml(config.content || '')}</div>`;
                
                case 'stats_card':
                    return renderStatsCard(config);
                
                case 'event_info_card':
                    return renderEventInfoCard(config);
                
                case 'data_table':
                    return '<p>Data table will load here...</p>';
                
                case 'candlestick_chart':
                case 'price_paths_chart':
                case 'distribution_chart':
                case 'box_plot':
                case 'scatter_plot':
                    // Plotly charts - render placeholder, will be initialized
                    if (config.image_path) {
                        return `<img src="${escapeHtml(config.image_path)}" style="max-width: 100%;" alt="Chart">`;
                    }
                    return `<div id="chart-${widget.id}" style="width: 100%; height: 400px;"></div>`;
                
                case 'live_stats_panel':
                    return renderLivePanel(config);
                
                default:
                    return `<p>Widget type: ${type}</p><pre>${JSON.stringify(config, null, 2)}</pre>`;
            }
        }
        
        function renderStatsCard(config) {
            const stats = config.statistics || {};
            return `
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;">
                    <div><small>Mean</small><br><strong>${stats.mean?.toFixed(2) || '--'}</strong></div>
                    <div><small>Median</small><br><strong>${stats.median?.toFixed(2) || '--'}</strong></div>
                    <div><small>Min</small><br><strong>${stats.min?.toFixed(2) || '--'}</strong></div>
                    <div><small>Max</small><br><strong>${stats.max?.toFixed(2) || '--'}</strong></div>
                </div>
            `;
        }
        
        function renderEventInfoCard(config) {
            return `
                <div>
                    <p><strong>Event:</strong> ${config.event_type || 'N/A'}</p>
                    <p><strong>Date:</strong> ${config.event_date || 'Latest'}</p>
                </div>
            `;
        }
        
        function renderLivePanel(config) {
            return `
                <div style="text-align: center; padding: 20px;">
                    <p style="color: var(--accent-orange);">Live data panel</p>
                    <p>Event: ${config.event_type || 'NFP'}</p>
                    <p>Mode: ${config.mode || 'pre_event'}</p>
                </div>
            `;
        }
        
        function initializeCharts(widgets) {
            // Initialize Plotly charts for chart widgets
            for (const widget of widgets || []) {
                if (widget.type.endsWith('_chart') || widget.type === 'box_plot') {
                    const chartDiv = document.getElementById(`chart-${widget.id}`);
                    if (chartDiv && !widget.config?.image_path) {
                        // Create empty placeholder chart
                        Plotly.newPlot(chartDiv, [], {
                            paper_bgcolor: '#161b22',
                            plot_bgcolor: '#161b22',
                            font: { color: '#8b949e' },
                            margin: { t: 20, r: 20, b: 40, l: 50 },
                        });
                    }
                }
            }
        }
        
        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>
"""


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Dashboard App with Chat Sidebar")
    parser.add_argument("--port", type=int, default=8765, help="Port to run on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  Dashboard App Starting")
    print(f"  URL: http://localhost:{args.port}")
    print(f"{'='*60}\n")
    
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
