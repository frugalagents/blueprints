#!/usr/bin/env python3
"""
Streamlit Chat UI for Codebase Knowledge Base
Run with: streamlit run chat_ui.py
"""

import streamlit as st
from chat_agent import CodebaseKnowledgeAgent
from pathlib import Path
import json

# Page config
st.set_page_config(
    page_title="Codebase Knowledge Chat",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }
    .stat-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        margin-bottom: 1rem;
    }
    .stat-number {
        font-size: 2rem;
        font-weight: 700;
    }
    .stat-label {
        font-size: 0.9rem;
        opacity: 0.9;
    }
    .example-question {
        background: #f0f2f6;
        padding: 0.5rem 1rem;
        border-radius: 5px;
        margin: 0.3rem 0;
        cursor: pointer;
        border-left: 3px solid #667eea;
    }
    .example-question:hover {
        background: #e0e2e6;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_agent(db_path: str):
    """Initialize and cache the agent."""
    return CodebaseKnowledgeAgent(db_path)


@st.cache_data
def get_stats(db_path: str):
    """Get knowledge base statistics."""
    import sqlite3
    conn = sqlite3.connect(db_path)

    stats = {}
    stats['total_functions'] = conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0]
    stats['business_functions'] = conn.execute("SELECT COUNT(*) FROM functions WHERE classification='business'").fetchone()[0]
    stats['business_rules'] = conn.execute("SELECT COUNT(*) FROM business_rules").fetchone()[0]
    stats['flows'] = conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
    stats['domains'] = conn.execute("SELECT COUNT(DISTINCT domain) FROM functions WHERE domain IS NOT NULL").fetchone()[0]

    conn.close()
    return stats


def main():
    # Sidebar
    with st.sidebar:
        st.markdown("### 🧠 Codebase Knowledge Chat")
        st.markdown("---")

        # Database selection
        db_path = st.text_input(
            "Knowledge Base Path",
            value="output/code_graph.db",
            help="Path to the SQLite knowledge graph database"
        )

        if not Path(db_path).exists():
            st.error(f"❌ Database not found: {db_path}")
            st.info("Run `python main.py extract <repo>` first to build the knowledge base.")
            st.stop()

        # Load stats
        stats = get_stats(db_path)

        st.markdown("#### 📊 Knowledge Base Stats")
        st.metric("Total Functions", stats['total_functions'])
        st.metric("Business Functions", stats['business_functions'])
        st.metric("Business Rules", stats['business_rules'])
        st.metric("Business Flows", stats['flows'])
        st.metric("Domains", stats['domains'])

        st.markdown("---")
        st.markdown("#### 💡 Example Questions")

        example_questions = [
            "What happens when an employee is terminated?",
            "What are the promotion eligibility rules?",
            "How does the leave request workflow work?",
            "Which functions handle payroll processing?",
            "What are the COBRA eligibility requirements?",
            "What business domains exist in the codebase?",
        ]

        for q in example_questions:
            if st.button(q, key=f"ex_{q}", use_container_width=True):
                st.session_state.clicked_question = q

        st.markdown("---")
        if st.button("🗑️ Clear Chat History", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    # Main content
    st.markdown('<div class="main-header">🧠 Codebase Knowledge Assistant</div>', unsafe_allow_html=True)
    st.markdown("Ask me anything about your codebase! I'll search through functions, business rules, and flows to answer your questions.")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Initialize agent
    try:
        agent = get_agent(db_path)
    except Exception as e:
        st.error(f"Failed to initialize agent: {e}")
        st.stop()

    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Handle clicked example question
    if "clicked_question" in st.session_state:
        question = st.session_state.clicked_question
        del st.session_state.clicked_question

        # Add to chat history
        st.session_state.messages.append({"role": "user", "content": question})

        # Display user message
        with st.chat_message("user"):
            st.markdown(question)

        # Generate response
        with st.chat_message("assistant"):
            with st.spinner("🤔 Searching knowledge base..."):
                response = agent.ask(question)
            st.markdown(response)

        # Add to history
        st.session_state.messages.append({"role": "assistant", "content": response})
        st.rerun()

    # Chat input
    if prompt := st.chat_input("Ask a question about your codebase..."):
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Generate assistant response
        with st.chat_message("assistant"):
            with st.spinner("🤔 Searching knowledge base..."):
                try:
                    response = agent.ask(prompt)
                    st.markdown(response)

                    # Add assistant response to chat history
                    st.session_state.messages.append({"role": "assistant", "content": response})

                except Exception as e:
                    error_msg = f"❌ Error: {str(e)}"
                    st.error(error_msg)
                    st.session_state.messages.append({"role": "assistant", "content": error_msg})


if __name__ == "__main__":
    main()
