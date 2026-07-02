#!/usr/bin/env python3
"""
Chat Agent for Codebase Knowledge Base
Interactive Q&A interface using Bedrock Claude and SQLite knowledge graph
"""

import sqlite3
import json
import sys
from typing import List, Dict, Any, Optional
import boto3
from pathlib import Path


class CodebaseKnowledgeAgent:
    """Chat agent that answers questions about the codebase using the knowledge graph."""

    def __init__(self, db_path: str, model_id: str = "global.anthropic.claude-sonnet-4-6", region: str = "us-east-1"):
        self.db_path = db_path
        self.model_id = model_id
        self.region = region
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)

        # Verify database exists
        if not Path(db_path).exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def search_functions(self, keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for functions by name or summary."""
        cursor = self.conn.execute(
            """
            SELECT name, file_path, classification, domain, summary
            FROM functions
            WHERE name LIKE ? OR summary LIKE ?
            ORDER BY
                CASE WHEN classification = 'business' THEN 1 ELSE 2 END,
                name
            LIMIT ?
            """,
            (f"%{keyword}%", f"%{keyword}%", limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_business_rules(self, keyword: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Get business rules, optionally filtered by keyword."""
        if keyword:
            cursor = self.conn.execute(
                """
                SELECT title, domain, given_clause, when_clause, then_clause,
                       business_impact, source_function, confidence
                FROM business_rules
                WHERE title LIKE ? OR given_clause LIKE ? OR then_clause LIKE ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT title, domain, given_clause, when_clause, then_clause,
                       business_impact, source_function, confidence
                FROM business_rules
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (limit,)
            )
        return [dict(row) for row in cursor.fetchall()]

    def get_flows(self, keyword: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Get business flows, optionally filtered by keyword."""
        if keyword:
            cursor = self.conn.execute(
                """
                SELECT name, domain, description, entry_point, steps_json
                FROM flows
                WHERE name LIKE ? OR description LIKE ?
                LIMIT ?
                """,
                (f"%{keyword}%", f"%{keyword}%", limit)
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT name, domain, description, entry_point, steps_json
                FROM flows
                LIMIT ?
                """,
                (limit,)
            )

        flows = []
        for row in cursor.fetchall():
            flow_dict = dict(row)
            try:
                flow_dict['steps'] = json.loads(flow_dict['steps_json'])
            except:
                flow_dict['steps'] = []
            del flow_dict['steps_json']
            flows.append(flow_dict)
        return flows

    def get_call_graph(self, function_name: str, direction: str = "callees") -> List[Dict[str, Any]]:
        """Get functions called by (callees) or calling (callers) a given function."""
        if direction == "callers":
            query = """
                SELECT f2.name, f2.file_path, f2.classification
                FROM calls c
                JOIN functions f1 ON f1.id = c.callee_id
                JOIN functions f2 ON f2.id = c.caller_id
                WHERE f1.name = ?
                LIMIT 20
            """
        else:  # callees
            query = """
                SELECT f2.name, f2.file_path, f2.classification
                FROM calls c
                JOIN functions f1 ON f1.id = c.caller_id
                JOIN functions f2 ON f2.id = c.callee_id
                WHERE f1.name = ?
                LIMIT 20
            """

        cursor = self.conn.execute(query, (function_name,))
        return [dict(row) for row in cursor.fetchall()]

    def get_domain_summary(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get summary of functions by domain."""
        if domain:
            cursor = self.conn.execute(
                """
                SELECT domain, COUNT(*) as total_functions,
                       SUM(CASE WHEN classification = 'business' THEN 1 ELSE 0 END) as business_functions
                FROM functions
                WHERE domain LIKE ?
                GROUP BY domain
                """,
                (f"%{domain}%",)
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT domain, COUNT(*) as total_functions,
                       SUM(CASE WHEN classification = 'business' THEN 1 ELSE 0 END) as business_functions
                FROM functions
                WHERE domain IS NOT NULL
                GROUP BY domain
                ORDER BY business_functions DESC
                LIMIT 20
                """
            )
        return [dict(row) for row in cursor.fetchall()]

    def _gather_context(self, question: str) -> Dict[str, Any]:
        """Gather relevant context from the knowledge base based on the question."""
        context = {}

        # Extract keywords (simple approach)
        keywords = [w.lower() for w in question.split() if len(w) > 3]

        # Search functions
        for keyword in keywords[:3]:  # Top 3 keywords
            functions = self.search_functions(keyword, limit=3)
            if functions:
                context.setdefault('functions', []).extend(functions)

        # Search rules
        for keyword in keywords[:3]:
            rules = self.get_business_rules(keyword, limit=3)
            if rules:
                context.setdefault('rules', []).extend(rules)

        # Search flows
        for keyword in keywords[:3]:
            flows = self.get_flows(keyword, limit=2)
            if flows:
                context.setdefault('flows', []).extend(flows)

        # Deduplicate
        if 'functions' in context:
            seen = set()
            context['functions'] = [f for f in context['functions'] if f['name'] not in seen and not seen.add(f['name'])]

        if 'rules' in context:
            seen = set()
            context['rules'] = [r for r in context['rules'] if r['title'] not in seen and not seen.add(r['title'])]

        if 'flows' in context:
            seen = set()
            context['flows'] = [f for f in context['flows'] if f['name'] not in seen and not seen.add(f['name'])]

        return context

    def ask(self, question: str) -> str:
        """Answer a question about the codebase using the knowledge base + LLM."""

        # Gather relevant context
        context = self._gather_context(question)

        # Build prompt
        prompt = self._build_prompt(question, context)

        # Call Bedrock
        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": prompt}]
                    }
                ],
                inferenceConfig={
                    "maxTokens": 2048,
                    "temperature": 0.3,
                }
            )

            output = response.get("output", {}).get("message", {})
            content_blocks = output.get("content", [])
            answer = "\n".join([block["text"] for block in content_blocks if "text" in block])
            return answer

        except Exception as e:
            return f"Error calling Bedrock: {str(e)}"

    def _build_prompt(self, question: str, context: Dict[str, Any]) -> str:
        """Build a prompt with the question and relevant context."""

        prompt_parts = [
            "You are a codebase knowledge assistant. Answer the user's question based on the extracted knowledge below.",
            "",
            "# USER QUESTION",
            question,
            "",
            "# KNOWLEDGE BASE CONTEXT",
        ]

        # Add functions
        if context.get('functions'):
            prompt_parts.append("\n## Functions")
            for func in context['functions'][:5]:
                prompt_parts.append(f"\n**{func['name']}** ({func['file_path']})")
                prompt_parts.append(f"Classification: {func['classification']}")
                prompt_parts.append(f"Domain: {func['domain']}")
                if func.get('summary'):
                    prompt_parts.append(f"Summary: {func['summary']}")

        # Add rules
        if context.get('rules'):
            prompt_parts.append("\n## Business Rules")
            for rule in context['rules'][:5]:
                prompt_parts.append(f"\n**{rule['title']}**")
                prompt_parts.append(f"Domain: {rule['domain']}")
                prompt_parts.append(f"GIVEN: {rule['given_clause']}")
                prompt_parts.append(f"WHEN: {rule['when_clause']}")
                prompt_parts.append(f"THEN: {rule['then_clause']}")
                if rule.get('business_impact'):
                    prompt_parts.append(f"Impact: {rule['business_impact'][:200]}...")

        # Add flows
        if context.get('flows'):
            prompt_parts.append("\n## Business Flows")
            for flow in context['flows'][:3]:
                prompt_parts.append(f"\n**{flow['name']}**")
                prompt_parts.append(f"Domain: {flow['domain']}")
                prompt_parts.append(f"Description: {flow['description']}")
                prompt_parts.append(f"Entry Point: {flow['entry_point']}")
                if flow.get('steps'):
                    prompt_parts.append("Steps:")
                    for i, step in enumerate(flow['steps'][:5], 1):
                        if 'description' in step:
                            prompt_parts.append(f"  {i}. {step['description']}")
                        elif 'function_name' in step:
                            prompt_parts.append(f"  {i}. {step['function_name']} - {step.get('summary', '')[:100]}")

        prompt_parts.extend([
            "",
            "# INSTRUCTIONS",
            "- Answer the question directly and concisely",
            "- Reference specific functions, rules, or flows when relevant",
            "- If the context doesn't contain relevant information, say so",
            "- Format your answer in clear markdown",
            "- Include file paths when referencing code",
        ])

        return "\n".join(prompt_parts)

    def close(self):
        """Close database connection."""
        self.conn.close()


def main():
    """CLI interface for the chat agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Chat with your codebase knowledge base")
    parser.add_argument("--db", default="output/code_graph.db", help="Path to knowledge base database")
    parser.add_argument("--question", "-q", help="Ask a single question and exit")
    args = parser.parse_args()

    try:
        agent = CodebaseKnowledgeAgent(args.db)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        print("\nRun 'python main.py extract <repo>' first to build the knowledge base.")
        sys.exit(1)

    print("🧠 Codebase Knowledge Agent")
    print("=" * 60)

    if args.question:
        # Single question mode
        print(f"\n❓ {args.question}\n")
        answer = agent.ask(args.question)
        print(answer)
        agent.close()
        return

    # Interactive mode
    print("Ask me anything about your codebase!")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            question = input("\n❓ You: ").strip()

            if not question:
                continue

            if question.lower() in ['exit', 'quit', 'q']:
                print("\n👋 Goodbye!")
                break

            print("\n🤔 Thinking...\n")
            answer = agent.ask(question)
            print(f"💡 Agent:\n{answer}\n")

        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")

    agent.close()


if __name__ == "__main__":
    main()
