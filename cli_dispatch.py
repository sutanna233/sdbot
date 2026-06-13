def dispatch(tester, args):
    if args.command == "dream":
        tester.cmd_dream(" ".join(args.description))
    elif args.command == "run":
        if getattr(args, "no_dedup", False):
            tester.dedup_enabled = False
        if getattr(args, "mode", None):
            tester.mode = args.mode
        if getattr(args, "num", None):
            tester.cli_args["num"] = args.num
        if getattr(args, "resume", False):
            tester.allow_resample = False
        tester.run()
    elif args.command == "status":
        tester.cmd_status()
    elif args.command == "history":
        tester.cmd_history(last=args.last, search=getattr(args, "search", None))
    elif args.command == "gallery":
        tester.cmd_gallery(
            run_name=getattr(args, "run_name", None),
            list_only=getattr(args, "list_only", False),
            regenerate=getattr(args, "regenerate", False),
        )
    elif args.command == "tags":
        tester.cmd_tags(args.keyword, getattr(args, "type", None))
    elif args.command == "loras":
        action = getattr(args, "action", "list")
        if action == "list":
            tester.cmd_loras(search=getattr(args, "search", None))
        else:
            tester.cmd_loras(action=action, name=getattr(args, "name", None), trigger=getattr(args, "trigger", None))
    elif args.command == "artists":
        if getattr(args, "count", False):
            tester.cmd_artists(count_only=True)
        elif args.action == "gen":
            artists = tester._load_artists()
            generated = set()
            for value in tester.history.get("combos", {}).values():
                for artist in value.get("artists", []):
                    generated.add(artist)
            print(f"{len(generated)} generated / {len(artists)} total")
        else:
            tester.cmd_artists(search=getattr(args, "search", None))
    elif args.command == "llm":
        tester.cmd_llm(args.action, getattr(args, "key", None), getattr(args, "value", None))
    elif args.command == "models":
        tester.cmd_models(
            action=getattr(args, "action", "list"),
            role=getattr(args, "role", None),
            model_key=getattr(args, "model_key", None),
        )
    elif args.command == "config":
        if args.action == "set" and getattr(args, "key", None):
            tester.cmd_config("set", args.key, getattr(args, "value", None))
        elif args.action == "get" and getattr(args, "key", None):
            tester.cmd_config("get", args.key)
        else:
            tester.cmd_config("show")
    elif args.command == "shell":
        tester.cmd_agent()
    elif args.command == "webui":
        tester.cmd_webui(host=getattr(args, "host", "127.0.0.1"), port=int(args.port))
    elif args.command == "telegram":
        tester.cmd_telegram(
            action=args.action,
            token=getattr(args, "token", None),
            block=args.action == "start",
        )
    elif args.command == "update":
        tester.cmd_update(
            apply=getattr(args, "apply", False),
            deps=getattr(args, "deps", False),
            remote=getattr(args, "remote", None),
            branch=getattr(args, "branch", None),
        )
    elif args.command == "tagsite":
        tester.cmd_tagsite(*args.names)
    elif args.command == "clear":
        tester.cmd_clear(args.target)
