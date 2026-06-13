from .config import ClearTool, ConfigTool, LLMTool
from .characters import CharacterConfirmTool, CharacterResolveTool
from .dream import DreamTool, GenerationInfoTool
from .files import FileDeleteTool, FileFindTool, FileListTool, FileReadTool, FileWriteTool
from .loras import LorasTool
from .memory import MemoryForgetTool, MemoryGetTool, MemoryListTool, MemorySetTool
from .models import AddProviderTool, ModelsTool
from .registry import ToolRegistry
from .run import RunTool
from .session import SessionListTool, SessionNewTool, SessionRenameTool, SessionSwitchTool
from .skills import SkillCreateTool, SkillListTool, SkillLoadTool
from .status import ArtistsTool, GalleryTool, HistoryTool, StatusTool, WebUITool
from .tags import TagsTool
from .tagsite import TagSiteTool
from .telegram import TelegramTool
from .update import UpdateTool
from .vision import CritiqueTool
from .web_fetch import WebFetchTool
from agent.schemas import TOOL_SCHEMAS


def build_tool_registry(host):
    registry = ToolRegistry()
    registry.register("dream", DreamTool(host))
    registry.register("models", ModelsTool(host))
    registry.register("add_provider", AddProviderTool(host))
    registry.register("tagsite", TagSiteTool(host))
    registry.register("character_resolve", CharacterResolveTool(host))
    registry.register("character_confirm", CharacterConfirmTool(host))
    registry.register("loras", LorasTool(host))
    registry.register("telegram", TelegramTool(host))
    registry.register("status", StatusTool(host))
    registry.register("history", HistoryTool(host))
    registry.register("artists", ArtistsTool(host))
    registry.register("gallery", GalleryTool(host))
    registry.register("webui", WebUITool(host))
    registry.register("config_get", ConfigTool(host, "get"))
    registry.register("config_set", ConfigTool(host, "set"))
    registry.register("clear", ClearTool(host))
    registry.register("llm_test", LLMTool(host, "test"))
    registry.register("llm_status", LLMTool(host, "status"))
    registry.register("session_new", SessionNewTool(host))
    registry.register("session_list", SessionListTool(host))
    registry.register("session_switch", SessionSwitchTool(host))
    registry.register("session_rename", SessionRenameTool(host))
    registry.register("file_read", FileReadTool(host))
    registry.register("file_write", FileWriteTool(host))
    registry.register("file_list", FileListTool(host))
    registry.register("file_delete", FileDeleteTool(host))
    registry.register("file_find", FileFindTool(host))
    registry.register("generation_info", GenerationInfoTool(host))
    registry.register("web_fetch", WebFetchTool(host))
    registry.register("critique", CritiqueTool(host))
    registry.register("run", RunTool(host))
    registry.register("tags", TagsTool(host))
    registry.register("update", UpdateTool(host))
    registry.register("skill_list", SkillListTool(host))
    registry.register("skill_load", SkillLoadTool(host))
    registry.register("skill_create", SkillCreateTool(host))
    registry.register("memory_set", MemorySetTool(host))
    registry.register("memory_get", MemoryGetTool(host))
    registry.register("memory_forget", MemoryForgetTool(host))
    registry.register("memory_list", MemoryListTool(host))
    _attach_tool_schemas(registry)
    return registry


def _attach_tool_schemas(registry):
    for tool_name, schema in TOOL_SCHEMAS.items():
        if tool_name != "chat" and tool_name in registry.names():
            registry.register(tool_name, registry.get(tool_name), schema=schema)
