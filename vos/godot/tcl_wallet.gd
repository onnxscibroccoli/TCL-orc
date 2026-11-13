extends Node
## Portable Godot vOS wallet — the SAME GDScript runs on Android, desktop, web.
## One non-redeemable tau balance across games; game-specific inventories are
## explicitly separate. Local demo only; never connected to TCL financial ledger.

signal wallet_changed(view: Dictionary)

const SAVE_PATH := "user://tcl_vos_wallet_v1.json"
const STARTING_TAU := 1200
const MAX_EVENTS := 10000
const MAX_BALANCE := 10000000
const REGISTERED := {
    "civitas99": ["work", "advance"],
    "opencity": ["delivery"],
    "nightdrive": ["paint", "turbo", "refuel"],
}
const GENESIS := "TCL-VOS-GODOT-LOCAL-V1"

var _state: Dictionary = {}
var _events: Array[Dictionary] = []
var _ids: Dictionary = {}
var _locked := false


func _ready() -> void:
    reset_session()
    if FileAccess.file_exists(SAVE_PATH):
        if not _read_save():
            _locked = true
            push_error("TCL vOS save validation failed. Preserve the file; writes are disabled.")
    wallet_changed.emit(snapshot())


func reset_session() -> void:
    _state = {
        "balance": STARTING_TAU, "issued": STARTING_TAU, "sunk": 0,
        "turn": 1, "worked_turn": 0, "deliveries": 0,
        "paint": false, "turbo": false, "fuel": 60,
    }
    _events.clear()
    _ids.clear()
    _locked = false


func snapshot() -> Dictionary:
    return {
        "wallet": _state.duplicate(true),
        "events": _events.size(),
        "hash": _events.back()["hash"] if not _events.is_empty() else GENESIS,
        "locked": _locked,
    }


func dispatch(game: String, action: String, request_id: String) -> Dictionary:
    if _locked:
        return {"ok": false, "error": "Save integrity error; game economy locked"}
    if not REGISTERED.has(game) or not action in REGISTERED[game]:
        return {"ok": false, "error": "Unregistered game capability"}
    if request_id.length() < 6 or request_id.length() > 72:
        return {"ok": false, "error": "Invalid idempotency key"}
    if _ids.has(request_id):
        if _ids[request_id] == game + "/" + action:
            return {"ok": true, "duplicate": true, "state": snapshot()}
        return {"ok": false, "error": "Request key reused for different command"}
    if _events.size() >= MAX_EVENTS:
        return {"ok": false, "error": "Event journal limit reached"}
    var candidate: Dictionary = _state.duplicate(true)
    var error := _apply(candidate, game, action)
    if error != "":
        return {"ok": false, "error": error}
    if not _conserved(candidate):
        return {"ok": false, "error": "Currency conservation failed"}
    var prev: String = _events.back()["hash"] if not _events.is_empty() else GENESIS
    var rec: Dictionary = {
        "seq": _events.size() + 1, "game": game, "action": action,
        "id": request_id, "before": prev, "state": candidate.duplicate(true),
    }
    rec["hash"] = (prev + "|" + JSON.stringify(rec)).sha256_text()
    var proposed: Array[Dictionary] = _events.duplicate(true)
    proposed.append(rec)
    if not _write_save(proposed):
        return {"ok": false, "error": "Could not commit durable save"}
    _events = proposed
    _state = candidate
    _ids[request_id] = game + "/" + action
    wallet_changed.emit(snapshot())
    return {"ok": true, "duplicate": false, "state": snapshot()}


func _apply(s: Dictionary, game: String, action: String) -> String:
    if game == "civitas99":
        if action == "work":
            if s["worked_turn"] == s["turn"]:
                return "Only one civic shift per turn"
            s["worked_turn"] = s["turn"]
            s["balance"] += 30
            s["issued"] += 30
        elif action == "advance":
            if s["turn"] >= 500:
                return "City has reached its turn limit"
            s["turn"] += 1
    elif game == "opencity" and action == "delivery":
        if s["deliveries"] >= 200:
            return "Delivery season completed"
        s["deliveries"] += 1
        s["balance"] += 45
        s["issued"] += 45
    elif game == "nightdrive":
        var price := 0
        match action:
            "paint":
                if s["paint"]:
                    return "Paint already purchased"
                price = 60
                s["paint"] = true
            "turbo":
                if s["turbo"]:
                    return "Turbo already purchased"
                price = 100
                s["turbo"] = true
            "refuel":
                if s["fuel"] == 60:
                    return "Tank already full"
                price = 15
                s["fuel"] = 60
        if s["balance"] < price:
            return "Insufficient TCL vOS tau"
        s["balance"] -= price
        s["sunk"] += price
    if s["issued"] > MAX_BALANCE:
        return "Game credit budget reached"
    return ""


func _conserved(s: Dictionary) -> bool:
    return (
        typeof(s["balance"]) == TYPE_INT
        and s["balance"] >= 0
        and s["issued"] == s["balance"] + s["sunk"]
        and s["issued"] <= MAX_BALANCE
        and s["sunk"] >= 0
    )


func _write_save(entries: Array[Dictionary]) -> bool:
    var target := ProjectSettings.globalize_path(SAVE_PATH)
    var temporary := target + ".tmp"
    var file := FileAccess.open(temporary, FileAccess.WRITE)
    if file == null:
        return false
    file.store_string(JSON.stringify({"version": 1, "events": entries}) + "\n")
    file.flush()
    file.close()
    var result := DirAccess.rename_absolute(temporary, target)
    if result != OK:
        DirAccess.remove_absolute(temporary)
        return false
    return true


func _read_save() -> bool:
    var file := FileAccess.open(SAVE_PATH, FileAccess.READ)
    if file == null or file.get_length() > 8000000:
        return false
    var document: Variant = JSON.parse_string(file.get_as_text())
    if typeof(document) != TYPE_DICTIONARY or document.get("version") != 1:
        return false
    var records: Variant = document.get("events", null)
    if typeof(records) != TYPE_ARRAY or records.size() > MAX_EVENTS:
        return false
    var previous := GENESIS
    var last_seq := 0
    var fresh := _state.duplicate(true)
    var seen := {}
    var checked: Array[Dictionary] = []
    for item in records:
        if typeof(item) != TYPE_DICTIONARY or item.size() != 7:
            return false
        if not item.has_all(["seq", "game", "action", "id", "before", "state", "hash"]):
            return false
        if typeof(item["seq"]) != TYPE_FLOAT and typeof(item["seq"]) != TYPE_INT:
            return false
        if int(item["seq"]) != last_seq + 1 or item["before"] != previous:
            return false
        var game: String = str(item["game"])
        var action: String = str(item["action"])
        var request_id: String = str(item["id"])
        if not REGISTERED.has(game) or not action in REGISTERED[game] or seen.has(request_id):
            return false
        var draft: Dictionary = fresh.duplicate(true)
        if _apply(draft, game, action) != "" or not _conserved(draft):
            return false
        if JSON.stringify(item["state"]) != JSON.stringify(draft):
            return false
        var unsigned: Dictionary = item.duplicate(true)
        unsigned.erase("hash")
        var digest: String = (previous + "|" + JSON.stringify(unsigned)).sha256_text()
        if item["hash"] != digest:
            return false
        checked.append(item)
        seen[request_id] = game + "/" + action
        fresh = draft
        previous = digest
        last_seq += 1
    _state = fresh
    _events = checked
    _ids = seen
    return true
