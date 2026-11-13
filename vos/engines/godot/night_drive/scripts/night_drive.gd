extends Node3D
## Godot 4 compatibility-renderer pilot for vOS's existing Night Drive game.
##
## Renderer and input adapter only; NO local wallet, mint, blockchain or
## financial account. The host owns reward logic and all world transitions.
## In preview mode, movement is cosmetic and never awards credits.

signal host_command_requested(game_id: String, action: String, argument: int, request_id: String)

const CITY_SIZE: int = 24
const CELL_SIZE: float = 2.25
const GOAL: Vector2i = Vector2i(12, 7)

var host_dispatch: Callable = Callable()
var player_cell: Vector2i = Vector2i(2, 2)
var credits: int = -1
var moves: int = 0
var car: Node3D
var goal_marker: Node3D
var hud: Label
var camera: Camera3D


func is_road(x: int, y: int) -> bool:
	return x >= 0 and y >= 0 and x < CITY_SIZE and y < CITY_SIZE and (x % 5 == 2 or y % 5 == 2)


func attach_host(callback: Callable) -> void:
	## callback(game, action, argument, request_id) -> {"ok", "state"}.
	## Supports future Android, desktop, WASM or native host bindings.
	host_dispatch = callback
	_refresh_hud("Host connected. All game credits remain host-owned.")


func update_from_host(reply: Dictionary) -> void:
	if not reply.get("ok", false):
		_refresh_hud("Host rejected the action.")
		return
	var state: Dictionary = reply.get("state", {})
	if not state.has("nightdrive") or not state.has("wallet"):
		_refresh_hud("Host state incomplete.")
		return
	var drive: Dictionary = state["nightdrive"]
	var wallet: Dictionary = state["wallet"]
	var x: int = int(drive.get("x", 2))
	var y: int = int(drive.get("y", 2))
	if not is_road(x, y):
		_refresh_hud("Invalid host road position.")
		return
	var amount: int = int(wallet.get("credits", -1))
	if amount < 0:
		_refresh_hud("Invalid universal wallet amount.")
		return
	player_cell = Vector2i(x, y)
	credits = amount
	_update_vehicle()
	_refresh_hud("Host-authoritative state synchronized.")


func _ready() -> void:
	_create_city()
	_create_car()
	_create_goal()
	_create_camera_and_lighting()
	_create_hud()
	_update_vehicle()
	_refresh_hud("Preview mode: no host wallet connected.")


func _unhandled_key_input(event: InputEvent) -> void:
	if not event is InputEventKey:
		return
	if not event.pressed or event.echo:
		return
	var direction: int = -1
	match event.keycode:
		KEY_UP, KEY_W:
			direction = 0
		KEY_RIGHT, KEY_D:
			direction = 1
		KEY_DOWN, KEY_S:
			direction = 2
		KEY_LEFT, KEY_A:
			direction = 3
	if direction >= 0:
		get_viewport().set_input_as_handled()
		request_move(direction)


func request_move(direction: int) -> void:
	if direction < 0 or direction > 3:
		return
	var vectors: Array[Vector2i] = [
		Vector2i(0, -1), Vector2i(1, 0),
		Vector2i(0, 1), Vector2i(-1, 0)
	]
	var next: Vector2i = player_cell + vectors[direction]
	if not is_road(next.x, next.y):
		_refresh_hud("Building ahead — stay on the roads.")
		return
	moves += 1
	var request_id: String = "godot_%012d" % moves
	if host_dispatch.is_null():
		# Cosmetic preview only. The real wallet and game events are absent.
		player_cell = next
		_update_vehicle()
		_refresh_hud("Offline renderer preview. No credits earned.")
		return
	# Always request an action from the authoritative vOS host.
	host_command_requested.emit("nightdrive", "move", direction, request_id)
	var response: Variant = host_dispatch.call("nightdrive", "move", direction, request_id)
	if response is Dictionary:
		update_from_host(response)
	else:
		_refresh_hud("Host transport failed.")


func _position(cell: Vector2i, height: float = 0.0) -> Vector3:
	return Vector3(float(cell.x) * CELL_SIZE, height, float(cell.y) * CELL_SIZE)


