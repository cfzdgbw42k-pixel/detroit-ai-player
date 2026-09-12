from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNNER_SRC = ROOT / "03_runner" / "src"
if str(RUNNER_SRC) not in sys.path:
    sys.path.insert(0, str(RUNNER_SRC))

from resolver import (  # noqa: E402
    ending_payload,
    node_condition_met,
    resolve_check_rule,
    resolve_choices,
    resolve_context,
)
from state import (  # noqa: E402
    apply_effects,
    evaluate_condition,
    extract_cross_chapter_state,
    initial_state,
)


SAVE_DIR = Path(__file__).resolve().parent / "saves"
CHAPTER_PATHS = sorted((ROOT / "01_json" / "zh").glob("ch*.json"))
EXPECTED_CHAPTER_IDS = [f"ch{index:02d}" for index in range(1, 33)]
ACTUAL_CHAPTER_IDS = [path.name[:4] for path in CHAPTER_PATHS]
if ACTUAL_CHAPTER_IDS != EXPECTED_CHAPTER_IDS:
    raise RuntimeError("章節資料不完整或順序錯誤，應為 ch01 至 ch32")
DATA_FINGERPRINT = hashlib.sha256(
    b"".join(path.read_bytes() for path in CHAPTER_PATHS)
).hexdigest()
TOTAL_ENDING_KEYS = {
    (json.loads(path.read_text(encoding="utf-8"))["chapter"]["id"], ending_id)
    for path in CHAPTER_PATHS
    for ending_id in json.loads(path.read_text(encoding="utf-8")).get("endings", {})
    if not str(ending_id).startswith("_")
}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_TIER_PRIORITY = {"worst": 0, "tragic": 1, "neutral": 2, "best": 3}


def _record_choice_aliases(state: dict[str, Any], node_id: str, choice_id: str) -> None:
    state[node_id] = choice_id
    if node_id == "n002_investigation_strategy":
        state["investigation"] = choice_id
    elif node_id == "n010_final_demand":
        state["final_demand"] = choice_id
    elif node_id == "n011_final_choice":
        state["final_choice"] = choice_id


def _resolve_mandatory_result(node: dict[str, Any], state: dict[str, Any]) -> str | None:
    system = node.get("system", {})
    if "result" in system:
        return system["result"]
    if "ending" in system:
        return system["ending"]
    ending_resolution = system.get("ending_resolution")
    if not isinstance(ending_resolution, dict):
        return None
    if "check" in ending_resolution:
        return resolve_check_rule(ending_resolution["check"], state) or None
    if len(ending_resolution) == 1:
        rule = next(iter(ending_resolution.values()))
        if isinstance(rule, dict):
            return rule.get("result")
    return None


def _node_track(node: dict[str, Any], tracks: set[str]) -> str | None:
    phase = node.get("phase", "")
    prefix = phase.split("_", 1)[0] if phase else ""
    return prefix if prefix in tracks else None


def _resolve_probability_rule(rule: dict[str, Any], difficulty: str, rng: random.Random) -> Any:
    if "result" in rule:
        return rule["result"]
    difficulty_rule = rule[difficulty]
    if "result" in difficulty_rule:
        payload = {key: copy.deepcopy(value) for key, value in difficulty_rule.items() if key in {"result", "state_update"}}
        return payload if "state_update" in payload else payload["result"]
    succeeded = rng.random() < difficulty_rule["probability_success"]
    picked = difficulty_rule["success"] if succeeded else difficulty_rule["failure"]
    if isinstance(picked, dict):
        payload = copy.deepcopy(picked)
        payload["_roll_outcome"] = "成功" if succeeded else "失敗"
        return payload
    return {"result": copy.deepcopy(picked), "_roll_outcome": "成功" if succeeded else "失敗"}


def resolve_post_choice_result(
    node: dict[str, Any], choice_id: str, state: dict[str, Any], difficulty: str, rng: random.Random
) -> Any:
    system = node.get("system", {})
    resolution_rule = system.get("resolution_rule")
    if resolution_rule and choice_id in resolution_rule:
        rule = resolution_rule[choice_id]
        if "check" in rule:
            return resolve_check_rule(rule["check"], state)
        return _resolve_probability_rule(rule, difficulty, rng)
    ending_resolution = system.get("ending_resolution")
    if not ending_resolution or choice_id not in ending_resolution:
        return None
    rule = ending_resolution[choice_id]
    if "result" in rule:
        return rule["result"]
    if "check" in rule:
        return resolve_check_rule(rule["check"], state)
    if difficulty in rule:
        return _resolve_probability_rule(rule, difficulty, rng)
    return None


