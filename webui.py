import os
import sys
import json
import time
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory, abort, redirect, url_for

from generate_artists import SDArtistTester


SCRIPT_DIR = Path(__file__).parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
STATIC_DIR = SCRIPT_DIR / "static"
OUTPUTS_DIR = SCRIPT_DIR / "outputs"


def create_app(tester=None):
    app = Flask(__name__,
                template_folder=str(TEMPLATES_DIR),
                static_folder=str(STATIC_DIR))
    app.config["tester"] = tester or SDArtistTester()
    app.config["outputs_dir"] = SCRIPT_DIR / app.config["tester"].config["output"]["base_dir"]
    app.config["outputs_dir"].mkdir(parents=True, exist_ok=True)
    app.config["log_dir"] = getattr(app.config["tester"], "_log_dir", SCRIPT_DIR / "logs")
    return app


class JobQueue:
    def __init__(self, tester):
        self.tester = tester
        self.queue = queue.Queue()
        self.jobs = {}
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def submit(self, job_type, params, run_dir_hint=None):
        job_id = uuid.uuid4().hex[:10]
        job = {
            "id": job_id,
            "type": job_type,
            "params": params,
            "status": "queued",
            "progress": 0,
            "current": 0,
            "total": params.get("num", 1),
            "step": "等待执行",
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "logs": [],
        }
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job_id

    def _log(self, job, msg):
        job["logs"].append({"ts": datetime.now().isoformat(), "msg": msg})

    def _worker(self):
        while True:
            job_id = self.queue.get()
            with self.lock:
                job = self.jobs.get(job_id)
            if not job:
                continue
            job["status"] = "running"
            job["started_at"] = datetime.now().isoformat()
            self._log(job, f"开始任务: {job['type']}")
            try:
                if job["type"] == "dream":
                    self._run_dream(job)
                elif job["type"] == "run":
                    self._run_run(job)
                else:
                    raise RuntimeError(f"未知任务: {job['type']}")
                job["status"] = "completed"
                job["step"] = "完成"
                job["progress"] = 100
                job["finished_at"] = datetime.now().isoformat()
                self._log(job, "任务完成")
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = datetime.now().isoformat()
                self._log(job, f"失败: {e}")

    def _run_dream(self, job):
        p = job["params"]
        job["step"] = "分析意图"
        job["progress"] = 10
        self._log(job, "[1/4] LLM 正在分析意图...")
        keywords = p.get("keywords") or []
        characters = p.get("characters") or []
        copyrights = p.get("copyrights") or []
        style = p.get("style", "detailed")
        all_tags = p.get("all_tags") or keywords
        prompt = p.get("prompt", "")
        if not prompt:
            prompt = self.tester._llm().write_detailed_prompt(
                p.get("description", ""), keywords, all_tags, style, characters, copyrights
            )
        job["step"] = "组装 prompt"
        job["progress"] = 30
        self._log(job, f"Prompt: {prompt[:80]}...")
        rec_artists = []
        local_artists = self.tester._load_artists()
        for kw in keywords:
            rec_artists.extend(self.tester.danbooru.search_artists(kw, local_artists))
        rec_artists = list(dict.fromkeys(rec_artists))
        loras = p.get("loras") or []
        if loras:
            loras = self.tester._resolve_loras(loras)
        job["step"] = "生成图片"
        job["progress"] = 40
        num = int(p.get("num", 1))
        old_template = self.tester.config["prompt"]["template"]
        old_neg = self.tester.config["prompt"]["negative"]
        old_w = self.tester.config["generation"]["width"]
        old_h = self.tester.config["generation"]["height"]
        old_steps = self.tester.config["generation"]["steps"]
        old_cfg = self.tester.config["generation"]["cfg_scale"]
        old_sampler = self.tester.config["generation"]["sampler"]
        try:
            self.tester.config["prompt"]["template"] = prompt
            if p.get("negative_prompt"):
                self.tester.config["prompt"]["negative"] = p["negative_prompt"]
            self.tester.config["generation"]["width"] = int(p.get("width", 1024))
            self.tester.config["generation"]["height"] = int(p.get("height", 1536))
            self.tester.config["generation"]["steps"] = int(p.get("steps", 28))
            self.tester.config["generation"]["cfg_scale"] = float(p.get("cfg_scale", 5))
            self.tester.config["generation"]["sampler"] = p.get("sampler", "Euler")
            self.tester.cli_args["num"] = num
            self.tester.mode = p.get("mode", "combo")
            mc = self.tester.config.get("mode_config", {}).get(self.tester.mode, {})
            self.tester.mode_min = mc.get("min_artists") or self.tester.config["testing"].get("min_artists", 3)
            self.tester.mode_max = mc.get("max_artists") or self.tester.config["testing"].get("max_artists", 8)
            if rec_artists:
                self.tester.config["artists"]["list_file"] = self.tester._write_temp_artists(rec_artists)
            actual_count = len(rec_artists) if rec_artists else len(self.tester._load_artists())
            if actual_count < self.tester.mode_min:
                if self.tester.mode != "single" and actual_count >= 2:
                    self.tester.mode_min = actual_count
                    self.tester.mode_max = min(self.tester.mode_max, actual_count)
                else:
                    self.tester.mode = "single"
                    self.tester.mode_min = 1
                    self.tester.mode_max = 1
            self._log(job, "[4/4] 开始生成...")
            self.tester.run(loras=self.tester._resolve_loras(loras) if loras else None)
            job["step"] = "完成"
            job["current"] = num
        finally:
            self.tester.config["prompt"]["template"] = old_template
            self.tester.config["prompt"]["negative"] = old_neg
            self.tester.config["generation"]["width"] = old_w
            self.tester.config["generation"]["height"] = old_h
            self.tester.config["generation"]["steps"] = old_steps
            self.tester.config["generation"]["cfg_scale"] = old_cfg
            self.tester.config["generation"]["sampler"] = old_sampler
        job["result"] = {"batch_name": self.tester.last_run_dir.name if self.tester.last_run_dir else None}

    def _run_run(self, job):
        p = job["params"]
        num = int(p.get("num", 5))
        job["total"] = num
        self.tester.cli_args["num"] = num
        if p.get("mode"):
            self.tester.mode = p["mode"]
            mc = self.tester.config.get("mode_config", {}).get(self.tester.mode, {})
            self.tester.mode_min = mc.get("min_artists") or self.tester.config["testing"].get("min_artists", 3)
            self.tester.mode_max = mc.get("max_artists") or self.tester.config["testing"].get("max_artists", 8)
        loras = p.get("loras") or []
        if loras:
            loras = self.tester._resolve_loras(loras)
        job["step"] = "生成中"
        job["progress"] = 30
        self.tester.run(loras=loras or None)
        job["step"] = "完成"
        job["current"] = num
        job["result"] = {"batch_name": self.tester.last_run_dir.name if self.tester.last_run_dir else None}

    def get(self, job_id):
        with self.lock:
            return dict(self.jobs.get(job_id, {}))

    def list_jobs(self):
        with self.lock:
            return [dict(j) for j in self.jobs.values()]


