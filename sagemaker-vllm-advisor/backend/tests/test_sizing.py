from backend.calculators.sizing import (
    analyze,
    calculate_kv_bytes_per_token,
    calculate_weight_memory_gb,
)
from backend.domain.models import AssessmentInput, Precision


def sample_input(**overrides):
    values = {
        "model_id": "meta-llama/Llama-3.1-8B-Instruct",
        "parameter_billions": 8.03,
        "layers": 32,
        "hidden_size": 4096,
        "attention_heads": 32,
        "kv_heads": 8,
        "precision": Precision.BF16,
        "p50_input_tokens": 1500,
        "p95_input_tokens": 6000,
        "p50_output_tokens": 250,
        "p95_output_tokens": 800,
        "peak_requests_per_second": 3,
        "target_ttft_seconds": 1.5,
        "max_model_length": 8192,
        "region": "us-east-1",
        "availability": "multi-az",
        "shared_prompt_prefix": True,
    }
    values.update(overrides)
    return AssessmentInput(**values)


def test_weight_memory_matches_frontend_fixture():
    assert calculate_weight_memory_gb(8.03, Precision.BF16) == 16.7024


def test_kv_cache_accounts_for_grouped_query_attention():
    assert calculate_kv_bytes_per_token(sample_input()) == 131072


def test_candidate_and_vllm_settings_are_generated():
    result = analyze(sample_input())
    assert result.p95_sequence_tokens == 6800
    assert result.recommendation
    settings = {
        item.parameter: item.value for item in result.recommendation.vllm_settings
    }
    assert settings["max_model_len"] == "8192"
    assert settings["enable_prefix_caching"] == "true"
    assert result.recommendation.estimated_replicas >= 2


def test_large_model_requires_tensor_parallelism():
    result = analyze(
        sample_input(
            model_id="large-model",
            parameter_billions=70,
            layers=80,
            hidden_size=8192,
            attention_heads=64,
            kv_heads=8,
        )
    )
    assert result.candidates
    assert all(item.tensor_parallel_size > 1 for item in result.candidates)
