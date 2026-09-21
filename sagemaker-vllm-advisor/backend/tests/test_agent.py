import pytest

from backend.agent.bedrock_agent import BedrockAdvisorAgent
from backend.domain.models import AgentQuestion


class FakeBedrockClient:
    def __init__(self):
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "stopReason": "tool_use",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "tool-1",
                                    "name": "resolve_huggingface_model",
                                    "input": {"model_id": "x"},
                                }
                            }
                        ],
                    }
                },
            }
        return {
            "stopReason": "end_turn",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Please provide a complete model ID."}],
                }
            },
        }


@pytest.mark.asyncio
async def test_agent_executes_validated_tool_loop():
    agent = BedrockAdvisorAgent(
        client=FakeBedrockClient(), model_id="test-model"
    )
    answer = await agent.ask(AgentQuestion(question="Resolve this model"))
    assert answer.model_id == "test-model"
    assert answer.answer == "Please provide a complete model ID."
    assert any(step.phase == "Act" for step in answer.trace)
