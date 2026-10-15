"""Shared surface alignment for placement previews and imported assets."""

from __future__ import annotations

import math

Vector3 = tuple[float, float, float]
Basis = tuple[Vector3, Vector3, Vector3]


def surface_basis(normal: Vector3, rotation_z: float) -> Basis:
    """Swing local Z onto the normal, then spin around that normal."""
    length = math.hypot(*normal)
    if not math.isfinite(length) or length == 0.0:
        raise ValueError(f"Invalid placement surface normal: {normal!r}")
    nx, ny, nz = (component / length for component in normal)
    sine = math.hypot(nx, ny)
    if sine == 0.0:
        x_axis = (1.0, 0.0, 0.0)
        y_axis = (0.0, nz, 0.0)
    else:
        ax, ay = -ny / sine, nx / sine
        t = 1.0 - nz
        x_axis = (nz + ax * ax * t, ax * ay * t, -ay * sine)
        y_axis = (ax * ay * t, nz + ay * ay * t, ax * sine)
    angle = math.radians(rotation_z)
    c, s = math.cos(angle), math.sin(angle)
    return (
        (c * x_axis[0] + s * y_axis[0], c * x_axis[1] + s * y_axis[1], c * x_axis[2] + s * y_axis[2]),
        (-s * x_axis[0] + c * y_axis[0], -s * x_axis[1] + c * y_axis[1], -s * x_axis[2] + c * y_axis[2]),
        (nx, ny, nz),
    )


def rotate_vector(point: Vector3, basis: Basis) -> Vector3:
    """Rotate a local vector using the world-space axes of a placement."""
    x, y, z = point
    a, b, c = basis
    return (
        x * a[0] + y * b[0] + z * c[0],
        x * a[1] + y * b[1] + z * c[1],
        x * a[2] + y * b[2] + z * c[2],
    )
