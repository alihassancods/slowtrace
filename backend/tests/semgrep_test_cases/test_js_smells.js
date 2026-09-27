// Semgrep rule fixtures — JavaScript / TypeScript smells.
//
// This file contains **intentionally bad code**. It is never executed; Semgrep
// scans it as text and `semgrep --test` compares reported lines against the
// annotations. Undefined names (`db`, `prisma`, `users`) are fine.
//
// Run it with:
//
//   backend/.venv/bin/semgrep --test --metrics=off \
//       --config backend/src/agent/codebase/rules/slowtrace-rules.yml \
//       backend/tests/semgrep_test_cases/test_js_smells.js
//
// Annotations bind to the immediately following line, and for the loop rules
// that line is the `await ...` statement inside the loop body (not the `for`
// header and not the `async function` line).

// --------------------------------------------------------------------------
// javascript-n-plus-one-await-in-loop — for...of form
// --------------------------------------------------------------------------
async function getBadOrders(orderIds) {
  for (const id of orderIds) {
    // ruleid: javascript-n-plus-one-await-in-loop
    const order = await db.query('SELECT * FROM orders WHERE id = $1', [id]);
    console.log(order);
  }
}

// --------------------------------------------------------------------------
// javascript-n-plus-one-await-in-loop — classic C-style form
// --------------------------------------------------------------------------
async function getBadOrdersIndexed(ids) {
  for (let i = 0; i < ids.length; i++) {
    // ruleid: javascript-n-plus-one-await-in-loop
    const row = await db.user.findUnique({ where: { id: ids[i] } });
    console.log(row);
  }
}

// --------------------------------------------------------------------------
// javascript-n-plus-one-map-async
// --------------------------------------------------------------------------
async function getBadUsers(users) {
  return users.map(async (user) => {
    // ruleid: javascript-n-plus-one-map-async
    return await prisma.profile.findFirst({ where: { userId: user.id } });
  });
}

// --------------------------------------------------------------------------
// prisma-unbounded-findmany — no argument, and empty object argument
// --------------------------------------------------------------------------
async function getAllUsers() {
  // ruleid: prisma-unbounded-findmany
  return await prisma.user.findMany();
}

async function getAllPosts() {
  // ruleid: prisma-unbounded-findmany
  return await prisma.post.findMany({});
}

// --------------------------------------------------------------------------
// javascript-sql-template-literal — SELECT and DELETE branches
// --------------------------------------------------------------------------
async function unsafeQuery(userId) {
  // ruleid: javascript-sql-template-literal
  return await db.query(`SELECT * FROM users WHERE id = ${userId}`);
}

async function unsafeDelete(userId) {
  // ruleid: javascript-sql-template-literal
  return await db.query(`DELETE FROM sessions WHERE user_id = ${userId}`);
}

// ==========================================================================
// Negative cases — none of the below may be reported.
// ==========================================================================

async function goodOrderFetch(orderIds) {
  // ok: javascript-n-plus-one-await-in-loop, javascript-sql-template-literal
  return await db.query('SELECT * FROM orders WHERE id = ANY($1)', [orderIds]);
}

async function goodBatchFetch(ids) {
  // ok: javascript-n-plus-one-map-async, javascript-n-plus-one-await-in-loop
  return await Promise.all(ids.map((id) => db.user.count({ where: { id } })));
}

async function goodPaginatedUsers(page) {
  // ok: prisma-unbounded-findmany
  return await prisma.user.findMany({ take: 25, skip: page * 25 });
}

async function goodParameterized(userId) {
  // ok: javascript-sql-template-literal
  return await db.query('SELECT * FROM users WHERE id = $1', [userId]);
}
