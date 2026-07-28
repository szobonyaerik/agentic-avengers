# When to Mock

Mock at **system boundaries** only:
- External APIs (payment, email, etc.)
- Databases (sometimes — prefer a test DB)
- Time/randomness
- File system (sometimes)

Don't mock:
- Your own classes/modules
- Internal collaborators
- Anything you control

Mocking an internal collaborator produces an *implementation-coupled* test (see the anti-patterns in
`SKILL.md`): it breaks on refactor even when behavior is unchanged.

## Designing for Mockability

At system boundaries, design interfaces that are easy to mock. These principles are language-agnostic;
the syntax below is illustrative.

**1. Use dependency injection.** Pass external dependencies in rather than creating them internally:

```
// Easy to mock
function processPayment(order, paymentClient):
    return paymentClient.charge(order.total)

// Hard to mock
function processPayment(order):
    client = new StripeClient(env.STRIPE_KEY)
    return client.charge(order.total)
```

**2. Prefer SDK-style interfaces over generic fetchers.** Create a specific function per external
operation instead of one generic function with conditional logic:

```
// GOOD: each function is independently mockable
api = {
    getUser:     (id)     -> fetch("/users/" + id),
    getOrders:   (userId) -> fetch("/users/" + userId + "/orders"),
    createOrder: (data)   -> fetch("/orders", { method: "POST", body: data }),
}

// BAD: mocking requires conditional logic inside the mock
api = {
    fetch: (endpoint, options) -> fetch(endpoint, options),
}
```

The SDK approach means:
- Each mock returns one specific shape
- No conditional logic in test setup
- Easier to see which endpoints a test exercises
- Type safety per endpoint (in typed languages)

---
*Adapted for the agentic-avengers pipeline from Matt Pocock's TDD skill
(github.com/mattpocock/skills, `skills/engineering/tdd`).*
