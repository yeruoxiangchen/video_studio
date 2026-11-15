"""Pure configuration routing; actual analysis is checked against real services."""

from camcat.config import Settings
from camcat.services.providers import QwenChatClient, QwenVisualAnalysisClient


def test_visual_analysis_can_retain_its_real_video_gateway_with_a_separate_planner():
    settings = Settings(
        llm_base_url="http://planner.local:18974",
        llm_model="Qwen/Qwen3.6-27B",
        llm_api_key="planner-key",
        visual_analysis_base_url="http://visual.local:18972",
        visual_analysis_model="Qwen/Qwen3.5-9B",
        visual_analysis_api_key="visual-key",
    )
    planner, visual = QwenChatClient(settings), QwenVisualAnalysisClient(settings)
    assert planner.model == "Qwen/Qwen3.6-27B"
    assert planner.base_url == "http://planner.local:18974"
    assert visual.model == "Qwen/Qwen3.5-9B"
    assert visual.base_url == "http://visual.local:18972"
    assert visual.client.headers["Authorization"] == "Bearer visual-key"
    planner.client.close()
    visual.client.close()


def test_existing_single_gateway_configuration_keeps_its_analysis_route():
    settings = Settings(llm_base_url="http://existing.local:18969", llm_api_key="existing")
    visual = QwenVisualAnalysisClient(settings)
    assert visual.base_url == settings.llm_base_url and visual.model == settings.llm_model
    visual.client.close()