def register_routes(app, job_queue):
    outputs_dir = app.config["outputs_dir"]

    @app.route("/")
    def index():
        return render_template("master.html")

    @app.route("/batch/<name>")
    def batch(name):
        return render_template("batch.html", name=name)

    @app.route("/generate")
    def generate():
        return render_template("generate.html")

    @app.route("/loras")
    def loras():
        return render_template("loras.html")

    @app.route("/artists")
    def artists():
        return render_template("artists.html")

    @app.route("/config")
    def config():
        return render_template("config.html")

    @app.route("/logs")
    def logs():
        return render_template("logs.html")

    @app.route("/skills")
    def skills():
        return render_template("skills.html")

    @app.route("/stats")
    def stats():
        return render_template("stats.html")

    @app.route("/chat")
    def chat():
        return render_template("chat.html")

    @app.route("/outputs/<path:relpath>")
    def serve_output(relpath):
        return send_from_directory(str(outputs_dir), relpath)

    @app.route("/api/batches")
    def api_batches():
        search = (request.args.get("search") or "").lower().strip()
        mode = (request.args.get("mode") or "").strip()
        sort = request.args.get("sort", "newest")
        if not outputs_dir.exists():
            return jsonify({"batches": [], "total": 0})
        dirs = [d for d in outputs_dir.iterdir() if d.is_dir()]
        batches = []
        for d in dirs:
            log_path = d / "generation_log.json"
            if not log_path.exists():
                continue
            try:
                log = json.load(open(log_path, "r", encoding="utf-8"))
            except Exception:
                continue
            entry = {
                "name": d.name,
                "timestamp": log.get("timestamp", ""),
                "mode": log.get("mode", "?"),
                "elapsed": log.get("elapsed_seconds", 0),
                "success": log.get("success_count", 0),
                "total": log.get("total_images", 0),
                "results": log.get("results", []),
            }
            entry["has_gallery"] = (d / "index.html").exists()
            thumbs_dir = d / "_thumbs"
            if thumbs_dir.exists():
                thumbs = sorted(thumbs_dir.glob("*.png"))
                entry["thumb"] = thumbs[0].name if thumbs else None
            else:
                entry["thumb"] = None
            if search:
                hit = search in d.name.lower()
                if not hit:
                    for r in entry["results"]:
                        if search in (r.get("prompt", "")).lower():
                            hit = True
                            break
                        if any(search in a.lower() for a in r.get("artists", [])):
                            hit = True
                            break
                if not hit:
                    continue
            if mode and mode != "all" and entry["mode"] != mode:
                continue
            batches.append(entry)
        if sort == "newest":
            batches.sort(key=lambda x: x["timestamp"], reverse=True)
        elif sort == "oldest":
            batches.sort(key=lambda x: x["timestamp"])
        elif sort == "elapsed":
            batches.sort(key=lambda x: x["elapsed"], reverse=True)
        elif sort == "success":
            batches.sort(key=lambda x: x["success"] / max(x["total"], 1), reverse=True)
        return jsonify({"batches": batches, "total": len(batches)})

    @app.route("/api/batches/<name>")
    def api_batch(name):
        target = outputs_dir / name
        if not target.exists() or not target.is_dir():
            abort(404)
        log_path = target / "generation_log.json"
        if not log_path.exists():
            return jsonify({"name": name, "results": []})
        log = json.load(open(log_path, "r", encoding="utf-8"))
        thumbs_dir = target / "_thumbs"
        for r in log.get("results", []):
            if r.get("info") and r["info"].endswith(".png"):
                p = Path(r["info"])
                thumb = thumbs_dir / p.name
                r["thumb"] = f"/outputs/{name}/_thumbs/{p.name}" if thumb.exists() else f"/outputs/{name}/{p.name}"
                r["image_url"] = f"/outputs/{name}/{p.name}"
        return jsonify({
            "name": name,
            "timestamp": log.get("timestamp"),
            "mode": log.get("mode"),
            "elapsed": log.get("elapsed_seconds"),
            "results": log.get("results", []),
            "log_url": f"/outputs/{name}/generation_log.json",
            "config_url": f"/outputs/{name}/config.yaml",
        })

    @app.route("/api/batches/<name>/regenerate", methods=["POST"])
    def api_regenerate(name):
        target = outputs_dir / name
        if not target.exists():
            return jsonify({"error": "not found"}), 404
        app.config["tester"]._generate_gallery(target)
        return jsonify({"ok": True})

    @app.route("/api/batches/<name>", methods=["DELETE"])
    def api_delete_batch(name):
        target = outputs_dir / name
        if not target.exists() or not target.is_dir():
            return jsonify({"error": "not found"}), 404
        import shutil
        shutil.rmtree(target)
        return jsonify({"ok": True})

    @app.route("/api/generate/dream", methods=["POST"])
    def api_dream():
        p = request.json or {}
        try:
            tester = app.config["tester"]
            intent = tester._llm().analyze_intent(p.get("description", ""))
            keywords = intent.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [keywords]
            chars = intent.get("characters") or []
            cps = intent.get("copyrights") or []
            style = intent.get("style", "detailed")
            all_tags = tester._llm().generate_tags(
                p.get("description", ""), keywords, chars, cps, p.get("max_tags")
            )
            if not isinstance(all_tags, list) or not all_tags:
                all_tags = keywords
            if p.get("max_tags"):
                all_tags = all_tags[: int(p["max_tags"])]
            prompt = tester._llm().write_detailed_prompt(
                p.get("description", ""), keywords, all_tags, style, chars, cps
            )
            job_id = job_queue.submit("dream", {
                "description": p.get("description", ""),
                "keywords": keywords,
                "characters": chars,
                "copyrights": cps,
                "style": style,
                "all_tags": all_tags,
                "prompt": prompt,
                "num": int(p.get("num", 1)),
                "mode": p.get("mode", "combo"),
                "width": int(p.get("width", 1024)),
                "height": int(p.get("height", 1536)),
                "steps": int(p.get("steps", 28)),
                "cfg_scale": float(p.get("cfg_scale", 5)),
                "sampler": p.get("sampler", "Euler"),
                "negative_prompt": p.get("negative_prompt"),
                "loras": p.get("loras") or [],
            })
            return jsonify({"ok": True, "job_id": job_id, "preview": {
                "keywords": keywords, "characters": chars, "copyrights": cps,
                "style": style, "tags_count": len(all_tags), "prompt": prompt,
            }})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/generate/run", methods=["POST"])
    def api_run():
        p = request.json or {}
        job_id = job_queue.submit("run", {
            "num": int(p.get("num", 5)),
            "mode": p.get("mode", "combo"),
            "loras": p.get("loras") or [],
        })
        return jsonify({"ok": True, "job_id": job_id})

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        p = request.json or {}
        msg = (p.get("message") or "").strip()
        if not msg:
            return jsonify({"error": "消息不能为空"}), 400
        try:
            result = app.config["tester"]._agent_process(msg, source="webui")
            chain = app.config["tester"]._extract_chain(result)
            return jsonify({
                "reply": result.get("reply", ""),
                "chain": chain,
                "needs_confirm": bool(chain),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/chat/confirm", methods=["POST"])
    def api_chat_confirm():
        p = request.json or {}
        chain = p.get("chain", [])
        action = p.get("action", "execute")
        if action == "cancel":
            return jsonify({"status": "cancelled"})
        if action == "edit":
            new_msg = (p.get("new_message") or "").strip()
            if not new_msg:
                return jsonify({"error": "需要新描述"}), 400
            try:
                result = app.config["tester"]._agent_process(new_msg, source="webui")
                new_chain = app.config["tester"]._extract_chain(result)
                return jsonify({
                    "status": "ok",
                    "chain": new_chain,
                    "reply": result.get("reply", ""),
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        results = []
        for step in chain:
            a = step.get("action")
            params = step.get("params", {})
            if a == "dream":
                job_id = job_queue.submit("dream", params)
                results.append({"action": a, "job_id": job_id, "status": "queued"})
            elif a == "run":
                job_id = job_queue.submit("run", params)
                results.append({"action": a, "job_id": job_id, "status": "queued"})
            else:
                try:
                    app.config["tester"]._execute_action(a, params)
                    results.append({"action": a, "status": "ok"})
                except Exception as e:
                    results.append({"action": a, "status": "failed", "error": str(e)})
        return jsonify({"status": "ok", "results": results})

    @app.route("/api/chat/history")
    def api_chat_history():
        sid, session = app.config["tester"]._session_current()
        return jsonify({
            "session_id": sid,
            "session_name": session.get("name", ""),
            "messages": session.get("conversation", []),
        })

    @app.route("/api/chat/reset", methods=["POST"])
    def api_chat_reset():
        sid = app.config["tester"]._session_create()
        return jsonify({"session_id": sid})

    @app.route("/api/jobs")
    def api_jobs():
        return jsonify({"jobs": job_queue.list_jobs()})

    @app.route("/api/jobs/<job_id>")
    def api_job(job_id):
        j = job_queue.get(job_id)
        if not j:
            return jsonify({"error": "not found"}), 404
        return jsonify(j)

    @app.route("/api/loras")
    def api_loras():
        loras = app.config["tester"]._fetch_loras()
        triggers = app.config["tester"].lora_triggers
        items = [{"name": l["name"], "alias": l.get("alias", ""), "trigger": triggers.get(l["name"])}
                 for l in loras]
        return jsonify({"loras": items})

    @app.route("/api/loras/trigger", methods=["POST"])
    def api_set_trigger():
        p = request.json or {}
        name = (p.get("name") or "").strip()
        trigger = (p.get("trigger") or "").strip()
        if not name or not trigger:
            return jsonify({"error": "需要 name 和 trigger"}), 400
        app.config["tester"].lora_triggers[name] = trigger
        app.config["tester"]._save_lora_triggers()
        return jsonify({"ok": True})

    @app.route("/api/loras/trigger/<name>", methods=["DELETE"])
    def api_del_trigger(name):
        if name in app.config["tester"].lora_triggers:
            del app.config["tester"].lora_triggers[name]
            app.config["tester"]._save_lora_triggers()
        return jsonify({"ok": True})

    @app.route("/api/artists")
    def api_artists():
        search = (request.args.get("search") or "").lower().strip()
        artists = app.config["tester"]._load_artists()
        if search:
            artists = [a for a in artists if search in a.lower()]
        return jsonify({"artists": artists, "total": len(artists)})

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        return jsonify(app.config["tester"].config)

    @app.route("/api/config", methods=["POST"])
    def api_config_set():
        p = request.json or {}
        key = p.get("key", "")
        value = p.get("value")
        cfg = app.config["tester"].config
        parts = key.split(".")
        obj = cfg
        for k in parts[:-1]:
            if k not in obj or not isinstance(obj[k], dict):
                return jsonify({"error": f"invalid key: {key}"}), 400
            obj = obj[k]
        obj[parts[-1]] = value
        return jsonify({"ok": True, "config": cfg})

    def _skill_list_data():
        tester = app.config["tester"]
        sdir = tester.skills_dir()
        items = []
        if sdir.exists():
            for d in sorted(sdir.iterdir()):
                if not d.is_dir() or not (d / "SKILL.md").exists():
                    continue
                parsed = tester._parse_skill(d / "SKILL.md")
                if parsed.get("_error"):
                    items.append({"name": d.name, "error": parsed["_error"]})
                    continue
                items.append({
                    "name": parsed["name"],
                    "description": parsed["description"],
                    "triggers": parsed["triggers"] or [],
                    "has_chain": bool(parsed["chain_template"]),
                    "body_preview": (parsed["body"] or "")[:200],
                })
        return items

    @app.route("/api/skills")
    def api_skills_list():
        return jsonify({"skills": _skill_list_data()})

    @app.route("/api/skills/<name>")
    def api_skills_get(name):
        tester = app.config["tester"]
        path, err = tester._resolve_skill_path(name)
        if err:
            return jsonify({"error": err}), 404
        parsed = tester._parse_skill(path)
        if parsed.get("_error"):
            return jsonify({"error": parsed["_error"]}), 500
        text = path.read_text(encoding="utf-8")
        return jsonify({
            "name": parsed["name"],
            "description": parsed["description"],
            "triggers": parsed["triggers"] or [],
            "chain_template": parsed["chain_template"] or "",
            "body": parsed["body"],
            "raw": text,
        })

    @app.route("/api/skills", methods=["POST"])
    def api_skills_create():
        p = request.json or {}
        name = (p.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name 不能为空"}), 400
        tester = app.config["tester"]
        sdir = tester.skills_dir()
        target = sdir / name / "SKILL.md"
        if target.exists():
            return jsonify({"error": f"已存在: {name}"}), 409
        try:
            ok, msg = tester._write_skill_file(
                name,
                description=(p.get("description") or "").strip(),
                triggers=p.get("triggers") or [],
                chain_template=(p.get("chain_template") or "").strip() or None,
                body=(p.get("body") or "").rstrip(),
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        if not ok:
            return jsonify({"error": msg}), 400
        return jsonify({"ok": True, "name": name, "skills": _skill_list_data()})

    @app.route("/api/skills/<name>", methods=["PUT"])
    def api_skills_update(name):
        p = request.json or {}
        tester = app.config["tester"]
        path, err = tester._resolve_skill_path(name)
        if err:
            return jsonify({"error": err}), 404
        try:
            ok, msg = tester._write_skill_file(
                name,
                description=(p.get("description") or "").strip(),
                triggers=p.get("triggers") or [],
                chain_template=(p.get("chain_template") or "").strip() or None,
                body=(p.get("body") or "").rstrip(),
                overwrite=True,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        if not ok:
            return jsonify({"error": msg}), 400
        return jsonify({"ok": True, "name": name, "skills": _skill_list_data()})

    @app.route("/api/skills/<name>", methods=["DELETE"])
    def api_skills_delete(name):
        import shutil
        tester = app.config["tester"]
        path, err = tester._resolve_skill_path(name)
        if err:
            return jsonify({"error": err}), 404
        shutil.rmtree(path.parent)
        return jsonify({"ok": True, "skills": _skill_list_data()})

    @app.route("/api/logs")
    def api_logs():
        log_dir = Path(app.config.get("log_dir", SCRIPT_DIR / "logs"))
        level = request.args.get("level", "DEBUG")
        search = request.args.get("search", "")
        tail = int(request.args.get("tail", 200))
        source = request.args.get("source", "sdbot.log")

        level_order = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3, "DEBUG": 4}
        min_level = level_order.get(level.upper(), 4)

        # List available log files
        log_files = []
        if log_dir.exists():
            log_files = sorted(log_dir.glob("*.log*"), reverse=True)[:20]
        files_info = [{"name": f.name, "size": f.stat().st_size} for f in log_files]

        entries = []
        log_file = log_dir / source
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    content = f.read()
                lines = content.splitlines()
                total = len(lines)
                for line in lines[-tail:]:
                    line_level = "DEBUG"
                    for lv in ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]:
                        if f"[{lv}]" in line:
                            line_level = lv
                            break
                    if level_order.get(line_level, 4) > min_level:
                        continue
                    if search and search.lower() not in line.lower():
                        continue
                    entries.append({"line": line, "level": line_level})
            except Exception as e:
                entries.append({"line": f"[ERR] 读取日志失败: {e}", "level": "ERROR"})
        else:
            entries.append({"line": f"[INFO] 日志文件不存在: {log_file}", "level": "INFO"})

        return jsonify({"entries": entries, "files": files_info, "total": len(lines) if log_file.exists() else 0})

    @app.route("/api/stats")
    def api_stats():
        t = app.config["tester"]
        artists = t._load_artists()
        loras = t._fetch_loras()
        combos = t.history.get("combos", {})
        u = t._llm().total_usage if t._chat_model else {"total_tokens": 0, "cost_yuan": 0}
        artist_count = {}
        for c in combos.values():
            for a in c.get("artists", []):
                artist_count[a] = artist_count.get(a, 0) + 1
        top_artists = sorted(artist_count.items(), key=lambda x: -x[1])[:20]
        mode_count = {}
        if outputs_dir.exists():
            for d in outputs_dir.iterdir():
                if not d.is_dir():
                    continue
                log_path = d / "generation_log.json"
                if log_path.exists():
                    try:
                        log = json.load(open(log_path, "r", encoding="utf-8"))
                        m = log.get("mode", "?")
                        mode_count[m] = mode_count.get(m, 0) + 1
                    except Exception:
                        pass
        return jsonify({
            "total_generated": len(combos),
            "total_artists": len(artists),
            "total_loras": len(loras),
            "total_tokens": u.get("total_tokens", 0),
            "total_cost": round(u.get("cost_yuan", 0), 6),
            "top_artists": [{"name": n, "count": c} for n, c in top_artists],
            "mode_count": mode_count,
        })


def main():
    import argparse
    parser = argparse.ArgumentParser(description="sdbot WebUI")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    tester = SDArtistTester()
    app = create_app(tester)
    job_queue = JobQueue(tester)
    register_routes(app, job_queue)
    url = f"http://{args.host}:{args.port}"
    print(f"  WebUI 启动: {url}")
    print(f"  按 Ctrl+C 停止")
    if not args.no_browser:
        import threading, webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
