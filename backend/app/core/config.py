"""
统一配置中心。

合并两个项目的配置，按 pydantic-settings 管理。
从 .env 文件、环境变量读取所有参数。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# 指向 backend/.env（相对本文件定位，避免受启动 CWD 影响）。
# 存在则本地开发使用；Render 等部署环境没有该文件，会自动回退到进程环境变量。
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        自定义配置来源优先级（从高到低）：
            init 参数 > .env 文件 > 进程/系统环境变量 > secrets 文件

        默认 pydantic-settings 中环境变量优先于 .env，会导致本机
        Windows 用户级 OPENAI_BASE_URL/OPENAI_API_KEY（指向第三方代理）
        覆盖掉本项目 .env。这里把 dotenv 提到 env 之前修正该行为：
          - 本地：存在 .env → .env 优先（不再被系统环境变量覆盖）
          - Render：无 .env → dotenv 源为空，自动回退到平台注入的环境变量
        """
        return (
            init_settings,
            dotenv_settings,
            env_settings,
            file_secret_settings,
        )

    # ---- 应用 ----
    app_name: str = "Data Agent 智能问数与经营分析平台"
    api_prefix: str = "/api"
    cors_origins: str = "https://ai-gateway-1qp6.onrender.com,http://localhost:3000"

    # ---- DeepSeek / OpenAI 兼容接口 ----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_fallback_mock: bool = True

    # ---- MySQL（仅 SQL 分析模式使用）----
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "analyzer"
    mysql_password: str = ""
    mysql_database: str = "ai_analyzer"
    # 托管数据库（TiDB Cloud / Aiven / PlanetScale 等）通常强制 TLS
    mysql_ssl_enabled: bool = False
    mysql_ssl_ca: str = ""                   # 留空则使用 certifi 内置根证书

    # ---- 前端静态资源 ----
    # 指向前端构建产物目录；存在时由后端直接托管前端，实现单服务部署
    static_dir: str = "static"

    # ---- MCP Server（SQL 执行 & Schema 发现）----
    mcp_server_enabled: bool = True
    mcp_server_command: str = "npx"
    mcp_server_args: str = "-y @anthropic/mcp-server-mysql"
    # 传给 MCP server 的环境变量（JSON 格式或留空用 mysql_* 自动拼接）
    mcp_server_env: str = ""
    mcp_connect_timeout: float = 30.0
    mcp_call_timeout: float = 30.0
    mcp_base_url: str = "https://ai-gateway-1qp6.onrender.com"
    # 是否把 MCP 辅助工具（mysql_query / describe_table / read_file）注册进编排层
    # 默认关闭：核心是 SQL/Log 两个工具，MCP 为可选辅助（见 tools/mcp_tool.py）
    mcp_tools_enabled: bool = False

    # ---- Filesystem MCP Server（文件上传/读取）----
    fs_mcp_server_enabled: bool = True
    fs_mcp_server_command: str = "npx"
    fs_mcp_server_args: str = "-y @anthropic/mcp-server-filesystem"
    fs_mcp_connect_timeout: float = 15.0
    fs_mcp_call_timeout: float = 15.0
    fs_sandbox_root: str = "uploads"         # 沙箱根目录（相对于 gateway/）
    fs_session_ttl_minutes: int = 30         # 会话目录保留时间

    # ---- Web 搜索 & Fetch（外部检索增强）----
    web_search_enabled: bool = True
    web_search_max_results: int = 3
    web_search_timeout: float = 10.0
    fetch_mcp_server_enabled: bool = True
    fetch_mcp_server_command: str = "npx"
    fetch_mcp_server_args: str = "-y @anthropic/mcp-server-fetch"
    fetch_mcp_connect_timeout: float = 15.0
    fetch_mcp_call_timeout: float = 15.0

    # ---- 日志诊断 ----
    max_log_chars: int = 12000
    max_upload_bytes: int = 2 * 1024 * 1024  # 2MB

    # ---- 计算属性 ----
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


if __name__ == "__main__":
    # 安全的配置自检：仅展示非敏感字段，绝不打印 API Key 明文。
    s = get_settings()
    key = s.openai_api_key.strip()
    print("app_name    :", s.app_name)
    print("env_file    :", _ENV_FILE, f"(exists={_ENV_FILE.exists()})")
    print("base_url    :", s.openai_base_url)
    print("model       :", s.openai_model)
    print("api_key_set :", bool(key), f"(len={len(key)})")  # 只显示是否配置及长度
