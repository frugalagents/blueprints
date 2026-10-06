"""Agent control decisions: is the task done, do I need to ask the user, which tool next?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice, yes_no

TOOLS = {
    "search_web": "Look up current public information on the web",
    "read_file": "Open and read a file in the project",
    "edit_file": "Modify a file in the project",
    "run_tests": "Run the project's test suite",
    "reply_to_user": "Send a message to the user (answer, question, or summary)",
}
QUESTIONS = {
    "complete": yes_no("Has the task already been fully completed and reported to the user?"),
    "clarify": yes_no("Is the task too vague to start without asking the user a question?"),
    "next_tool": choice("Which tool should the agent use next?", TOOLS),
}

# (task, history so far, expected complete, expected clarify, expected next tool or None)
SCENARIOS = [
    (
        "Fix the failing unit test in utils.py",
        "run_tests: 1 failed (test_parse_date). read_file utils.py: found the bug in parse_date. "
        "edit_file utils.py: fixed. run_tests: all 42 passed. reply_to_user: fixed, all tests pass.",
        True, False, None,
    ),
    ("Fix the failing unit test in utils.py", "run_tests: 1 failed (test_parse_date).", False, False, "read_file"),
    ("Book me a table for dinner", "(nothing done yet)", False, True, "reply_to_user"),
    ("What's the latest stable version of Node.js?", "(nothing done yet)", False, False, "search_web"),
    (
        "Add a login button to the homepage",
        "read_file homepage.tsx. edit_file homepage.tsx: added LoginButton. Tests have not been run.",
        False, False, "run_tests",
    ),
    ("Send the report to the team", "(nothing done yet)", False, True, "reply_to_user"),
    ("Summarize README.md", "read_file README.md: read 120 lines, not yet summarized.", False, False, "reply_to_user"),
    (
        "Find out who won the 2022 World Cup",
        "search_web: Argentina won the 2022 FIFA World Cup. reply_to_user: Argentina won, beating France on penalties.",
        True, False, None,
    ),
]

if __name__ == "__main__":
    t = Tally("06_agent_control")
    for task, history, want_done, want_clarify, want_tool in SCENARIOS:
        a = ask(f"Task: {task}\nProgress so far: {history}", QUESTIONS)
        done, clarify, tool = a["complete"]["noul"] > 0.5, a["clarify"]["noul"] > 0.5, a["next_tool"]["choice"]
        marks = [t.add(done == want_done), t.add(clarify == want_clarify)]
        tool_txt = "(n/a)"
        if want_tool:
            marks.append(t.add(tool == want_tool))
            tool_txt = tool
        print(f"[{'/'.join(m.strip() for m in marks)}] complete={done!s:<5} clarify={clarify!s:<5} next={tool_txt:<14} | {task}")
    t.summary()
