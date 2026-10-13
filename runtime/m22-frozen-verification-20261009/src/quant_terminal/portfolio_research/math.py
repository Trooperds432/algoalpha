"""Bounded Decimal linear algebra for deterministic portfolio research.

All public research calculations use a local 34-digit context. Inputs remain
exact Decimals; outputs of division and square roots are rounded to that context.
"""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.core.exceptions import DomainError

DECIMAL_PRECISION = 34
MAX_DIMENSION = 64
Vector = tuple[Decimal, ...]
Matrix = tuple[Vector, ...]
ZERO = Decimal(0)
ONE = Decimal(1)


def decimal_value(value: Decimal, name: str = "value") -> Decimal:
    """Reject floating-point, nonfinite and excessive numeric inputs."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{name} must be a finite Decimal")
    parts = value.as_tuple()
    if len(parts.digits) > DECIMAL_PRECISION or not -100 <= int(parts.exponent) <= 100:
        raise DomainError(f"{name} exceeds the supported Decimal bounds")
    return value


def vector(values: Vector, name: str = "vector", size: int | None = None) -> Vector:
    if not isinstance(values, tuple) or not 1 <= len(values) <= MAX_DIMENSION:
        raise DomainError(f"{name} must be a nonempty bounded tuple")
    if size is not None and len(values) != size:
        raise DomainError(f"{name} dimension mismatch")
    for value in values:
        decimal_value(value, name)
    return values


def matrix(values: Matrix, name: str = "matrix", square: bool = False) -> Matrix:
    if not isinstance(values, tuple) or not 1 <= len(values) <= MAX_DIMENSION:
        raise DomainError(f"{name} must be a nonempty bounded tuple")
    width = len(vector(values[0], name))
    for row in values:
        vector(row, name, width)
    if square and width != len(values):
        raise DomainError(f"{name} must be square")
    return values


def dot(left: Vector, right: Vector) -> Decimal:
    if len(left) != len(right):
        raise DomainError("dot product dimension mismatch")
    return sum((a * b for a, b in zip(left, right, strict=True)), ZERO)


def matvec(values: Matrix, weights: Vector) -> Vector:
    return tuple(dot(row, weights) for row in values)


def transpose(values: Matrix) -> Matrix:
    return tuple(tuple(row[i] for row in values) for i in range(len(values[0])))


def quadratic(weights: Vector, values: Matrix) -> Decimal:
    return dot(weights, matvec(values, weights))


def cholesky(values: Matrix, *, check_bounds: bool = True) -> Matrix:
    """Factor symmetric PD matrices, rejecting PSD and unresolved pivots.

    Pivots within 1e-30 of their original diagonal are unsupported at the
    34-digit working precision. This prevents rounded square roots from
    silently accepting singular covariance matrices.
    """
    if check_bounds:
        matrix(values, square=True)
    elif (
        not 1 <= len(values) <= MAX_DIMENSION
        or any(len(row) != len(values) for row in values)
        or any(not value.is_finite() for row in values for value in row)
    ):
        raise DomainError("internal matrix must be square, finite and bounded")
    count = len(values)
    if any(values[i][j] != values[j][i] for i in range(count) for j in range(i)):
        raise DomainError("matrix must be symmetric")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        result = [[ZERO] * count for _ in range(count)]
        for i in range(count):
            for j in range(i + 1):
                residual = values[i][j] - sum((result[i][k] * result[j][k] for k in range(j)), ZERO)
                if i == j:
                    if residual <= ZERO or residual <= abs(values[i][i]) * Decimal("1e-30"):
                        raise DomainError("matrix must be positive definite with resolved pivots")
                    result[i][j] = residual.sqrt()
                else:
                    result[i][j] = residual / result[j][j]
        return tuple(tuple(row) for row in result)


def solve(values: Matrix, rhs: Vector, *, check_bounds: bool = True) -> Vector:
    """Solve a symmetric PD linear system with Cholesky, without inversion."""
    if check_bounds:
        vector(rhs, size=len(values))
    elif len(rhs) != len(values) or any(not value.is_finite() for value in rhs):
        raise DomainError("internal right hand side must be finite with matching dimension")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        lower = cholesky(values, check_bounds=check_bounds)
        count = len(values)
        intermediate: list[Decimal] = []
        for i in range(count):
            intermediate.append(
                (rhs[i] - sum((lower[i][k] * intermediate[k] for k in range(i)), ZERO))
                / lower[i][i]
            )
        result = [ZERO] * count
        for i in reversed(range(count)):
            result[i] = (
                intermediate[i] - sum((lower[k][i] * result[k] for k in range(i + 1, count)), ZERO)
            ) / lower[i][i]
        return tuple(result)