def _pick_primary_ending(data: dict[str, Any], ending_ids: list[str]) -> str:
    unique = list(dict.fromkeys(ending_ids))
    def score(ending_id: str) -> tuple[int, int]:
        ending = data.get("endings", {}).get(ending_id, {})
        return (-len(ending.get("deaths", [])), _TIER_PRIORITY.get(ending.get("tier", "neutral"), 2))
    return min(unique, key=score)


def _single_ending_id(data: dict[str, Any]) -> str | None:
    endings = [key for key in data.get("endings", {}) if not str(key).startswith("_")]
    return endings[0] if len(endings) == 1 else None


def _contains_name(values: list[Any], name: str) -> bool:
    aliases = {
        "Connor": ["Connor", "康纳"], "Emma": ["Emma", "艾玛"],
        "Daniel": ["Daniel", "丹尼尔"], "Markus": ["Markus", "马库斯"],
    }.get(name, [name])
    return any(any(alias in str(value) for alias in aliases) for value in values)


def apply_derived_exports(state: dict[str, Any], rules: list[dict[str, Any]], result: dict[str, Any]) -> None:
    ending = result.get("ending", {})
    survivors, deaths = ending.get("survivors", []), ending.get("deaths", [])
    for rule in rules:
        target = rule.get("target")
        if not target:
            continue
        if "source" in rule:
            if "derive_rule" in rule:
                state[target] = evaluate_condition(rule["derive_rule"], state)
            elif rule["source"] in state:
                state[target] = copy.deepcopy(state[rule["source"]])
        elif "from_ending_survivors" in rule:
            name = str(rule["from_ending_survivors"])
            survived, died = _contains_name(survivors, name), _contains_name(deaths, name)
            state[target] = survived or (bool(rule.get("default_if_not_dead")) and not died)
        elif "from_ending_deaths" in rule:
            value = _contains_name(deaths, str(rule["from_ending_deaths"]))
            state[target] = not value if rule.get("invert") else value


def build_chapter_summary(segments: list[dict[str, Any]], state: dict[str, Any], chapter_id: str = "") -> str:
    parts: list[str] = []
    for index, segment in enumerate(segments):
        if chapter_id == "ch30_crossroads" and index in {0, 1} and not state.get("ch17_kara_alive"):
            continue
        if "text" in segment:
            parts.append(str(segment["text"]))
            continue
        variable = segment.get("condition_variable")
        options = segment.get("options", {})
        if variable == "_ending_id" and isinstance(state.get("_ending_ids"), list):
            parts.extend(str(options[item]) for item in state["_ending_ids"] if str(item) in options)
        elif variable is not None and str(state.get(variable, "")) in options:
            parts.append(str(options[str(state.get(variable, ""))]))
    return "".join(parts)


def _add_campaign_derived_state(state: dict[str, Any], data: dict[str, Any], result: dict[str, Any]) -> None:
    ending = result["ending"]
    prefix = data["chapter"]["id"].split("_", 1)[0]
    state["_ending_id"] = ending["id"]
    state["_ending_ids"] = [item["id"] for item in result.get("all_endings", [ending])]
    state[f"{prefix}_ending"] = ending["id"]
    state["connor_death_count"] = int(state.get("connor_death_count", 0))
    all_endings = result.get("all_endings", [ending])
    connor_destroyed = any(
        any(str(item).startswith(("Connor", "康纳")) for item in candidate.get("deaths", []))
        or candidate.get("id") in {"ending_connor_machine_destroyed", "ending_connor_deviant_destroyed", "ending_connor_destroyed"}
        for candidate in all_endings
    )
    if connor_destroyed:
        state["connor_death_count"] += 1


