extends SceneTree
## Headless test: renderer uses host-owned τ, never its own currency ledger.

func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var stage: PackedScene = load("res://scenes/night_drive.tscn")
	assert(stage != null, "Night Drive scene must load")
	var renderer: Node = stage.instantiate()
	root.add_child(renderer)
	await process_frame
	assert(renderer.is_road(2, 2))
	assert(renderer.is_road(12, 7))
	assert(not renderer.is_road(0, 0))
	assert(renderer.credits == -1)
	assert(renderer.player_cell == Vector2i(2, 2))
	assert(renderer.car != null)
	assert(renderer.camera != null)

	# Unconnected renderer previews navigation only; cannot mint τ.
	renderer.request_move(1)
	assert(renderer.player_cell == Vector2i(3, 2))
	assert(renderer.credits == -1)

	# A host port can be supplied by any platform or engine adapter.
	var port: Callable = func(game: String, action: String, argument: int, request_id: String) -> Dictionary:
		assert(game == "nightdrive")
		assert(action == "move")
		assert(argument == 1)
		assert(request_id.begins_with("godot_"))
		return {"ok": true, "state": {
			"nightdrive": {"x": 4, "y": 2},
			"wallet": {"credits": 484}
		}}
	renderer.attach_host(port)
	renderer.request_move(1)
	assert(renderer.player_cell == Vector2i(4, 2))
	assert(renderer.credits == 484)

	# A rejected host request cannot mutate renderer currency.
	var reject: Callable = func(_game: String, _action: String, _arg: int, _id: String) -> Dictionary:
		return {"ok": false, "error": "rejected"}
	renderer.attach_host(reject)
	renderer.request_move(1)
	assert(renderer.credits == 484)
	assert(renderer.player_cell == Vector2i(4, 2))

	print("TCL vOS Godot renderer host-ABI smoke PASS")
	quit(0)