func _material(color: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 1.0
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	return mat


func _batch(cells: Array[Vector3], size: Vector3, color: Color, name_: String) -> void:
	if cells.is_empty():
		return
	var box := BoxMesh.new()
	box.size = size
	var multi := MultiMesh.new()
	multi.transform_format = MultiMesh.TRANSFORM_3D
	multi.mesh = box
	multi.instance_count = cells.size()
	for idx in cells.size():
		multi.set_instance_transform(idx, Transform3D(Basis(), cells[idx]))
	var node := MultiMeshInstance3D.new()
	node.name = name_
	node.multimesh = multi
	node.material_override = _material(color)
	add_child(node)


func _create_city() -> void:
	var roads: Array[Vector3] = []
	var blocks: Array[Vector3] = []
	var roofs: Array[Vector3] = []
	var lights: Array[Vector3] = []
	for y in CITY_SIZE:
		for x in CITY_SIZE:
			var cell := Vector2i(x, y)
			var pos := _position(cell)
			if is_road(x, y):
				roads.append(pos + Vector3(0, -0.12, 0))
				if (x + y) % 8 == 0:
					lights.append(pos + Vector3(0, 0.03, 0))
			else:
				var h: float = 1.0 + float((x * 11 + y * 17) % 4) * 0.5
				blocks.append(pos + Vector3(0, h / 2.0, 0))
				roofs.append(pos + Vector3(0, h, 0))
	_batch(roads, Vector3(CELL_SIZE * 0.98, 0.09, CELL_SIZE * 0.98),
		Color(0.14, 0.19, 0.28), "Roads")
	_batch(blocks, Vector3(CELL_SIZE * 0.85, 1.0, CELL_SIZE * 0.85),
		Color(0.20, 0.26, 0.40), "Buildings")
	_batch(roofs, Vector3(CELL_SIZE * 0.65, 0.05, CELL_SIZE * 0.65),
		Color(0.35, 0.22, 0.48), "Rooftops")
	_batch(lights, Vector3(0.12, 0.04, 0.12),
		Color(0.9, 0.84, 0.25), "RoadMarkers")


func _create_car() -> void:
	car = Node3D.new()
	car.name = "PlayerVehicle"
	add_child(car)
	var body := MeshInstance3D.new()
	body.name = "VehicleBody"
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.9, 0.48, 1.45)
	body.mesh = mesh
	body.material_override = _material(Color(0.25, 0.96, 0.79))
	body.position.y = 0.36
	car.add_child(body)
	var windshield := MeshInstance3D.new()
	var top := BoxMesh.new()
	top.size = Vector3(0.7, 0.1, 0.65)
	windshield.mesh = top
	windshield.material_override = _material(Color(0.06, 0.11, 0.2))
	windshield.position.y = 0.67
	car.add_child(windshield)


func _create_goal() -> void:
	goal_marker = Node3D.new()
	goal_marker.name = "CourierGoal"
	add_child(goal_marker)
	goal_marker.position = _position(GOAL, 1.15)
	var flag := MeshInstance3D.new()
	var cube := BoxMesh.new()
	cube.size = Vector3(0.6, 2.4, 0.6)
	flag.mesh = cube
	flag.material_override = _material(Color(0.98, 0.77, 0.11))
	goal_marker.add_child(flag)


func _create_camera_and_lighting() -> void:
	camera = Camera3D.new()
	camera.name = "ArcadeCamera"
	camera.current = true
	camera.fov = 49.0
	camera.position = Vector3(21, 37, 48)
	add_child(camera)
	camera.look_at(Vector3(26, 0, 26), Vector3.UP)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, 28, -12)
	sun.light_energy = 0.8
	add_child(sun)


func _create_hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	hud = Label.new()
	hud.name = "GameStatus"
	hud.position = Vector2(20, 20)
	hud.add_theme_color_override("font_color", Color(0.7, 1.0, 0.9))
	hud.add_theme_font_size_override("font_size", 20)
	layer.add_child(hud)


func _update_vehicle() -> void:
	car.position = _position(player_cell, 0.0)


func _refresh_hud(info: String) -> void:
	if hud == null:
		return
	var wallet_text: String = "%d τ" % credits if credits >= 0 else "HOST WALLET DISCONNECTED"
	hud.text = "TCL vOS · NIGHT DRIVE 3D\n%s\nCell %d, %d · Goal %d, %d\n%s\nArrows/WASD to drive" % [
		wallet_text, player_cell.x, player_cell.y, GOAL.x, GOAL.y, info
	]