def _apply_procedural_hooks(chapter_id: str, node_id: str, state: dict[str, Any]) -> None:
    if chapter_id == "ch12_waiting_for_hank" and node_id == "n003_carlos_android":
        state["carlos_interacted"] = True
        state["ch12_carlos_body_available"] = True
    if chapter_id == "ch30_crossroads" and node_id == "n001_kara_jericho":
        if state.get("alice_response") == "distant" and state.get("alice_relationship", 0) <= -4:
            state["alice_left"] = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chapter_allowed(chapter_id: str, state: dict[str, Any]) -> tuple[bool, str]:
    rules = {
        "ch09_the_interrogation": (bool(state.get("ch06_deviant_found")), "前一案未找到可審訊的仿生人"),
        "ch10_fugitives": (bool(state.get("ch07_kara_alive")) and bool(state.get("ch07_alice_alive")), "卡拉或愛麗絲未能抵達本章"),
        "ch11_from_the_dead": (state.get("ch08_markus_junkyard", True) is not False, "馬庫斯未被送往廢料場"),
        "ch13_on_the_run": (bool(state.get("ch07_kara_alive")) and bool(state.get("ch07_alice_alive")), "卡拉或愛麗絲未能抵達本章"),
        "ch14_jericho": (bool(state.get("ch11_markus_alive")), "馬庫斯未能抵達耶利哥線"),
        "ch16_time_to_decide": (bool(state.get("ch14_markus_at_jericho")), "馬庫斯不在耶利哥"),
        "ch17_zlatko": (bool(state.get("ch13_kara_alive")) and not bool(state.get("ch13_kara_captured")), "卡拉已死亡或被捕"),
        "ch21_the_pirates_cove": (bool(state.get("ch17_kara_alive")) and bool(state.get("ch17_alice_alive")), "卡拉或愛麗絲未能抵達本章"),
        "ch25_midnight_train": (bool(state.get("ch17_kara_alive")) and bool(state.get("ch17_alice_alive")) and bool(state.get("ch17_luther_joined")), "卡拉一行未能抵達本章"),
        "ch26_capitol_park": (bool(state.get("ch14_markus_at_jericho")), "馬庫斯不在耶利哥"),
        "ch30_crossroads": (bool(state.get("ch17_kara_alive")) or bool(state.get("ch28_markus_alive")) or bool(state.get("ch29_connor_alive")), "三條主角線都已終止"),
        "ch31_night_of_the_soul": (bool(state.get("ch30_markus_alive")) or bool(state.get("ch30_connor_alive")), "馬庫斯與康納線都已終止"),
    }
    return rules.get(chapter_id, (True, ""))


def _apply_chapter_import_hooks(chapter_id: str, state: dict[str, Any]) -> None:
    """Translate earlier chapter exports into the generic flags used later.

    Crossroads ships with ``kara_alive``/``alice_alive`` defaulting to true.
    Without this translation, a Kara who died on the highway in chapter 13 can
    incorrectly reappear when chapter 30 begins.
    """
    if chapter_id == "ch32_battle_for_detroit":
        # Chapter 32 defaults to a peaceful Markus-led demonstration. If
        # chapter 31 was skipped (both leads dead), its strategy export is
        # absent and that default would bring Markus back to life. North is
        # the only possible movement leader after his death, irrespective of
        # whether chapter 31 produced its usual strategy export.
        if state.get("ch30_markus_alive") is False:
            state["ch31_markus_strategy"] = "north_attacks"
        return
    if chapter_id != "ch30_crossroads":
        return

    kara_alive = state.get(
        "ch17_kara_alive",
        state.get("ch13_kara_alive", state.get("ch07_kara_alive", True)),
    )
    alice_alive = state.get(
        "ch17_alice_alive",
        state.get("ch13_alice_alive", state.get("ch07_alice_alive", True)),
    )
    if state.get("ch13_kara_captured"):
        kara_alive = False
        alice_alive = False
    state["kara_alive"] = bool(kara_alive)
    state["alice_alive"] = bool(alice_alive)


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned[:48] or uuid.uuid4().hex[:12]


def _clean_role_prompt(text: str) -> str:
    return re.split(r"(?:请|請)?(?:以|用)JSON格式回(?:复|覆)", text, maxsplit=1)[0].strip()


