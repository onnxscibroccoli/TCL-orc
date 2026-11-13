extends CanvasLayer
## Non-invasive TCL vOS overlay for the MIT-licensed OpenCity Godot scene.
## Reuses vehicle, on-foot physics, traffic, pedestrians, procedural city.
## Shared tau wallet lives in a single autoload, not in any individual game.

var _world: Node3D
var _summary: Label
var _mission: Label
var _message: Label
var _buttons: Dictionary = {}
var _goal: Vector3 = Vector3(-30.0, 1.0, -45.0)
var _goal_visual: Node3D
var _last_pos: Vector3 = Vector3.ZERO
var _driven: float = 0.0
var _mission_active := false
var _mission_claimed := false


func _ready() -> void:
    _world = get_parent() as Node3D
    _build_overlay()
    TCLWallet.wallet_changed.connect(_update_view)
    _update_view(TCLWallet.snapshot())
    if DisplayServer.is_touchscreen_available():
        Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
    _create_delivery_marker()


func _build_overlay() -> void:
    var background := PanelContainer.new()
    background.set_anchors_preset(Control.PRESET_TOP_RIGHT)
    background.position = Vector2(0, 12)
    background.custom_minimum_size = Vector2(360, 100)
    add_child(background)
    var column := VBoxContainer.new()
    background.add_child(column)
    var title := Label.new()
    title.text = "TCL vOS  |  OPEN CITY"
    title.add_theme_color_override("font_color", Color(0.55, 0.96, 0.83))
    column.add_child(title)
    _summary = Label.new()
    column.add_child(_summary)
    _mission = Label.new()
    _mission.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    column.add_child(_mission)
    _message = Label.new()
    _message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    column.add_child(_message)
    var actions := HBoxContainer.new()
    column.add_child(actions)
    for entry in [
        ["Civic shift +30 τ", "civitas_work"],
        ["Next turn", "civitas_advance"],
        ["Night Drive turbo 100 τ", "nightdrive_turbo"],
    ]:
        var btn := Button.new()
        btn.text = entry[0]
        btn.custom_minimum_size = Vector2(105, 48)
        actions.add_child(btn)
        btn.pressed.connect(_command.bind(entry[1]))
        _buttons[entry[1]] = btn
    var mission_buttons := HBoxContainer.new()
    column.add_child(mission_buttons)
    var start := Button.new()
    start.text = "Start courier delivery"
    start.custom_minimum_size = Vector2(170, 48)
    mission_buttons.add_child(start)
    start.pressed.connect(_begin_delivery)
    _buttons["delivery"] = start
    var enter := Button.new()
    enter.text = "Enter / Exit vehicle"
    enter.custom_minimum_size = Vector2(145, 48)
    mission_buttons.add_child(enter)
    enter.pressed.connect(_toggle_vehicle)
    var controls := HBoxContainer.new()
    controls.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
    controls.position = Vector2(20, -115)
    add_child(controls)
    for pair in [["◀", "vos_left"], ["▲", "vos_forward"], ["▼", "vos_back"], ["▶", "vos_right"],
                 ["BOOST", "vos_boost"], ["BRAKE", "vos_brake"]]:
        var action: String = pair[1]
        if not InputMap.has_action(action):
            InputMap.add_action(action)
        var key := Button.new()
        key.text = pair[0]
        key.custom_minimum_size = Vector2(66, 70)
        key.focus_mode = Control.FOCUS_NONE
        controls.add_child(key)
        key.button_down.connect(_touch_down.bind(action))
        key.button_up.connect(_touch_up.bind(action))


func _touch_down(action: String) -> void:
    Input.action_press(action)


func _touch_up(action: String) -> void:
    Input.action_release(action)


func _toggle_vehicle() -> void:
    if _world.driving == null:
        _world.enter_nearest_vehicle()
    else:
        _world.exit_vehicle()


func _command(action: String) -> void:
    var game := "civitas99"
    var command := "work"
    var key := ""
    var state: Dictionary = TCLWallet.snapshot()["wallet"]
    match action:
        "civitas_work":
            key = "civitas:work:%d" % state["turn"]
        "civitas_advance":
            command = "advance"
            key = "civitas:advance:%d" % state["turn"]
        "nightdrive_turbo":
            game = "nightdrive"
            command = "turbo"
            key = "nightdrive:turbo:purchase"
        _:
            return
    var response: Dictionary = TCLWallet.dispatch(game, command, key)
    _message.text = "Purchase/work committed" if response["ok"] else str(response["error"])


func _begin_delivery() -> void:
    if _world.driving == null:
        _message.text = "Enter a car, then start your courier mission."
        return
    var car := _world.driving as Node3D
    _goal = car.global_position + Vector3(-40.0, 0.0, -60.0)
    _last_pos = car.global_position
    _driven = 0.0
    _mission_active = true
    _mission_claimed = false
    _message.text = "Drive 35 metres and reach the glowing drop-off."
    _goal_visual.global_position = _goal


func _create_delivery_marker() -> void:
    _goal_visual = Node3D.new()
    _goal_visual.name = "TCLDropOffMarker"
    _world.add_child.call_deferred(_goal_visual)
    var marker := MeshInstance3D.new()
    var mesh := CylinderMesh.new()
    mesh.top_radius = 2.2
    mesh.bottom_radius = 2.2
    mesh.height = 0.25
    marker.mesh = mesh
    var surface := StandardMaterial3D.new()
    surface.albedo_color = Color(0.1, 0.93, 0.72, 0.75)
    surface.emission_enabled = true
    surface.emission = Color(0.1, 0.9, 0.65)
    surface.emission_energy_multiplier = 2.0
    marker.material_override = surface
    _goal_visual.add_child(marker)
    _goal_visual.visible = false


func _physics_process(_delta: float) -> void:
    if not _mission_active or _mission_claimed or _world.driving == null:
        return
    var position: Vector3 = _world.driving.global_position
    var movement := position.distance_to(_last_pos)
    _last_pos = position
    if movement < 12.0:
        _driven += movement
    _mission.text = "Courier | %dm travelled | %dm to drop-off" % [
        int(_driven), int(position.distance_to(_goal))
    ]
    if _driven < 35.0 or position.distance_to(_goal) > 10.0:
        return
    var completed: int = TCLWallet.snapshot()["wallet"]["deliveries"] + 1
    var response: Dictionary = TCLWallet.dispatch(
        "opencity", "delivery", "opencity:delivery:%d" % completed
    )
    if response["ok"]:
        _mission_claimed = true
        _mission_active = false
        _goal_visual.visible = false
        _message.text = "Delivery complete: +45 universal TCL vOS τ!"
    else:
        _message.text = str(response["error"])


func _update_view(view: Dictionary) -> void:
    var state: Dictionary = view["wallet"]
    _summary.text = "%s τ · issued %s · sunk %s · events %d" % [
        state["balance"], state["issued"], state["sunk"], view["events"]
    ]
    _buttons["civitas_work"].disabled = state["worked_turn"] == state["turn"]
    _buttons["nightdrive_turbo"].disabled = state["turbo"] or state["balance"] < 100
    _buttons["civitas_advance"].disabled = state["turn"] >= 500
    if not _mission_active:
        _mission.text = "City turn %s · courier deliveries %s · saved locally" % [
            state["turn"], state["deliveries"]
        ]
