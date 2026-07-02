#!/usr/bin/env python3
"""
Simple Flask API server for dashboard chat interface
Serves both the dashboard and handles chat queries
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from chat_agent import CodebaseKnowledgeAgent
from pathlib import Path
import logging

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize agent (singleton)
DB_PATH = "output/code_graph.db"
agent = None

def get_agent():
    """Lazy-load the agent."""
    global agent
    if agent is None:
        if not Path(DB_PATH).exists():
            raise FileNotFoundError(f"Database not found: {DB_PATH}")
        agent = CodebaseKnowledgeAgent(DB_PATH)
        logger.info(f"Initialized agent with database: {DB_PATH}")
    return agent


@app.route('/')
def index():
    """Serve the dashboard."""
    return send_from_directory('output', 'dashboard.html')


@app.route('/<path:path>')
def serve_static(path):
    """Serve static files from output directory."""
    return send_from_directory('output', path)


@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat queries."""
    try:
        data = request.get_json()
        question = data.get('question', '').strip()

        if not question:
            return jsonify({'error': 'No question provided'}), 400

        logger.info(f"Chat query: {question}")

        # Get agent and ask question
        agent = get_agent()
        answer = agent.ask(question)

        return jsonify({
            'question': question,
            'answer': answer,
            'status': 'success'
        })

    except FileNotFoundError as e:
        logger.error(f"Database not found: {e}")
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error processing chat: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def stats():
    """Get knowledge base statistics."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)

        stats = {
            'total_functions': conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0],
            'business_functions': conn.execute("SELECT COUNT(*) FROM functions WHERE classification='business'").fetchone()[0],
            'business_rules': conn.execute("SELECT COUNT(*) FROM business_rules").fetchone()[0],
            'flows': conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0],
            'domains': conn.execute("SELECT COUNT(DISTINCT domain) FROM functions WHERE domain IS NOT NULL").fetchone()[0],
        }

        conn.close()
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'db_path': DB_PATH})


if __name__ == '__main__':
    print("🚀 Starting Codebase Knowledge API Server...")
    print(f"📊 Dashboard: http://localhost:5001")
    print(f"💬 Chat API: http://localhost:5001/api/chat")
    print()
    app.run(host='0.0.0.0', port=5001, debug=False)
