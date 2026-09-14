# addons/ue2godot/core/transform.gd
class_name TransformConverter
extends RefCounted

const UE_CM_TO_GODOT_M := 0.01

static func unreal_rotator_to_basis(pitch: float, yaw: float, roll: float) -> Basis:
	var p := deg_to_rad(pitch)
	var y := deg_to_rad(yaw)
	var r := deg_to_rad(roll)

	var cp := cos(p * 0.5)
	var sp := sin(p * 0.5)
	var cy := cos(y * 0.5)
	var sy := sin(y * 0.5)
	var cr := cos(r * 0.5)
	var sr := sin(r * 0.5)

	var qx := cr * sp * sy - sr * cp * cy
	var qy := -cr * sp * cy - sr * cp * sy  # V10.7 fix: negative qy
	var qz := cr * cp * sy - sr * sp * cy
	var qw := cr * cp * cy + sr * sp * sy

	return Basis(Quaternion(qx, qy, qz, qw))

static func convert_basis_ue_to_godot(ue_basis: Basis) -> Basis:
	# C matrix: Gx=Ux, Gy=Uz, Gz=Uy (det -1)
	var c := Basis(
		Vector3(1, 0, 0),
		Vector3(0, 0, 1),
		Vector3(0, 1, 0)
	)
	return c * ue_basis * c.inverse()

static func transform_from_v10(data: Dictionary) -> Transform3D:
	var loc: Array = data.get("location", [0.0, 0.0, 0.0])
	var rot: Dictionary = data.get("rotation", {"pitch": 0.0, "yaw": 0.0, "roll": 0.0})
	var scl: Array = data.get("scale", [1.0, 1.0, 1.0])

	var ue_pos := Vector3(float(loc[0]), float(loc[1]), float(loc[2]))
	var ue_rot := Vector3(float(rot.get("pitch", 0.0)), float(rot.get("yaw", 0.0)), float(rot.get("roll", 0.0)))
	var ue_scl := Vector3(float(scl[0]), float(scl[1]), float(scl[2]))

	var ue_basis := unreal_rotator_to_basis(ue_rot.x, ue_rot.y, ue_rot.z)
	var converted_basis := convert_basis_ue_to_godot(ue_basis)
	converted_basis = converted_basis.scaled(ue_scl)

	var godot_pos := Vector3(ue_pos.x, ue_pos.z, ue_pos.y) * UE_CM_TO_GODOT_M
	return Transform3D(converted_basis, godot_pos)
