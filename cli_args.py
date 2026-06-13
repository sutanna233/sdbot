import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="sdbot - Stable Diffusion agent bot")
    parser.add_argument("--debug", action="store_true", help="Show traceback on errors")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run generation")
    run.add_argument("--mode", choices=["combo", "single", "pair", "sequential", "weighted"])
    run.add_argument("--num", type=int)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--no-dedup", action="store_true")

    dream = subparsers.add_parser("dream", help="Natural language → auto generate")
    dream.add_argument("description", nargs="+", help="Image description")

    tags = subparsers.add_parser("tags", help="Search Danbooru tags")
    tags.add_argument("keyword", help="Tag keyword")
    tags.add_argument("--type", choices=["general", "artist", "character", "copyright"])

    llm = subparsers.add_parser("llm", help="LLM configuration")
    llm.add_argument("action", choices=["test", "status", "set"])
    llm.add_argument("key", nargs="?")
    llm.add_argument("value", nargs="?")

    models = subparsers.add_parser("models", help="List / manage configured LLM models")
    models.add_argument("action", nargs="?", default="list", choices=["list", "status", "switch", "test"])
    models.add_argument("role", nargs="?", choices=["chat", "vision"])
    models.add_argument("model_key", nargs="?")

    subparsers.add_parser("status")

    history = subparsers.add_parser("history")
    history.add_argument("--last", type=int, default=20)
    history.add_argument("--search")

    gallery = subparsers.add_parser("gallery", help="View / manage galleries")
    gallery.add_argument("run_name", nargs="?")
    gallery.add_argument("--list", action="store_true", dest="list_only")
    gallery.add_argument("--regenerate", action="store_true")

    loras = subparsers.add_parser("loras", help="List / manage LoRAs")
    loras.add_argument("action", nargs="?", default="list", choices=["list", "triggers", "set-trigger"])
    loras.add_argument("name", nargs="?")
    loras.add_argument("trigger", nargs="?")
    loras.add_argument("--search")

    artists = subparsers.add_parser("artists")
    artists.add_argument("action", nargs="?", default="list", choices=["list", "count", "gen"])
    artists.add_argument("--search")
    artists.add_argument("--count", action="store_true")

    config = subparsers.add_parser("config")
    config.add_argument("action", nargs="?", default="show", choices=["show", "set", "get"])
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")

    clear = subparsers.add_parser("clear")
    clear.add_argument("target", nargs="?", default="history", choices=["history", "outputs"])

    webui = subparsers.add_parser("webui", help="Start web UI server")
    webui.add_argument("--port", type=int, default=7861)
    webui.add_argument("--host", default="127.0.0.1")

    telegram = subparsers.add_parser("telegram", help="Manage Telegram bot")
    telegram.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
    telegram.add_argument("--token", help="Override bot token")

    update = subparsers.add_parser("update", help="Check or pull updates from GitHub")
    update.add_argument("--check", action="store_true", help="Only check for updates (default)")
    update.add_argument("--apply", action="store_true", help="Apply updates with git pull --ff-only")
    update.add_argument("--deps", action="store_true", help="Run pip install -r requirements.txt after updating")
    update.add_argument("--remote", help="Git remote name, default from config update.remote or origin")
    update.add_argument("--branch", help="Git branch name, default from config update.branch or main")

    tagsite = subparsers.add_parser("tagsite", help="Search character tags from downloadmost.com")
    tagsite.add_argument("names", nargs="+", help="Character name(s)")

    subparsers.add_parser("setup", help="Run interactive first-time setup wizard")

    subparsers.add_parser("shell")

    daemon = subparsers.add_parser("daemon", help="Start all services (WebUI + Telegram) as background daemon")
    daemon.add_argument("--port", type=int, default=7861)
    daemon.add_argument("--host", default="127.0.0.1")

    return parser.parse_args(argv)
