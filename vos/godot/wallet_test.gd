extends SceneTree
## Godot CLI integration tests: validates the SAME GDScript used by Android,
## Linux, Windows, macOS, and Web export, without Java/Python gameplay forks.

var errors: Array[String] = []


func _initialize() -> void:
    call_deferred("_run")


func _expect(condition: bool, message: String) -> void:
    if not condition:
        errors.append(message)
        push_error(message)


func _run() -> void:
    var w: Node = load("res://vos/tcl_wallet.gd").new()
    w.reset_session()
    var initial: Dictionary = w.snapshot()
    _expect(initial["wallet"]["balance"] == 1200, "Wrong starting tau")
    _expect(initial["wallet"]["issued"] == 1200, "Wrong starting issuance")
    var earned: Dictionary = w.dispatch("civitas99", "work", "civitas:turn:1")
    _expect(earned["ok"], "Civitas work failed")
    _expect(w.snapshot()["wallet"]["balance"] == 1230, "Civitas work not in universal wallet")
    var dup: Dictionary = w.dispatch("civitas99", "work", "civitas:turn:1")
    _expect(dup["ok"] and dup["duplicate"], "Replay idempotency failed")
    _expect(w.snapshot()["events"] == 1, "Duplicate issued a second credit")
    _expect(not w.dispatch("civitas99", "work", "civitas:turn:2")["ok"],
            "Same-turn duplicate work should fail")
    var car: Dictionary = w.dispatch("opencity", "delivery", "opencity:mission:1")
    _expect(car["ok"], "OpenCity mission reward failed")
    _expect(w.snapshot()["wallet"]["balance"] == 1275, "Car reward was not shared")
    var purchase: Dictionary = w.dispatch("nightdrive", "turbo", "nightdrive:buy:turbo")
    _expect(purchase["ok"], "NightDrive purchase with shared tau failed")
    _expect(w.snapshot()["wallet"]["balance"] == 1175, "Cross-game spending failed")
    _expect(w.snapshot()["wallet"]["issued"] == 1275, "Issuance should not drop on spend")
    _expect(w.snapshot()["wallet"]["sunk"] == 100, "Treasury sink was not accounted for")
    _expect(not w.dispatch("nightdrive", "turbo", "nightdrive:again:turbo")["ok"],
            "Double-purchase accepted")
    _expect(not w.dispatch("unknown_game", "delivery", "unknown:bad")["ok"],
            "Unregistered game was able to mint tau")
    _expect(not w.dispatch("civitas99", "advance", "nightdrive:buy:turbo")["ok"],
            "Reused idempotency key with changed command accepted")
    _expect(w.snapshot()["wallet"]["issued"] ==
            w.snapshot()["wallet"]["balance"] + w.snapshot()["wallet"]["sunk"],
            "Conservation identity violated")

    var replay: Node = load("res://vos/tcl_wallet.gd").new()
    replay.reset_session()
    _expect(replay._read_save(), "Save replay failed")
    _expect(replay.snapshot()["wallet"] == w.snapshot()["wallet"],
            "Replayed wallet not identical")
    _expect(replay.snapshot()["hash"] == w.snapshot()["hash"], "Replay hash mismatch")

    var file := FileAccess.open("user://tcl_vos_wallet_v1.json", FileAccess.READ)
    var saved: String = file.get_as_text()
    file.close()
    var tampered: String = saved.replace('"sunk":100', '"sunk":1')
    _expect(tampered != saved, "Test cannot locate intentional tamper")
    file = FileAccess.open("user://tcl_vos_wallet_v1.json", FileAccess.WRITE)
    file.store_string(tampered)
    file.close()
    var invalid: Node = load("res://vos/tcl_wallet.gd").new()
    invalid.reset_session()
    _expect(not invalid._read_save(), "Tampered journal was accepted")
    file = FileAccess.open("user://tcl_vos_wallet_v1.json", FileAccess.WRITE)
    file.store_string(saved)
    file.close()

    if not errors.is_empty():
        print("TCL VOS WALLET FAIL: ", errors)
        quit(1)
        return
    print("TCL VOS WALLET PASS: cross-game credits, replay, conservation, idempotency, tamper")
    quit(0)