class CampaignEngine:
    def __init__(self, session: dict[str, Any]):
        self.session = session
        self.chapter_data: dict[str, Any] | None = None
        current = session.get("current")
        if current:
            self.chapter_data = self._load_chapter(current["chapter_index"])

    @classmethod
    def create(cls, name: str, difficulty: str = "casual") -> "CampaignEngine":
        if difficulty not in {"casual", "experienced", "hardcore"}:
            raise ValueError("不支援的難度")
        session_id = uuid.uuid4().hex
        session = {
            "version": 1,
            "data_fingerprint": DATA_FINGERPRINT,
            "revision": 0,
            "id": session_id,
            "name": name.strip() or "玩家・容容線",
            "difficulty": difficulty,
            "created_at": _now(),
            "updated_at": _now(),
            "chapter_index": 0,
            "cross_chapter_state": {},
            "memory_segments": [],
            "chapters": [],
            "skipped_chapters": [],
            "current": None,
            "complete": False,
        }
        engine = cls(session)
        engine._start_chapter()
        engine.save()
        return engine

    @classmethod
    def load(cls, session_id: str) -> "CampaignEngine":
        path = SAVE_DIR / f"{_safe_id(session_id)}.json"
        backup_path = path.with_suffix(".json.bak")
        if not path.is_file() and not backup_path.is_file():
            raise FileNotFoundError("找不到這個存檔")
        candidates = [path, backup_path]
        last_error: Exception | None = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                cls._validate_session(data, session_id)
                engine = cls(data)
                engine._validate_pending()
                return engine
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                last_error = error
        raise ValueError(f"存檔損壞且無法從備份恢復：{last_error}")

    @staticmethod
    def _validate_session(data: dict[str, Any], requested_id: str) -> None:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("存檔版本不相容")
        if data.get("id") != requested_id or not re.fullmatch(r"[0-9a-f]{32}", str(data.get("id", ""))):
            raise ValueError("存檔識別碼不正確")
        if data.get("data_fingerprint") != DATA_FINGERPRINT:
            raise ValueError("遊戲資料已變更，這份存檔不能安全續玩")
        index = data.get("chapter_index")
        if not isinstance(index, int) or not 0 <= index <= len(CHAPTER_PATHS):
            raise ValueError("存檔章節位置不正確")
        if not isinstance(data.get("revision"), int) or data["revision"] < 0:
            raise ValueError("存檔修訂編號不正確")

    def _validate_pending(self) -> None:
        current = self.session.get("current")
        if not current or current.get("complete"):
            return
        assert self.chapter_data is not None
        if current.get("chapter_index") != self.session["chapter_index"]:
            raise ValueError("存檔章節識別不一致")
        pending = current.get("pending")
        if not pending:
            raise ValueError("存檔缺少目前場景")
        index = pending.get("node_index")
        if not isinstance(index, int) or not 0 <= index < len(self.chapter_data["nodes"]):
            raise ValueError("存檔場景位置不正確")
        node = self.chapter_data["nodes"][index]
        if pending.get("node_id") != node.get("id"):
            raise ValueError("存檔場景識別不一致")
        expected = [item["id"] for item in resolve_choices(node, current["state"])] if pending.get("choices") else []
        actual = [item.get("id") for item in pending.get("choices", [])]
        if expected != actual:
            raise ValueError("存檔選項與目前遊戲資料不一致")
        if pending.get("context") != resolve_context(node, current["state"]):
            raise ValueError("存檔場景內容與目前遊戲資料不一致")

    @staticmethod
    def list_sessions() -> list[dict[str, Any]]:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        items = []
        session_ids = {path.stem for path in SAVE_DIR.glob("*.json")}
        session_ids.update(path.name.removesuffix(".json.bak") for path in SAVE_DIR.glob("*.json.bak"))
        for session_id in session_ids:
            try:
                data = CampaignEngine.load(session_id).session
                items.append({
                    "id": data["id"],
                    "name": data["name"],
                    "difficulty": data["difficulty"],
                    "chapter_index": data.get("chapter_index", 0),
                    "chapter_count": len(CHAPTER_PATHS),
                    "complete": data.get("complete", False),
                    "updated_at": data.get("updated_at"),
                })
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item.get("updated_at") or "", reverse=True)

    @staticmethod
    def ending_progress() -> dict[str, Any]:
        """Return spoiler-free ending completion across all local campaigns."""
        discovered: set[tuple[str, str]] = set()
        session_ids = {path.stem for path in SAVE_DIR.glob("*.json")}
        session_ids.update(path.name.removesuffix(".json.bak") for path in SAVE_DIR.glob("*.json.bak"))
        for session_id in session_ids:
            try:
                data = CampaignEngine.load(session_id).session
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            for chapter in data.get("chapters", []):
                chapter_id = str(chapter.get("chapter_id", ""))
                for ending in chapter.get("all_endings", []):
                    key = (chapter_id, str(ending.get("id", "")))
                    if key in TOTAL_ENDING_KEYS:
                        discovered.add(key)
        total = len(TOTAL_ENDING_KEYS)
        count = len(discovered)
        return {
            "discovered": count,
            "total": total,
            "percent": round((count / total * 100) if total else 0, 1),
        }

    @classmethod
    def import_backup(cls, payload: dict[str, Any]) -> "CampaignEngine":
        if not isinstance(payload, dict) or payload.get("format") != "Detroit Blind Run 完整存檔":
            raise ValueError("這不是主持台匯出的完整存檔")
        original = payload.get("session")
        if not isinstance(original, dict):
            raise ValueError("完整存檔缺少遊戲資料")
        cls._validate_session(original, str(original.get("id", "")))
        probe = cls(copy.deepcopy(original))
        probe._validate_pending()
        restored = copy.deepcopy(original)
        restored["id"] = uuid.uuid4().hex
        restored["name"] = f"{restored.get('name', '匯入存檔')}（匯入）"
        restored["created_at"] = _now()
        restored["updated_at"] = _now()
        restored["revision"] = 0
        engine = cls(restored)
        engine.save()
        return engine

    def save(self) -> None:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        self.session["updated_at"] = _now()
        self.session["revision"] = int(self.session.get("revision", 0)) + 1
        path = SAVE_DIR / f"{_safe_id(self.session['id'])}.json"
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(self.session, ensure_ascii=False, indent=2), encoding="utf-8")
        if path.is_file():
            shutil.copy2(path, path.with_suffix(".json.bak"))
        temporary.replace(path)

    @classmethod
    def delete_session(cls, session_id: str, expected_name: str) -> str:
        """Permanently remove one explicitly confirmed local campaign save."""
        engine = cls.load(session_id)
        actual_name = str(engine.session.get("name", ""))
        if expected_name != actual_name:
            raise ValueError("存檔名稱不一致，已取消刪除")
        path = SAVE_DIR / f"{_safe_id(session_id)}.json"
        backup_path = path.with_suffix(".json.bak")
        path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        return actual_name

    def assert_precondition(self, revision: Any, node_id: Any = None) -> None:
        if revision != self.session.get("revision"):
            raise RuntimeError("STALE_SAVE")
        if node_id is not None:
            pending = (self.session.get("current") or {}).get("pending")
            if not pending or node_id != pending.get("node_id"):
                raise RuntimeError("STALE_SCENE")

    def _load_chapter(self, index: int) -> dict[str, Any]:
        if index < 0 or index >= len(CHAPTER_PATHS):
            raise IndexError("章節不存在")
        return json.loads(CHAPTER_PATHS[index].read_text(encoding="utf-8"))

    def _start_chapter(self) -> None:
        index = self.session["chapter_index"]
        while index < len(CHAPTER_PATHS):
            data = self._load_chapter(index)
            state = initial_state(data)
            state.update(copy.deepcopy(self.session["cross_chapter_state"]))
            _apply_chapter_import_hooks(data["chapter"]["id"], state)
            allowed, reason = _chapter_allowed(data["chapter"]["id"], state)
            if allowed:
                break
            self.session["skipped_chapters"].append({
                "chapter_number": data["chapter"].get("chapter_number", index + 1),
                "title": data["chapter"].get("title_zh") or data["chapter"].get("title"),
                "reason": reason,
            })
            index += 1
            self.session["chapter_index"] = index
        if index >= len(CHAPTER_PATHS):
            self.session["complete"] = True
            self.session["current"] = None
            return
        protagonist = data["chapter"].get("protagonist")
        tracks = {str(name).lower() for name in protagonist} if isinstance(protagonist, list) else set()
        self.chapter_data = data
        self.session["current"] = {
            "chapter_index": index,
            "node_index": 0,
            "state": state,
            "decisions": [],
            "collected_endings": [],
            "ended_tracks": [],
            "protagonist_tracks": sorted(tracks),
            "pending": None,
            "complete": False,
            "ending": None,
            "all_endings": [],
            "started_at": _now(),
        }
        self._seek()

    def _seek(self) -> None:
        current = self.session["current"]
        if not current or current["complete"]:
            return
        assert self.chapter_data is not None
        nodes = self.chapter_data["nodes"]
        while current["node_index"] < len(nodes):
            node = nodes[current["node_index"]]
            track = _node_track(node, set(current["protagonist_tracks"]))
            if track and track in set(current["ended_tracks"]):
                current["node_index"] += 1
                continue
            if not node_condition_met(node, current["state"]):
                current["node_index"] += 1
                continue
            context = resolve_context(node, current["state"])
            try:
                choices = resolve_choices(node, current["state"])
            except ValueError:
                if node.get("type") in {"mandatory", "narrative"}:
                    choices = []
                else:
                    raise
            current["pending"] = {
                "node_index": current["node_index"],
                "node_id": node["id"],
                "context": context,
                "choices": copy.deepcopy(choices),
                "type": node.get("type", "choice"),
            }
            return
        self._finish_chapter()

    def act(self, choice_id: str | None = None, reason: str = "") -> dict[str, Any]:
        current = self.session.get("current")
        if not current or current.get("complete"):
            raise ValueError("目前沒有等待處理的場景")
        pending = current.get("pending")
        if not pending:
            raise ValueError("目前沒有等待處理的場景")
        assert self.chapter_data is not None
        node = self.chapter_data["nodes"][pending["node_index"]]
        choices = pending["choices"]
        result = None
        selected = None
        roll_outcome = None

        if choices:
            selected = next((item for item in choices if item["id"] == choice_id), None)
            if selected is None:
                raise ValueError("請選擇畫面上提供的選項")
            effects = node.get("system", {}).get("effects", {}).get(choice_id, {})
            apply_effects(current["state"], effects)
            _record_choice_aliases(current["state"], node["id"], str(choice_id))
            result = resolve_post_choice_result(
                node,
                str(choice_id),
                current["state"],
                self.session["difficulty"],
                random.SystemRandom(),
            )
            # Some published QTE rules return a structured payload containing
            # both the result label and a state update.
            if isinstance(result, dict):
                roll_outcome = result.get("_roll_outcome")
                apply_effects(current["state"], result.get("state_update", {}))
                result = result.get("result")
            if result:
                resolution = node.get("system", {}).get("resolution_effects", {}).get(result, {})
                apply_effects(current["state"], resolution)
        else:
            apply_effects(current["state"], node.get("system", {}).get("effects", {}))
            result = _resolve_mandatory_result(node, current["state"])
            if result and result.startswith("ending_"):
                ending_effects = node.get("system", {}).get("ending_effects", {}).get(result, {})
                apply_effects(current["state"], ending_effects)

        _apply_procedural_hooks(self.chapter_data["chapter"]["id"], node["id"], current["state"])

        if result and not result.startswith("ending_"):
            prefix = node["id"].split("_", 1)[0]
            current["state"][f"_{prefix}_result"] = result
            current["state"][f"_{node['id']}_result"] = result
            if node["id"] == "n011_final_choice":
                current["state"]["_n011_result"] = result

        current["decisions"].append({
            "node_id": node["id"],
            "context": pending["context"],
            "choices": [{"id": item["id"], "text": item["text"]} for item in choices],
            "selected_id": selected["id"] if selected else None,
            "selected_text": selected["text"] if selected else None,
            "reason": reason.strip() if selected else "",
            "random_event": node.get("type") == "qte_converted" or roll_outcome is not None,
            "resolution": roll_outcome or ("固定判定" if node.get("type") == "qte_converted" else ""),
            "decided_at": _now(),
            "internal_result": result,
        })
        current["pending"] = None
        current["node_index"] += 1

        if result and result.startswith("ending_"):
            protagonist = self.chapter_data["chapter"].get("protagonist")
            chapter_id = self.chapter_data["chapter"]["id"]
            if isinstance(protagonist, list) and chapter_id != "ch13_on_the_run":
                if result not in current["collected_endings"]:
                    current["collected_endings"].append(result)
                track = _node_track(node, set(current["protagonist_tracks"]))
                if track and track not in current["ended_tracks"]:
                    current["ended_tracks"].append(track)
            else:
                self._finish_chapter(result)

        if not current["complete"]:
            self._seek()
        self.save()
        return self.view()

    def _finish_chapter(self, ending_id: str | None = None) -> None:
        current = self.session["current"]
        assert current is not None and self.chapter_data is not None
        collected = current["collected_endings"]
        if ending_id is None and collected:
            if self.chapter_data["chapter"]["id"] == "ch31_night_of_the_soul":
                primary_ids = {"ending_peaceful_protest", "ending_violent_attack", "ending_north_attacks"}
                ending_id = next((item for item in reversed(collected) if item in primary_ids), None)
            ending_id = ending_id or _pick_primary_ending(self.chapter_data, collected)
        if ending_id is None:
            ending_id = _single_ending_id(self.chapter_data)
        # Chapter 1 marks a direct refusal as the internal ``auto_fail`` path,
        # but the published tree has no separate terminal node for that path.
        # Resolve it to the matching failure ending so a valid player choice
        # cannot leave the campaign without an ending.
        if ending_id is None and current["state"].get("path") == "auto_fail":
            if "ending_failed_to_reach" in self.chapter_data.get("endings", {}):
                ending_id = "ending_failed_to_reach"
        if ending_id is None:
            raise RuntimeError("本章走完但無法判定結局")

        ending_ids = collected or [ending_id]
        all_endings = [ending_payload(self.chapter_data, item) for item in ending_ids]
        primary = ending_payload(self.chapter_data, ending_id)
        current["ending"] = primary
        current["all_endings"] = all_endings
        current["complete"] = True
        current["pending"] = None

        final_state = copy.deepcopy(current["state"])
        result = {"ending": primary, "all_endings": all_endings}
        _add_campaign_derived_state(final_state, self.chapter_data, result)
        campaign = self.chapter_data.get("campaign", {})
        apply_derived_exports(final_state, campaign.get("derived_exports", []), result)
        exports = extract_cross_chapter_state(final_state, campaign.get("cross_chapter_exports", []))
        self.session["cross_chapter_state"].update(exports)
        chapter = self.chapter_data["chapter"]
        summary = build_chapter_summary(campaign.get("summary_segments", []), final_state, chapter["id"])
        if summary:
            self.session["memory_segments"].append(summary)

        self.session["chapters"].append({
            "chapter_index": self.session["chapter_index"],
            "chapter_id": chapter["id"],
            "chapter_number": chapter.get("chapter_number", self.session["chapter_index"] + 1),
            "title": chapter.get("title_zh") or chapter.get("title"),
            "protagonist": chapter.get("protagonist"),
            "decisions": copy.deepcopy(current["decisions"]),
            "ending": primary,
            "all_endings": all_endings,
            "summary": summary,
            "reflection": "",
            "completed_at": _now(),
        })

    def save_reflection(self, text: str) -> dict[str, Any]:
        current = self.session.get("current")
        if not current or not current.get("complete") or not self.session["chapters"]:
            raise ValueError("目前沒有可寫回顧的已完成章節")
        self.session["chapters"][-1]["reflection"] = text.strip()
        self.save()
        return self.view()

    def next_chapter(self) -> dict[str, Any]:
        current = self.session.get("current")
        if not current or not current.get("complete"):
            raise ValueError("本章尚未結束")
        self.session["chapter_index"] += 1
        self.session["current"] = None
        self._start_chapter()
        self.save()
        return self.view()

    def view(self) -> dict[str, Any]:
        if self.session.get("complete"):
            return {
                "screen": "campaign_complete",
                "session": self._public_session(),
                "chapters": [self._safe_chapter(item) for item in self.session["chapters"]],
                "skipped_chapters": copy.deepcopy(self.session.get("skipped_chapters", [])),
            }
        current = self.session["current"]
        assert current is not None and self.chapter_data is not None
        chapter = self.chapter_data["chapter"]
        base = {
            "session": self._public_session(),
            "chapter": {
                "number": chapter.get("chapter_number", self.session["chapter_index"] + 1),
                "title": chapter.get("title_zh") or chapter.get("title"),
                "protagonist": chapter.get("protagonist"),
            },
            "decision_count": len(current["decisions"]),
        }
        if current["complete"]:
            return {
                **base,
                "screen": "chapter_complete",
                "ending": self._safe_ending(current["ending"]),
                "all_endings": [self._safe_ending(item) for item in current["all_endings"]],
                "chapter_card": self._safe_chapter(self.session["chapters"][-1]),
                "has_next": self.session["chapter_index"] + 1 < len(CHAPTER_PATHS),
            }
        pending = current["pending"]
        assert pending is not None
        choices = [
            {"id": item["id"], "text": item["text"], "label": LETTERS[index]}
            for index, item in enumerate(pending["choices"])
        ]
        return {
            **base,
            "screen": "scene",
            "node_id": pending["node_id"],
            "context": pending["context"],
            "choices": choices,
            "copy_text": self._copy_text(pending["context"], choices),
            "is_first_scene": len(current["decisions"]) == 0,
        }

    def _copy_text(self, context: str, choices: list[dict[str, str]]) -> str:
        assert self.chapter_data is not None
        chapter = self.chapter_data["chapter"]
        lines = [
            f"《底特律：變人》盲玩｜{self.session['name']}",
            f"第 {chapter.get('chapter_number', self.session['chapter_index'] + 1)} 章・{chapter.get('title_zh') or chapter.get('title')}",
        ]
        current = self.session["current"]
        if current and len(current["decisions"]) == 0:
            role = self.chapter_data.get("system_prompt", {}).get("content", "")
            role = _clean_role_prompt(role)
            if role:
                lines.extend(["", "角色：", role])
            if self.session["memory_segments"]:
                lines.extend(["", "此前發生的事：", "\n".join(self.session["memory_segments"])])
        lines.extend(["", "目前場景：", context])
        if choices:
            lines.extend(["", "你要怎麼做？"])
            lines.extend(f"{item['label']}. {item['text']}" for item in choices)
            lines.extend([
                "",
                "請只依據目前提供的資訊盲選，不使用攻略或已知後續。",
                "請用這個格式回答：選擇：A｜理由：50 字以內的理由",
            ])
        else:
            lines.extend(["", "這一幕沒有選項，讀完後請告訴容容繼續。"])
        return "\n".join(lines)

    def export_safe(self) -> dict[str, Any]:
        current = self.session.get("current")
        result = {
            "format": "Detroit AI Player 盲玩交接卡",
            "session": self._public_session(),
            "completed_chapters": [self._safe_chapter(item) for item in self.session["chapters"]],
            "skipped_chapters": copy.deepcopy(self.session.get("skipped_chapters", [])),
            "player_memory": copy.deepcopy(self.session["memory_segments"]),
            "current": self.view(),
            "current_chapter_role": _clean_role_prompt((self.chapter_data or {}).get("system_prompt", {}).get("content", "")),
            "current_chapter_history": [
                {
                    "node_id": item["node_id"],
                    "context": item["context"],
                    "choices": copy.deepcopy(item["choices"]),
                    "selected_id": item["selected_id"],
                    "selected_text": item["selected_text"],
                    "reason": item["reason"],
                    "random_event": item.get("random_event", False),
                    "resolution": item.get("resolution", ""),
                    "decided_at": item["decided_at"],
                }
                for item in ((current or {}).get("decisions", []))
            ],
            "note": "這是對話交接卡，不是可匯入的原始存檔；本機續玩仍使用主持台內的存檔。",
        }
        result["current"].pop("session", None)
        return result

    def export_backup(self) -> dict[str, Any]:
        return {
            "format": "Detroit Blind Run 完整存檔",
            "warning": "包含遊戲內部狀態。只用於主持台匯入，不要貼給正在盲玩的玩家。",
            "session": copy.deepcopy(self.session),
        }

    def history_view(self) -> dict[str, Any]:
        current = self.session.get("current")
        current_history = None
        if current and not current.get("complete"):
            data = self.chapter_data or self._load_chapter(current["chapter_index"])
            chapter = data["chapter"]
            current_history = {
                "chapter_number": chapter.get("chapter_number", current["chapter_index"] + 1),
                "title": chapter.get("title_zh") or chapter.get("title"),
                "decisions": [
                    {
                        "selected_text": item.get("selected_text"),
                        "reason": item.get("reason", ""),
                        "random_event": item.get("random_event", False),
                        "resolution": item.get("resolution", ""),
                        "decided_at": item.get("decided_at", ""),
                    }
                    for item in current.get("decisions", [])
                    if item.get("selected_text")
                ],
            }
        return {
            "session": self._public_session(),
            "chapters": [self._safe_chapter(item) for item in self.session["chapters"]],
            "skipped_chapters": copy.deepcopy(self.session.get("skipped_chapters", [])),
            "current": current_history,
        }

    def _public_session(self) -> dict[str, Any]:
        return {
            "id": self.session["id"],
            "name": self.session["name"],
            "difficulty": self.session["difficulty"],
            "chapter_index": self.session["chapter_index"],
            "chapter_count": len(CHAPTER_PATHS),
            "complete": self.session["complete"],
            "updated_at": self.session["updated_at"],
            "revision": self.session["revision"],
        }

    @staticmethod
    def _safe_ending(ending: dict[str, Any] | None) -> dict[str, Any] | None:
        if not ending:
            return None
        return {"id": ending["id"], "title": ending["title"], "narrative": ending.get("narrative", "")}

    @classmethod
    def _safe_chapter(cls, chapter: dict[str, Any]) -> dict[str, Any]:
        return {
            "chapter_number": chapter["chapter_number"],
            "title": chapter["title"],
            "protagonist": chapter["protagonist"],
            "decisions": [
                {
                    "node_id": item["node_id"],
                    "context": item["context"],
                    "choices": item["choices"],
                    "selected_id": item["selected_id"],
                    "selected_text": item["selected_text"],
                    "reason": item["reason"],
                    "random_event": item.get("random_event", False),
                    "resolution": item.get("resolution", ""),
                    "decided_at": item["decided_at"],
                }
                for item in chapter["decisions"]
            ],
            "ending": cls._safe_ending(chapter["ending"]),
            "all_endings": [cls._safe_ending(item) for item in chapter["all_endings"]],
            "summary": chapter["summary"],
            "reflection": chapter.get("reflection", ""),
            "completed_at": chapter["completed_at"],
        }
