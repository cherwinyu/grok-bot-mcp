extends RefCounted

func orthogonal_distance(start_cell: Vector2i, goal_cell: Vector2i) -> int:
	return absi(start_cell.x - goal_cell.x) + absi(start_cell.y - goal_cell.y)
