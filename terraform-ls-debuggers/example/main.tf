locals {
  # Top-level calls: hover works for these.
  a = concat([1, 2], [3, 4])
  b = merge({ x = 1 }, { y = 2 })

  # Same functions, but as the value of an attribute inside an
  # object-constructor expression: hover returns null for these.
  s = {
    Statement = concat(
      [1, 2],
      [3, 4],
    )
  }
  t = {
    Statement = merge(
      { x = 1 },
      { y = 2 },
    )
  }
}
