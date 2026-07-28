# Good and Bad Tests

Language-agnostic examples. The pseudocode uses a TypeScript-ish syntax for brevity — apply the same
principles in the project's stack (Python `pytest`, Java `JUnit`, C++ `GoogleTest`, …).

## Good Tests

**Integration-style**: test through real interfaces, not mocks of internal parts.

```
// GOOD: tests observable behavior
test("user can checkout with valid cart"):
    cart = createCart()
    cart.add(product)
    result = checkout(cart, paymentMethod)
    assert result.status == "confirmed"
```

Characteristics:
- Tests behavior users/callers care about
- Uses the public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

## Bad Tests

**Implementation-detail tests**: coupled to internal structure.

```
// BAD: tests implementation details
test("checkout calls paymentService.process"):
    mockPayment = mock(paymentService)
    checkout(cart, payment)
    assert mockPayment.process.calledWith(cart.total)
```

Red flags:
- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Verifying through external means instead of the interface

```
// BAD: bypasses the interface to verify
test("createUser saves to database"):
    createUser({ name: "Alice" })
    row = db.query("SELECT * FROM users WHERE name = ?", ["Alice"])
    assert row is not None

// GOOD: verifies through the interface
test("createUser makes user retrievable"):
    user = createUser({ name: "Alice" })
    retrieved = getUser(user.id)
    assert retrieved.name == "Alice"
```

**Tautological tests**: the expected value restates the implementation, so the test passes by
construction.

```
// BAD: expected value is recomputed the way the code computes it
test("calculateTotal sums line items"):
    items = [{ price: 10 }, { price: 5 }]
    expected = sum(i.price for i in items)
    assert calculateTotal(items) == expected

// GOOD: expected value is an independent, known literal
test("calculateTotal sums line items"):
    assert calculateTotal([{ price: 10 }, { price: 5 }]) == 15
```

---
*Adapted for the agentic-avengers pipeline from Matt Pocock's TDD skill
(github.com/mattpocock/skills, `skills/engineering/tdd`).*
